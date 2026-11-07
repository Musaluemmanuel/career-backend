import argparse
import json
import logging
import os
import re
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import truststore
import requests
from bs4 import BeautifulSoup


truststore.inject_into_ssl()


BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
REGISTRY_FILE = DATA_DIR / "course_urls.json"
INVENTORY_FILE = DATA_DIR / "programme_pages.json"
SCHOOL_DOMAINS_FILE = DATA_DIR / "school_domains.json"
USER_AGENT = "Mozilla/5.0 (compatible; CareerProgrammeInventory/1.0)"
NAVIGATION_TERMS = re.compile(
    r"academic|admission|course|degree|department|faculty|programme|program|school|study",
    re.IGNORECASE
)
COURSE_TERMS = re.compile(
    r"course|degree|programme|program|bachelor|master|diploma|undergraduate",
    re.IGNORECASE
)
PROGRAM_TITLE_PATTERN = re.compile(
    r"\b(?:bachelor(?:'s)?|master(?:'s)?|doctor(?:ate)?|ph\.?d|diploma|"
    r"certificate|degree)\b",
    re.IGNORECASE
)
EXCLUDED_PATH = re.compile(
    r"(?:^|[/_-])(?:blog|news|events?|login|portal|staff|linkages|partnerships?|"
    r"procurement|tenders?|vacanc(?:y|ies)|jobs?|downloads?|resources|"
    r"time[_-]?tables?|exam(?:inations?)?|calendar)(?:$|[/_.?-])",
    re.IGNORECASE
)
DETAIL_TERMS = re.compile(
    r"\b(?:admission|entry requirements?|duration|curriculum|course structure|"
    r"programme structure|program structure|programme overview|program overview|"
    r"learning outcomes|career opportunities|study mode|minimum grade|kcse)\b",
    re.IGNORECASE
)
COURSE_MATCH_STOPWORDS = {
    "and", "ba", "bachelor", "bsc", "bscn", "bsn", "certificate",
    "degree", "diploma", "doctor", "in", "llb", "ma", "master",
    "msc", "of", "phd", "the", "with",
}
COURSE_LEVEL_TOKENS = {
    "bachelor", "certificate", "doctor", "diploma", "higher", "master", "phd",
}
NON_HTML_SUFFIXES = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif", ".zip", ".xml", ".gz"
)


def _normalize(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def _same_domain(url, domain):
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    expected = domain.lower().rstrip(".")
    return host == expected or host.endswith("." + expected)


def _url_key(url):
    parts = urllib.parse.urlsplit(urllib.parse.urldefrag(url)[0])
    return urllib.parse.urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path,
        parts.query,
        ""
    ))


def _load_catalogue():
    with REGISTRY_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError(f"Unsupported course catalogue format in {REGISTRY_FILE}")

    courses_by_institution = {}
    domains_by_institution = {}
    institutions_by_key = {}
    known_verified_urls = {}
    for entry in data["entries"]:
        institution = entry.get("university", "").strip()
        course = entry.get("course", "").strip()
        domain = entry.get("domain", "").strip()
        if not institution:
            continue
        key = _normalize(institution)
        institutions_by_key.setdefault(key, institution)
        if course:
            courses_by_institution.setdefault(key, set()).add(course)
        if domain:
            domains_by_institution[key] = domain
        if entry.get("verified") is True and entry.get("url"):
            known_verified_urls.setdefault(key, set()).add(_url_key(entry["url"]))

    if SCHOOL_DOMAINS_FILE.exists():
        with SCHOOL_DOMAINS_FILE.open("r", encoding="utf-8") as file:
            school_domains = json.load(file)
        if not isinstance(school_domains, dict):
            raise ValueError(f"Unsupported school-domain format in {SCHOOL_DOMAINS_FILE}")
        for key in institutions_by_key:
            domain = school_domains.get(key)
            if domain and key not in domains_by_institution:
                domains_by_institution[key] = domain.strip()
    return (
        courses_by_institution,
        domains_by_institution,
        institutions_by_key,
        known_verified_urls,
    )


def _course_matches(title, courses):
    normalized_title = _normalize(title)
    if not normalized_title:
        return []
    title_tokens = set(normalized_title.split())
    matches = []
    for course in courses:
        normalized_course = _normalize(course)
        if not normalized_course:
            continue
        course_tokens = set(normalized_course.split())
        course_levels = course_tokens & COURSE_LEVEL_TOKENS
        title_levels = title_tokens & COURSE_LEVEL_TOKENS
        if course_levels and not course_levels.issubset(title_levels):
            continue
        if normalized_course == normalized_title:
            matches.append(course)
            continue
        meaningful = {
            token for token in course_tokens
            if len(token) > 2 and token not in COURSE_MATCH_STOPWORDS
        }
        title_meaningful = {
            token for token in title_tokens
            if len(token) > 2 and token not in COURSE_MATCH_STOPWORDS
        }
        if (
            len(meaningful) >= 3 and meaningful.issubset(title_meaningful)
        ) or (
            1 <= len(meaningful) < 3 and meaningful == title_meaningful
        ):
            matches.append(course)
    return sorted(matches)


def _navigation_priority(label, path, courses):
    combined = f"{label} {path}"
    if _course_matches(label, courses):
        return 0
    if PROGRAM_TITLE_PATTERN.search(label):
        return 1
    if re.search(r"course|degree|programme|program", path, re.IGNORECASE):
        return 2
    if re.search(r"school|faculty|department|academic", combined, re.IGNORECASE):
        return 3
    return 4


def _sitemap_urls(domain, session, limit):
    sitemap_queue = deque()
    try:
        robots = session.get(f"https://{domain}/robots.txt", timeout=5)
        if robots.ok and _same_domain(robots.url, domain):
            for line in robots.text.splitlines():
                match = re.match(r"(?i)^\s*sitemap:\s*(\S+)", line)
                if match:
                    sitemap_queue.append(
                        urllib.parse.urljoin(robots.url, match.group(1).strip())
                    )
    except requests.RequestException as exc:
        logging.debug("Could not inspect robots.txt for %s: %s", domain, exc)
    sitemap_queue.extend([
        f"https://{domain}/sitemap.xml",
        f"https://www.{domain}/sitemap.xml",
    ])
    sitemaps_seen = set()
    page_urls = []
    page_seen = set()
    while sitemap_queue and len(sitemaps_seen) < 8 and len(page_urls) < limit:
        sitemap_url = sitemap_queue.popleft()
        if sitemap_url in sitemaps_seen or not _same_domain(sitemap_url, domain):
            continue
        sitemaps_seen.add(sitemap_url)
        try:
            response = session.get(sitemap_url, timeout=7)
            if not response.ok or not _same_domain(response.url, domain):
                continue
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError, ValueError):
            continue
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() != "loc" or not element.text:
                continue
            candidate = urllib.parse.urldefrag(element.text.strip())[0]
            if not _same_domain(candidate, domain):
                continue
            path = urllib.parse.urlparse(candidate).path.lower()
            if path.endswith((".xml", ".xml.gz")):
                if candidate not in sitemaps_seen:
                    sitemap_queue.append(candidate)
            elif candidate not in page_seen and not EXCLUDED_PATH.search(
                urllib.parse.urlparse(candidate).path
            ):
                page_seen.add(candidate)
                page_urls.append(candidate)
                if len(page_urls) >= limit:
                    break
    page_urls.sort(key=lambda url: (
        0 if re.search(
            r"course|degree|department|faculty|programme|program|school",
            urllib.parse.urlparse(url).path,
            re.IGNORECASE
        ) else 1,
        url
    ))
    return page_urls[:limit]


def _page_record(url, html, courses, referrer, domain):
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript", "svg"]):
        element.decompose()
    title = " ".join(soup.title.stripped_strings) if soup.title else ""
    headings = [
        " ".join(tag.stripped_strings)
        for tag in soup.select("h1, h2, h3")
        if " ".join(tag.stripped_strings)
        and not tag.find_parent(["header", "nav", "footer", "aside"])
    ]
    main = soup.find("main") or soup.find("article") or soup.body or soup
    content_strings = []
    for string in main.strings:
        if not str(string).strip():
            continue
        if string.parent and string.parent.find_parent(["header", "nav", "footer", "aside"]):
            continue
        content_strings.append(str(string).strip())
    text = " ".join(content_strings)
    text = re.sub(r"\s+", " ", text).strip()
    primary_heading = headings[0] if headings else title
    normalized_path = urllib.parse.urlparse(url).path.lower()
    path_segments = [segment for segment in normalized_path.split("/") if segment]
    generic_programme_slugs = {
        "academic-programmes", "all-programmes", "our-programmes",
        "undergraduate-programmes", "postgraduate-programmes",
        "programmes", "programs", "courses", "degrees",
    }
    specific_course_slug = bool(
        path_segments
        and PROGRAM_TITLE_PATTERN.search(path_segments[-1].replace("-", " ").replace("_", " "))
        and path_segments[-1] not in generic_programme_slugs
        and re.search(
            r"/(?:course|courses|degree|degrees|programme|programmes|program|programs)/",
            normalized_path
        )
    )
    is_detail = bool(
        PROGRAM_TITLE_PATTERN.search(primary_heading)
        or _course_matches(primary_heading, courses)
        or specific_course_slug
    )
    course_name = primary_heading if is_detail else ""
    useful_details = sorted(set(
        match.group(0).lower()
        for match in DETAIL_TERMS.finditer(text)
    ))
    heading_matches = sorted({
        course
        for heading in headings
        for course in _course_matches(heading, courses)
    })
    primary_matches = _course_matches(primary_heading, courses)
    title_matches = _course_matches(title, courses)
    path_segments = [segment for segment in normalized_path.split("/") if segment]
    generic_programme_slugs = {
        "academic-programmes", "all-programmes", "our-programmes",
        "undergraduate-programmes", "postgraduate-programmes",
        "programmes", "programs", "courses", "degrees",
        "admissions", "undergraduate-admissions",
    }
    generic_index = not path_segments or path_segments[-1] in generic_programme_slugs
    verified_course_matches = (
        heading_matches
        if (
            len(heading_matches) == 1
            and bool(primary_matches or title_matches)
            and not generic_index
            and len(text) >= 100
            and useful_details
        )
        else []
    )
    course_links = []
    seen_course_links = set()
    for anchor in soup.select("a[href]"):
        label = " ".join(anchor.stripped_strings)
        href = anchor.get("href", "").strip()
        if not label or not href:
            continue
        link_url = urllib.parse.urldefrag(urllib.parse.urljoin(url, href))[0]
        parsed_link = urllib.parse.urlparse(link_url)
        if (
            parsed_link.scheme != "https"
            or not _same_domain(link_url, domain)
        ):
            continue
        path_tokens = [
            token for token in re.split(r"[/_-]+", parsed_link.path.lower())
            if token
        ]
        has_programme_route = bool(
            re.search(r"/(?:course|courses|degree|degrees|programme|programmes|program|programs)/", parsed_link.path, re.I)
            and not any(token in {"all", "index", "list", "lists"} for token in path_tokens[-1:])
        )
        if (
            not PROGRAM_TITLE_PATTERN.search(label)
            and not _course_matches(label, courses)
            and not (has_programme_route and len(label.split()) >= 1)
        ):
            continue
        if link_url in seen_course_links:
            continue
        seen_course_links.add(link_url)
        course_links.append({
            "title": label,
            "url": link_url,
            "matched_catalog_courses": _course_matches(label, courses),
            "resource_type": (
                "document"
                if parsed_link.path.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx"))
                else "html_candidate"
            ),
        })
    return {
        "url": url,
        "discovered_from": referrer,
        "title": title,
        "headings": headings[:12],
        "course_name": course_name,
        "page_type": "programme_or_course_candidate" if is_detail else "academic_navigation",
        "matched_catalog_courses": sorted({
            course
            for heading in headings
            for course in _course_matches(heading, courses)
        }),
        "verified_course_matches": verified_course_matches,
        "verification_status": (
            "verified_specific_course_page"
            if verified_course_matches
            else "discovered_unverified"
        ),
        "useful_detail_signals": useful_details[:12],
        "course_links": course_links,
        "content_excerpt": text[:1200],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }, soup


def _verify_discovered_course_links(
    records,
    session,
    domain,
    courses,
    requested_to_final,
    timeout,
    max_checks,
    time_limit,
    skip_urls=None,
):
    skip_urls = skip_urls or set()
    candidates = {}
    for page in records.values():
        for course_link in page.get("course_links", []):
            url = course_link["url"]
            parsed = urllib.parse.urlparse(url)
            if (
                parsed.scheme != "https"
                or not _same_domain(url, domain)
                or parsed.path.lower().endswith(NON_HTML_SUFFIXES)
                or _url_key(url) in skip_urls
            ):
                continue
            if (
                course_link.get("matched_catalog_courses")
                or PROGRAM_TITLE_PATTERN.search(course_link.get("title", ""))
            ):
                candidates.setdefault(url, []).append(course_link)

    ordered_candidates = sorted(
        candidates.items(),
        key=lambda item: (
            0 if any(link.get("matched_catalog_courses") for link in item[1]) else 1,
            min(link.get("title", "").casefold() for link in item[1]),
            item[0]
        )
    )[:max_checks]
    checked = 0
    failures = {}
    started = time.monotonic()
    for requested_url, links in ordered_candidates:
        if time.monotonic() - started >= time_limit:
            break
        if requested_url in requested_to_final:
            continue
        checked += 1
        try:
            response = session.get(
                requested_url,
                timeout=timeout,
                allow_redirects=True
            )
        except requests.RequestException as exc:
            failures[requested_url] = f"Could not fetch the candidate page: {exc}"
            continue
        if not response.ok:
            failures[requested_url] = f"Candidate page returned HTTP {response.status_code}."
            continue
        if not _same_domain(response.url, domain):
            failures[requested_url] = "Candidate page redirected outside the official domain."
            continue
        if "html" not in response.headers.get("Content-Type", "").lower():
            failures[requested_url] = "Candidate URL did not resolve to an HTML course page."
            continue

        final_url = urllib.parse.urldefrag(response.url)[0]
        requested_to_final[requested_url] = final_url
        record, _ = _page_record(
            final_url,
            response.text,
            courses,
            "course-link verification",
            domain
        )
        record["requested_url"] = requested_url
        records[final_url] = record

    return checked, failures


def discover_institution(
    institution,
    domain,
    courses,
    max_pages=120,
    max_depth=3,
    timeout=8,
    time_limit=180,
    start_urls=None,
    max_candidate_checks=80,
    verification_time_limit=60,
    skip_urls=None,
):
    skip_urls = skip_urls or set()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    roots = start_urls or [f"https://{domain}/"]
    for root in roots:
        if urllib.parse.urlparse(root).scheme != "https" or not _same_domain(root, domain):
            raise ValueError(f"Start URL must use HTTPS on the official {domain} domain: {root}")
    queue = deque((url, 0, "") for url in roots)
    queued = set(roots)
    visited = set()
    records = {}
    requested_to_final = {}
    sitemap_urls = _sitemap_urls(domain, session, limit=max_pages)
    started = time.monotonic()
    requested_to_final = {}
    sitemap_added = False

    while (
        queue
        and len(visited) < max_pages
        and time.monotonic() - started < time_limit
    ):
        current_url, depth, referrer = queue.popleft()
        current_url = urllib.parse.urldefrag(current_url)[0]
        if (
            current_url in visited
            or _url_key(current_url) in skip_urls
            or not _same_domain(current_url, domain)
        ):
            continue
        parsed = urllib.parse.urlparse(current_url)
        if parsed.scheme != "https" or parsed.path.lower().endswith(NON_HTML_SUFFIXES):
            continue
        if EXCLUDED_PATH.search(parsed.path):
            continue
        visited.add(current_url)
        try:
            response = session.get(current_url, timeout=timeout, allow_redirects=True)
            if not response.ok or not _same_domain(response.url, domain):
                continue
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type:
                continue
        except requests.RequestException as exc:
            logging.debug("Could not inspect %s: %s", current_url, exc)
            continue

        final_url = urllib.parse.urldefrag(response.url)[0]
        record, soup = _page_record(final_url, response.text, courses, referrer or "seed", domain)
        record["requested_url"] = current_url
        requested_to_final[current_url] = final_url
        records[final_url] = record
        if depth >= max_depth:
            continue

        for course_link in record["course_links"]:
            candidate = course_link["url"]
            parsed_course_link = urllib.parse.urlparse(candidate)
            path = parsed_course_link.path.lower()
            if (
                candidate not in queued
                and _url_key(candidate) not in skip_urls
                and _same_domain(candidate, domain)
                and parsed_course_link.scheme == "https"
                and not path.endswith(NON_HTML_SUFFIXES)
                and not EXCLUDED_PATH.search(path)
            ):
                queue.append((candidate, depth + 1, final_url))
                queued.add(candidate)

        navigation_candidates = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            candidate = urllib.parse.urldefrag(urllib.parse.urljoin(final_url, href))[0]
            candidate_parsed = urllib.parse.urlparse(candidate)
            path = candidate_parsed.path.lower()
            label = " ".join(anchor.stripped_strings)
            if (
                candidate in queued
                or _url_key(candidate) in skip_urls
                or not _same_domain(candidate, domain)
                or candidate_parsed.scheme != "https"
                or path.endswith(NON_HTML_SUFFIXES)
                or EXCLUDED_PATH.search(path)
            ):
                continue
            if NAVIGATION_TERMS.search(label) or NAVIGATION_TERMS.search(path):
                navigation_candidates.append((
                    _navigation_priority(label, path, courses),
                    candidate,
                    depth + 1,
                    final_url
                ))
        for _, candidate, candidate_depth, candidate_referrer in sorted(navigation_candidates):
            if candidate not in queued:
                queue.append((candidate, candidate_depth, candidate_referrer))
                queued.add(candidate)
        if not sitemap_added:
            for url in sitemap_urls:
                if url not in queued:
                    queue.append((url, 1, "sitemap"))
                    queued.add(url)
            sitemap_added = True

    verified_course_links = {}
    candidate_urls_checked, candidate_failures = _verify_discovered_course_links(
        records,
        session,
        domain,
        courses,
        requested_to_final,
        timeout,
        max_candidate_checks,
        verification_time_limit,
        skip_urls=skip_urls
    )
    for page in records.values():
        if page["verification_status"] == "verified_specific_course_page":
            for course in page["verified_course_matches"]:
                verified_course_links.setdefault(course, page["url"])
        for course_link in page["course_links"]:
            requested_url = course_link["url"]
            if _url_key(requested_url) in skip_urls:
                course_link["verification_status"] = "skipped_prior_verified_url"
                course_link["verification_reason"] = (
                    "This URL is already verified in the course registry and was not refetched."
                )
                continue
            final_url = requested_to_final.get(requested_url)
            target_page = records.get(final_url) if final_url else None
            target_matches = (
                set(target_page.get("verified_course_matches", []))
                if target_page else set()
            )
            matching_courses = set(course_link.get("matched_catalog_courses", []))
            confirmed_courses = sorted(
                (matching_courses & target_matches) if matching_courses else target_matches
            )
            if confirmed_courses:
                course_link["verification_status"] = "verified_specific_course_page"
                course_link["resolved_url"] = final_url
                course_link["verified_catalog_courses"] = confirmed_courses
                course_link["verification_evidence"] = {
                    "page_title": target_page["title"],
                    "matching_heading": next(
                        heading for heading in target_page["headings"]
                        if set(_course_matches(heading, courses)) & set(confirmed_courses)
                    ),
                    "useful_detail_signals": target_page["useful_detail_signals"],
                }
                for course in confirmed_courses:
                    verified_course_links.setdefault(course, final_url)
            else:
                course_link["verification_status"] = (
                    "rejected_not_specific_course_page"
                    if target_page
                    else (
                        "verification_failed"
                        if requested_url in candidate_failures
                        else "discovered_unverified"
                    )
                )
                if requested_url in candidate_failures:
                    course_link["verification_reason"] = candidate_failures[requested_url]
                if final_url:
                    course_link["resolved_url"] = final_url
                if target_page and target_page.get("verified_course_matches"):
                    course_link["verification_reason"] = (
                        "The destination page is specific to a different catalogue course."
                    )
                elif target_page:
                    course_link["verification_reason"] = (
                        "The destination heading does not identify this course with programme details."
                    )
    pages = sorted(
        records.values(),
        key=lambda page: (
            page["page_type"] != "programme_or_course_candidate",
            page["course_name"].casefold(),
            page["url"]
        )
    )
    course_index = {}
    for page in pages:
        for course_link in page["course_links"]:
            indexed_courses = (
                course_link.get("matched_catalog_courses")
                or course_link.get("verified_catalog_courses")
                or [course_link["title"]]
            )
            for course in indexed_courses:
                links = course_index.setdefault(course, [])
                item = {
                    "title": course_link["title"],
                    "url": course_link.get("resolved_url", course_link["url"]),
                    "discovered_from": page["url"],
                    "verification_status": course_link["verification_status"],
                    "matched_catalog_courses": course_link.get(
                        "matched_catalog_courses", []
                    ),
                }
                if course_link.get("verification_reason"):
                    item["verification_reason"] = course_link["verification_reason"]
                if course_link.get("verification_evidence"):
                    item["verification_evidence"] = course_link["verification_evidence"]
                if item not in links:
                    links.append(item)
    for course, url in verified_course_links.items():
        links = course_index.setdefault(course, [])
        item = {
            "title": course,
            "url": url,
            "discovered_from": records[url].get("discovered_from", "sitemap or navigation"),
            "verification_status": "verified_specific_course_page",
            "matched_catalog_courses": [course],
            "verification_evidence": {
                "page_title": records[url]["title"],
                "matching_heading": next(
                    heading for heading in records[url]["headings"]
                    if course in _course_matches(heading, courses)
                ),
                "useful_detail_signals": records[url]["useful_detail_signals"],
            },
        }
        if item not in links:
            links.append(item)
    return {
        "university": institution,
        "official_domain": domain,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "crawl_settings": {
            "max_pages": max_pages,
            "max_depth": max_depth,
            "timeout_seconds": timeout,
            "time_limit_seconds": time_limit,
            "start_urls": roots,
            "max_candidate_checks": max_candidate_checks,
            "verification_time_limit_seconds": verification_time_limit,
        },
        "crawl_summary": {
            "pages_visited": len(visited),
            "html_pages_saved": len(pages),
            "programme_candidates": sum(
                page["page_type"] == "programme_or_course_candidate" for page in pages
            ),
            "catalogue_heading_matches": sum(
                bool(page["matched_catalog_courses"]) for page in pages
            ),
            "programme_links_discovered": sum(
                len(page["course_links"]) for page in pages
            ),
            "verified_specific_course_links": len(verified_course_links),
            "candidate_urls_checked": candidate_urls_checked,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        },
        "verified_course_links": [
            {
                "course": course,
                "url": url,
                "verification_status": "verified_specific_course_page",
                "verification_evidence": {
                    "page_title": records[url]["title"],
                    "matching_heading": next(
                        heading for heading in records[url]["headings"]
                        if course in _course_matches(heading, courses)
                    ),
                    "useful_detail_signals": records[url]["useful_detail_signals"],
                },
            }
            for course, url in sorted(verified_course_links.items())
        ],
        "course_index": {
            course: sorted(links, key=lambda item: (
                item["verification_status"] != "verified_specific_course_page",
                item["title"].casefold(),
                item["url"]
            ))
            for course, links in sorted(course_index.items(), key=lambda item: item[0].casefold())
        },
        "pages": pages,
    }


def _save_inventory(institution_inventory, output_path, replace_existing=False):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        with output_path.open("r", encoding="utf-8") as file:
            inventory = json.load(file)
    else:
        inventory = {"schema_version": 1, "institutions": {}}
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema_version") != 1
        or not isinstance(inventory.get("institutions"), dict)
    ):
        raise ValueError(f"Unsupported programme inventory format in {output_path}")
    institution_key = _normalize(institution_inventory["university"])
    previous = (
        None
        if replace_existing
        else inventory["institutions"].get(institution_key)
    )
    if previous:
        merged_pages = {
            page["url"]: page
            for page in previous.get("pages", []) + institution_inventory.get("pages", [])
        }
        institution_inventory["pages"] = sorted(
            merged_pages.values(),
            key=lambda page: (
                page.get("page_type") != "programme_or_course_candidate",
                page.get("course_name", "").casefold(),
                page["url"]
            )
        )
        merged_verified = {
            item["course"]: item
            for item in previous.get("verified_course_links", [])
        }
        merged_verified.update({
            item["course"]: item
            for item in institution_inventory.get("verified_course_links", [])
        })
        institution_inventory["verified_course_links"] = sorted(
            merged_verified.values(), key=lambda item: item["course"].casefold()
        )
        merged_index = {}
        for source in (previous.get("course_index", {}), institution_inventory.get("course_index", {})):
            for course, links in source.items():
                by_url = {
                    link.get("url"): link
                    for link in merged_index.get(course, [])
                }
                for link in links:
                    url = link.get("url")
                    previous_link = by_url.get(url)
                    if (
                        previous_link is None
                        or link.get("verification_status")
                        == "verified_specific_course_page"
                    ):
                        by_url[url] = link
                merged_index[course] = list(by_url.values())
        institution_inventory["course_index"] = {
            course: sorted(links, key=lambda item: (
                item["verification_status"] != "verified_specific_course_page",
                item["title"].casefold(),
                item["url"]
            ))
            for course, links in sorted(merged_index.items(), key=lambda item: item[0].casefold())
        }
        prior_runs = previous.get("crawl_runs", [])
        institution_inventory["crawl_runs"] = prior_runs + [{
            "discovered_at": institution_inventory["discovered_at"],
            "crawl_settings": institution_inventory["crawl_settings"],
            "crawl_summary": institution_inventory["crawl_summary"],
        }]
        institution_inventory["crawl_summary"] = {
            "pages_visited": len(institution_inventory["pages"]),
            "html_pages_saved": len(institution_inventory["pages"]),
            "programme_candidates": sum(
                page.get("page_type") == "programme_or_course_candidate"
                for page in institution_inventory["pages"]
            ),
            "catalogue_heading_matches": sum(
                bool(page.get("matched_catalog_courses"))
                for page in institution_inventory["pages"]
            ),
            "programme_links_discovered": sum(
                len(page.get("course_links", []))
                for page in institution_inventory["pages"]
            ),
            "verified_specific_course_links": len(institution_inventory["verified_course_links"]),
            "crawl_runs": len(institution_inventory["crawl_runs"]),
        }
    else:
        institution_inventory["crawl_runs"] = [{
            "discovered_at": institution_inventory["discovered_at"],
            "crawl_settings": institution_inventory["crawl_settings"],
            "crawl_summary": institution_inventory["crawl_summary"],
        }]
    inventory["institutions"][institution_key] = institution_inventory
    handle, temporary_path = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=str(output_path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(inventory, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, output_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def _search_inventory(course, output_path, include_unverified=False):
    output_path = Path(output_path)
    if not output_path.exists():
        raise FileNotFoundError(
            f"No programme inventory exists at {output_path}; run a university discovery first."
        )
    with output_path.open("r", encoding="utf-8") as file:
        inventory = json.load(file)
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema_version") != 1
        or not isinstance(inventory.get("institutions"), dict)
    ):
        raise ValueError(f"Unsupported programme inventory format in {output_path}")

    wanted = _normalize(course)
    results_by_url = {}
    for institution in inventory["institutions"].values():
        for indexed_course, links in institution.get("course_index", {}).items():
            if _normalize(indexed_course) != wanted:
                continue
            for link in links:
                if (
                    not include_unverified
                    and link.get("verification_status") != "verified_specific_course_page"
                ):
                    continue
                result = {
                    "university": institution.get("university"),
                    "course": indexed_course,
                    **link,
                }
                key = (_normalize(result["university"] or ""), result.get("url"))
                existing = results_by_url.get(key)
                if (
                    existing is None
                    or (
                        result.get("verification_status") == "verified_specific_course_page"
                        and existing.get("verification_status") != "verified_specific_course_page"
                    )
                ):
                    results_by_url[key] = result
    return sorted(results_by_url.values(), key=lambda item: (
        item.get("university", "").casefold(),
        item.get("verification_status") != "verified_specific_course_page",
        item.get("url", "")
    ))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Crawl one university's official website and inventory programme/course pages. "
            "Discovered pages are not automatically verified course links."
        )
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--university",
        help="Institution name in the course registry; crawl and inventory its official site."
    )
    target_group.add_argument(
        "--course",
        help="Exact course name to retrieve from inventories already collected."
    )
    target_group.add_argument(
        "--all-universities",
        action="store_true",
        help="Discover programme pages for every university in the course catalogue."
    )
    parser.add_argument("--max-pages", type=int, default=120, help="Maximum HTML pages to inspect.")
    parser.add_argument("--max-depth", type=int, default=3, help="Maximum navigation depth from site seeds.")
    parser.add_argument("--timeout", type=int, default=8, help="HTTP timeout per page in seconds.")
    parser.add_argument(
        "--max-candidate-checks",
        type=int,
        default=80,
        help="Maximum number of discovered course links fetched for specific-page verification."
    )
    parser.add_argument(
        "--verification-time-limit",
        type=int,
        default=60,
        help="Maximum seconds spent fetching candidate course pages."
    )
    parser.add_argument(
        "--start-url",
        action="append",
        help=(
            "Official HTTPS university or school page to start from. May be repeated; "
            "the hostname must match the configured university domain."
        )
    )
    parser.add_argument(
        "--time-limit",
        type=int,
        default=180,
        help="Maximum crawl duration in seconds (excluding initial sitemap discovery)."
    )
    parser.add_argument("--output", default=str(INVENTORY_FILE), help="Separate inventory JSON output path.")
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="When searching by course, include discovered links not verified as specific course pages."
    )
    parser.add_argument(
        "--replace-institution",
        action="store_true",
        help="Replace prior inventory for this university instead of merging new crawl results."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing the inventory.")
    args = parser.parse_args(argv)
    if (
        args.max_pages < 1
        or args.max_depth < 0
        or args.timeout < 1
        or args.time_limit < 1
        or args.max_candidate_checks < 1
        or args.verification_time_limit < 1
    ):
        parser.error(
            "--max-pages, --timeout, --time-limit, --max-candidate-checks, "
            "and --verification-time-limit must be positive; --max-depth "
            "cannot be negative."
        )

    if args.course:
        try:
            results = _search_inventory(
                args.course,
                args.output,
                include_unverified=args.include_unverified
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            parser.exit(1, f"Could not search programme inventory: {exc}\n")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    (
        courses_by_institution,
        domains_by_institution,
        institutions_by_key,
        known_verified_urls,
    ) = _load_catalogue()
    if args.all_universities:
        results = []
        failures = []
        for institution_key in sorted(institutions_by_key):
            institution = institutions_by_key[institution_key]
            domain = domains_by_institution.get(institution_key)
            if not domain:
                failures.append({
                    "university": institution,
                    "error": "No official domain configured.",
                })
                continue
            try:
                inventory = discover_institution(
                    institution,
                    domain,
                    sorted(courses_by_institution.get(institution_key, set())),
                    max_pages=args.max_pages,
                    max_depth=args.max_depth,
                    timeout=args.timeout,
                    time_limit=args.time_limit,
                    start_urls=args.start_url if len(institutions_by_key) == 1 else None,
                    max_candidate_checks=args.max_candidate_checks,
                    verification_time_limit=args.verification_time_limit,
                    skip_urls=known_verified_urls.get(institution_key, set()),
                )
                if not args.dry_run:
                    _save_inventory(inventory, args.output)
                results.append({
                    "university": institution,
                    "domain": domain,
                    **inventory["crawl_summary"],
                })
                print(
                    f"{institution}: "
                    f"{inventory['crawl_summary']['verified_specific_course_links']} "
                    "verified specific course pages",
                    flush=True,
                )
            except (OSError, ValueError, requests.RequestException) as exc:
                logging.exception("Could not discover programmes for %s", institution)
                failures.append({"university": institution, "error": str(exc)})
        print(json.dumps({
            "universities_attempted": len(results) + len(failures),
            "universities_saved": len(results) if not args.dry_run else 0,
            "total_pages_visited": sum(item["pages_visited"] for item in results),
            "total_programme_links_discovered": sum(
                item["programme_links_discovered"] for item in results
            ),
            "total_verified_specific_course_links": sum(
                item["verified_specific_course_links"] for item in results
            ),
            "failures": failures,
            "results": results,
        }, ensure_ascii=False, indent=2))
        if failures:
            parser.exit(1, f"{len(failures)} university crawl(s) could not be completed.\n")
        print(
            "Inventory "
            + ("not written (dry run)" if args.dry_run else f"saved to {args.output}")
        )
        return

    institution_key = _normalize(args.university)
    if institution_key not in domains_by_institution:
        parser.error(f"No official domain configured for institution {args.university!r}.")
    inventory = discover_institution(
        args.university,
        domains_by_institution[institution_key],
        sorted(courses_by_institution.get(institution_key, set())),
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        timeout=args.timeout,
        time_limit=args.time_limit,
        start_urls=args.start_url,
        max_candidate_checks=args.max_candidate_checks,
        verification_time_limit=args.verification_time_limit,
        skip_urls=known_verified_urls.get(institution_key, set()),
    )
    if not args.dry_run:
        _save_inventory(
            inventory,
            args.output,
            replace_existing=args.replace_institution
        )
    print(json.dumps(inventory["crawl_summary"], indent=2))
    print(f"Inventory {'not written (dry run)' if args.dry_run else 'saved to ' + args.output}")


if __name__ == "__main__":
    main()
