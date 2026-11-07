import os
import re
import json
import io
import logging
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import xml.etree.ElementTree as ET
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import pdfplumber
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from pdfminer.pdfparser import PDFSyntaxError
import truststore

truststore.inject_into_ssl()

DATA_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
REGISTRY_FILE = os.environ.get(
    "COURSE_LINKS_FILE",
    os.path.join(DATA_FOLDER, "course_urls.json")
)
REGISTRY_LOCK_FILE = f"{REGISTRY_FILE}.lock"
MAINTENANCE_LOCK_FILE = f"{REGISTRY_FILE}.maintenance.lock"
DISCOVERY_LOCK_FILE = f"{REGISTRY_FILE}.discovery.lock"
KENET_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "kenet_all_200_institutions.txt")
SCHOOL_DOMAINS_FILE = os.path.join(DATA_FOLDER, "school_domains.json")
COURSE_LINK_CHECK_INTERVAL_DAYS = max(1, int(os.environ.get("COURSE_LINK_CHECK_INTERVAL_DAYS", "5")))
COURSE_PAGE_VERIFICATION_VERSION = 2
COURSE_DETAIL_PATTERN = re.compile(
    r"\b(?:admission|entry requirements?|duration|curriculum|course structure|"
    r"programme structure|program structure|programme overview|program overview|"
    r"course objectives|learning outcomes|career opportunities|clinical placement|"
    r"course units?|study mode|minimum grade|kcse)\b",
    re.IGNORECASE
)
NON_COURSE_PATH_PATTERN = re.compile(
    r"(?:^|[/_-])(?:blog|news|events?|council|compliance|"
    r"time[_-]?tables?|exam(?:inations?)?)(?:$|[/_.?-])",
    re.IGNORECASE
)
SITEMAP_PATH_PATTERN = re.compile(
    r"(?:academic|admission|course|degree|department|faculty|programme|program|school|study)",
    re.IGNORECASE
)
SITEMAP_COURSE_STOPWORDS = {
    "and", "arts", "bachelor", "degree", "education", "for", "in", "of",
    "science", "the", "with", "university"
}
COURSE_LINK_MAINTENANCE_POLL_SECONDS = min(
    86400,
    COURSE_LINK_CHECK_INTERVAL_DAYS * 86400
)

file_locks_guard = threading.Lock()
file_locks = {}
maintenance_thread_lock = threading.Lock()
maintenance_thread = None

def load_kenet_domains():
    domains = {}
    if not os.path.exists(KENET_FILE):
        logging.warning(f"⚠️ KENET file not found at {KENET_FILE}. Proceeding without domain hints.")
        return domains
    
    with open(KENET_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            if '->' in line:
                parts = line.split('->')
                name = parts[0].lower().strip()
                url_match = re.search(r'https?://([a-zA-Z0-9.-]+)', parts[1])
                if url_match:
                    domains[name] = url_match.group(1).replace('www.', '')
    return domains

KENET_DOMAINS = load_kenet_domains()
try:
    with open(SCHOOL_DOMAINS_FILE, "r", encoding="utf-8") as school_domains_file:
        KENET_DOMAINS.update(json.load(school_domains_file))
except (OSError, json.JSONDecodeError) as exc:
    logging.warning(f"Could not load additional school domains from {SCHOOL_DOMAINS_FILE}: {exc}")

KENET_DOMAINS.update({
    "jomo kenyatta university of agriculture and technology": "jkuat.ac.ke",
    "multimedia university of kenya": "mmu.ac.ke",
    "st pauls university": "spu.ac.ke",
    "management university of africa": "mua.ac.ke",
    "kenyatta university mama ngina university college": "mnu.ac.ke",
    "kiriri womens university of science and technology": "kwust.ac.ke",
    "kenya highlands evangelical university": "khu.ac.ke",
    "tangaza university": "tangaza.ac.ke",
    "uzima university": "uzimauniversity.ac.ke",
    "presbyterian university of east africa": "puea.ac.ke",
})


@contextmanager
def _file_lock(lock_path, blocking=True):
    with file_locks_guard:
        thread_lock = file_locks.setdefault(lock_path, threading.RLock())
    with thread_lock:
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        with open(lock_path, "a+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                if not lock_file.read(1):
                    lock_file.seek(0)
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                while True:
                    try:
                        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                        msvcrt.locking(lock_file.fileno(), mode, 1)
                        break
                    except OSError:
                        if not blocking:
                            yield False
                            return
                        time.sleep(0.1)
                unlock = lambda: (lock_file.seek(0), msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1))
            else:
                import fcntl

                mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                try:
                    fcntl.flock(lock_file.fileno(), mode)
                except BlockingIOError:
                    yield False
                    return
                unlock = lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

            try:
                yield True
            finally:
                unlock()


def _empty_registry():
    return {"schema_version": 1, "entries": []}


def _load_registry_unlocked():
    if not os.path.exists(REGISTRY_FILE):
        return _empty_registry()
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as registry_file:
            registry = json.load(registry_file)
    except (OSError, json.JSONDecodeError) as exc:
        logging.error(f"Could not read course-page registry at {REGISTRY_FILE}: {exc}")
        raise

    if registry == {}:
        return _empty_registry()
    if (
        not isinstance(registry, dict)
        or registry.get("schema_version") != 1
        or not isinstance(registry.get("entries"), list)
    ):
        logging.error(f"Unsupported course-page registry format in {REGISTRY_FILE}")
        raise ValueError(f"Unsupported course-page registry format in {REGISTRY_FILE}")
    return registry


def _save_registry_unlocked(registry):
    os.makedirs(os.path.dirname(REGISTRY_FILE) or ".", exist_ok=True)
    temporary_file = f"{REGISTRY_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(temporary_file, "w", encoding="utf-8") as registry_file:
            json.dump(registry, registry_file, indent=2, ensure_ascii=False)
            registry_file.flush()
            os.fsync(registry_file.fileno())
        os.replace(temporary_file, REGISTRY_FILE)
    finally:
        if os.path.exists(temporary_file):
            os.remove(temporary_file)


def _registry_entries():
    with _file_lock(REGISTRY_LOCK_FILE):
        return _load_registry_unlocked()["entries"]


def _normalized_key(value):
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _entry_key(domain, course_name, target_type, university=""):
    normalized_domain = domain.lower()
    university_key = _normalized_key(university) if not normalized_domain else ""
    return normalized_domain, _normalized_key(course_name), target_type, university_key


def _find_registry_entry(domain, course_name, target_type):
    wanted_key = _entry_key(domain, course_name, target_type)
    return next(
        (
            entry for entry in _registry_entries()
            if _entry_key(
                entry.get("domain", ""),
                entry.get("course", ""),
                entry.get("target_type", "institution"),
                entry.get("university", "")
            ) == wanted_key
        ),
        None
    )


def _update_registry_entry(entry):
    _update_registry_entries([entry])


def _update_registry_entries(entries):
    if not entries:
        return
    with _file_lock(REGISTRY_LOCK_FILE):
        registry = _load_registry_unlocked()
        replaced_offerings = {
            (
                _normalized_key(entry.get("university", "")),
                _normalized_key(entry["course"]),
                entry["target_type"]
            )
            for entry in entries
        }
        replacements = {
            _entry_key(
                entry["domain"],
                entry["course"],
                entry["target_type"],
                entry.get("university", "")
            ): entry
            for entry in entries
        }
        registry["entries"] = [
            existing for existing in registry["entries"]
            if (
                _entry_key(
                    existing.get("domain", ""),
                    existing.get("course", ""),
                    existing.get("target_type", "institution"),
                    existing.get("university", "")
                ) not in replacements
                and not (
                    not existing.get("domain")
                    and (
                        _normalized_key(existing.get("university", "")),
                        _normalized_key(existing.get("course", "")),
                        existing.get("target_type", "institution")
                    ) in replaced_offerings
                )
            )
        ]
        registry["entries"].extend(replacements.values())
        registry["entries"].sort(key=lambda item: (item["university"].lower(), item["course"].lower()))
        registry["updated_at"] = _now_iso()
        _save_registry_unlocked(registry)


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat()


def _parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _entry_is_due(entry, now=None):
    last_checked = _parse_timestamp(entry.get("last_checked_at"))
    now = now or _now()
    interval_days = 1 if entry.get("status") == "temporarily_unavailable" else COURSE_LINK_CHECK_INTERVAL_DAYS
    return last_checked is None or now - last_checked >= timedelta(days=interval_days)


def _store_check_result(entry, result, checked_at=None):
    updated = dict(entry)
    updated["last_checked_at"] = checked_at or _now_iso()
    if result is True:
        updated["verified"] = True
        updated["status"] = "verified"
        updated["verification_version"] = COURSE_PAGE_VERIFICATION_VERSION
        updated["last_verified_at"] = updated["last_checked_at"]
        updated.pop("last_error", None)
    elif result is False:
        updated["verified"] = False
        updated["status"] = "invalid"
        updated["last_error"] = "The page no longer matches the requested course or official domain."
    else:
        updated["verified"] = False
        updated["status"] = "temporarily_unavailable"
        updated["last_error"] = "The page could not be checked because the website was temporarily unavailable."
    _update_registry_entry(updated)
    return updated

def _course_token_sequence(text):
    normalized_text = re.sub(
        r"\(\s*(?:b\s*\.?\s*a\s*\.?\s*s|b\s*\.?\s*sc\s*\.?\s*hons?)\s*\.?\s*\)",
        " ",
        text.lower()
    )
    normalized_text = re.sub(r"\bb\s*\.?\s*scn\b", " bachelor science nursing ", normalized_text)
    abbreviations = (
        (r"\bb\s*\.?\s*sc\.?(?![a-z])", " bachelor science "),
        (r"\bb\s*\.?\s*a\.?(?!\.?\s*[a-z])", " bachelor arts "),
        (r"\bll\.?\s*b\.?(?![a-z])", " bachelor laws "),
    )
    for pattern, replacement in abbreviations:
        normalized_text = re.sub(pattern, replacement, normalized_text)

    ignored = {"of", "in", "the", "and", "degree"}
    tokens = [
        token for token in re.findall(r"[a-z0-9]+", normalized_text)
        if token not in ignored and token not in {"bscn", "mbchb"} and (len(token) > 2 or token == "it")
    ]
    if "mbchb" in re.findall(r"[a-z0-9]+", normalized_text):
        if {"bachelor", "medicine", "surgery"}.issubset(tokens):
            return tokens
        return ["bachelor", "medicine", "bachelor", "surgery"]

    midpoint = len(tokens) // 2
    if midpoint and len(tokens) % 2 == 0 and tokens[:midpoint] == tokens[midpoint:]:
        tokens = tokens[:midpoint]
    return tokens


def _course_tokens(course_name):
    return set(_course_token_sequence(course_name))


def _course_text_matches(text, course_name):
    expected = _course_token_sequence(course_name)
    actual = _course_token_sequence(text)
    expected_sequences = [expected]
    if len(expected) >= 2 and expected[-2:] == ["nursing", "science"]:
        expected_sequences.append(expected[:-1])
    return any(
        actual[index:index + len(sequence)] == sequence
        for sequence in expected_sequences
        if sequence
        for index in range(len(actual) - len(sequence) + 1)
    )


def _is_course_chrome(tag):
    return any(
        parent.name in {"nav", "header", "footer", "script", "style", "noscript", "template"}
        or parent.get("role") == "navigation"
        or re.search(
            r"(?:^|[-_\s])(?:menu|navigation|site-header|site-footer)(?:$|[-_\s])"
            r"|(?:^|[-_\s])(?<!has-)breadcrumbs?(?:$|[-_\s])",
            " ".join(parent.get("class", [])) + " " + str(parent.get("id", "")),
            re.IGNORECASE
        )
        for parent in (tag, *tag.parents)
    )


def _course_evidence_blocks(soup):
    selectors = ("title", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "td", "dt", "dd")
    blocks = {
        " ".join(tag.stripped_strings).lower()
        for tag in soup.select(", ".join(selectors))
        if not _is_course_chrome(tag)
    }
    return {block for block in blocks if block}


def _course_label_matches(label, course_name):
    return _course_text_matches(label, course_name)


def _is_official_domain_url(url, domain):
    try:
        hostname = urllib.parse.urlparse(url).hostname
    except ValueError:
        return False
    return bool(hostname and (hostname == domain or hostname.endswith(f".{domain}")))


def _verify_course_page(url, domain, course_name):
    return _check_course_page(url, domain, course_name) is True


def _check_course_page(url, domain, course_name):
    if not url.startswith("https://") or not _is_official_domain_url(url, domain):
        return False
    if NON_COURSE_PATH_PATTERN.search(urllib.parse.urlparse(url).path):
        logging.info("Rejected non-course-specific URL path: %s", url)
        return False

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CareerPathAI/1.0)"},
            timeout=10
        )
    except requests.RequestException as exc:
        logging.info(f"Course page could not be fetched: {url} ({exc})")
        return None

    if response.status_code == 429 or response.status_code >= 500:
        logging.info(f"Course page check was temporarily unavailable: {url} (HTTP {response.status_code})")
        return None
    if response.status_code >= 400:
        return False

    if not response.url.startswith("https://") or not _is_official_domain_url(response.url, domain):
        return False

    if "application/pdf" in response.headers.get("Content-Type", "").lower() or response.content.startswith(b"%PDF"):
        return _course_pdf_content_matches(response.content, course_name)

    return _course_page_content_matches(response.text, course_name)


def _course_pdf_content_matches(pdf_content, course_name):
    try:
        with pdfplumber.open(io.BytesIO(pdf_content)) as document:
            page_texts = [
                page.extract_text() or ""
                for page in document.pages[:250]
            ]
    except (OSError, ValueError, EOFError, PDFSyntaxError) as exc:
        logging.info("Could not extract text from candidate course PDF: %s", exc)
        return False

    for index, page_text in enumerate(page_texts):
        if not _course_text_matches(page_text, course_name):
            continue
        nearby_text = " ".join(page_texts[max(0, index - 1):index + 2])
        if len(page_text) >= 100 and COURSE_DETAIL_PATTERN.search(nearby_text):
            return True
    return False


def _course_page_content_matches(html, course_name):
    soup = BeautifulSoup(html, "html.parser")
    content_root = soup.find("main") or soup.find("article") or soup.body
    if not content_root:
        return False

    content_blocks = [
        tag for tag in content_root.select("h1, h2, h3, p, li, tr, dt, dd")
        if not _is_course_chrome(tag)
    ]
    has_course_heading = any(
        _course_text_matches(" ".join(tag.stripped_strings), course_name)
        for tag in content_blocks
        if tag.name in {"h1", "h2", "h3"}
    )
    course_body_mention = any(
        _course_text_matches(" ".join(tag.stripped_strings), course_name)
        for tag in content_blocks
        if tag.name not in {"h1", "h2", "h3"}
    )
    if not has_course_heading and not course_body_mention:
        return False

    content_text = " ".join(
        str(text).strip()
        for text in content_root.strings
        if str(text).strip() and not _is_course_chrome(text.parent)
    )
    return len(content_text) >= 100 and bool(COURSE_DETAIL_PATTERN.search(content_text))


def _course_subject_name(course_name):
    return re.sub(
        r"^\s*bachelor(?:\s+of)?\s+(?:science|arts|business|education|commerce|technology|laws?)"
        r"(?:\s+in)?\s*",
        "",
        course_name,
        flags=re.IGNORECASE
    ).strip(" ()")


def _course_search_queries(domain, course_name, university_name=None):
    subject_name = _course_subject_name(course_name)
    course_terms = [course_name]
    normalized_course = " ".join(re.findall(r"[a-z0-9]+", course_name.lower()))
    if "nursing" in normalized_course:
        course_terms.extend(("Bachelor of Science in Nursing", "BSc Nursing", "BScN", "BSN"))
    if subject_name and subject_name.casefold() != course_name.casefold():
        course_terms.append(subject_name)

    unique_terms = list(dict.fromkeys(
        re.sub(r"\s+", " ", term).strip()
        for term in course_terms
        if term.strip()
    ))
    contexts = ("", "programme", "admission")
    clean_university_name = re.sub(r"\s+", " ", university_name or "").strip()
    queries = []
    for term_index, course_term in enumerate(unique_terms):
        term_contexts = contexts if term_index < 2 else ("",)
        for context in term_contexts:
            query = f'site:{domain} "{course_term}"'
            if clean_university_name:
                query += f' "{clean_university_name}"'
            if context:
                query += f" {context}"
            queries.append(query)
    if unique_terms:
        primary_term = unique_terms[0]
        for context in ("school department faculty", "entry requirements"):
            query = f'site:{domain} "{primary_term}"'
            if clean_university_name:
                query += f' "{clean_university_name}"'
            queries.append(f"{query} {context}")
    return list(dict.fromkeys(queries))


def _sitemap_course_urls(domain, course_names, limit=500):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CareerPathAI/1.0)"}
    sitemap_urls = [f"https://{domain}/sitemap.xml", f"https://www.{domain}/sitemap.xml"]
    try:
        response = requests.get(f"https://{domain}/robots.txt", headers=headers, timeout=5)
        if response.ok:
            sitemap_urls = [
                urllib.parse.urljoin(response.url, match.strip())
                for match in re.findall(r"(?im)^sitemap:\s*(\S+)", response.text)
            ] + sitemap_urls
    except requests.RequestException as exc:
        logging.debug("Could not inspect robots.txt for %s: %s", domain, exc)

    sitemap_queue = list(dict.fromkeys(sitemap_urls))
    visited_sitemaps = set()
    discovered_urls = set()
    deadline = time.monotonic() + 18
    while sitemap_queue and len(visited_sitemaps) < 12 and time.monotonic() < deadline:
        sitemap_url = sitemap_queue.pop(0)
        if sitemap_url in visited_sitemaps or not _is_official_domain_url(sitemap_url, domain):
            continue
        visited_sitemaps.add(sitemap_url)
        try:
            response = requests.get(sitemap_url, headers=headers, timeout=5)
        except requests.RequestException as exc:
            logging.debug("Could not fetch sitemap %s: %s", sitemap_url, exc)
            continue
        if not response.ok or not _is_official_domain_url(response.url, domain):
            continue
        content = response.content
        if response.url.lower().endswith(".gz"):
            try:
                content = gzip.decompress(content)
            except OSError:
                logging.info("Could not decompress sitemap %s.", response.url)
                continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            logging.info("Could not parse sitemap XML at %s.", response.url)
            continue

        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() != "loc" or not element.text:
                continue
            candidate = element.text.strip()
            if not _is_official_domain_url(candidate, domain):
                continue
            if candidate.lower().endswith((".xml", ".xml.gz")):
                if candidate not in visited_sitemaps:
                    sitemap_queue.append(candidate)
            else:
                discovered_urls.add(candidate)
        if len(discovered_urls) >= 10000:
            break

    course_terms = []
    for course in course_names:
        tokens = [
            token for token in _course_token_sequence(course)
            if token not in SITEMAP_COURSE_STOPWORDS and len(token) > 3
        ]
        course_terms.append(set(tokens))

    ranked_urls = []
    for candidate in discovered_urls:
        path_text = urllib.parse.urlparse(candidate).path.lower().replace("-", " ").replace("_", " ")
        path_tokens = set(re.findall(r"[a-z0-9]+", path_text))
        matched_terms = max(
            (len(course_tokens & path_tokens) for course_tokens in course_terms),
            default=0
        )
        if matched_terms or SITEMAP_PATH_PATTERN.search(path_text):
            ranked_urls.append((-matched_terms, candidate))
    ranked_urls.sort()
    return [url for _, url in ranked_urls[:limit]]


def find_official_course_pages(
    university_name,
    domain,
    course_names,
    max_pages=60,
    time_limit=60,
    use_sitemap=True
):
    unique_courses = list(dict.fromkeys(course_names))
    if not unique_courses:
        return {}

    sitemap_urls = _sitemap_course_urls(domain, unique_courses) if use_sitemap else []
    queue = [
        f"https://www.{domain}/",
        f"https://{domain}/",
    ] + sitemap_urls + [
        f"https://www.{domain}/programmes",
        f"https://www.{domain}/programs",
        f"https://www.{domain}/courses",
        f"https://{domain}/programmes"
    ]
    queued_urls = set(queue)
    processed_urls = set()
    visited_urls = set()
    course_candidates = {}
    found = {}
    deadline = time.monotonic() + time_limit
    structural_terms = (
        "admission", "academic", "course", "degree", "department", "faculties", "faculty",
        "programme", "program", "school", "study"
    )
    course_subject_terms = {
        course_name: {
            token for token in re.findall(r"[a-z0-9]+", _course_subject_name(course_name).lower())
            if len(token) > 2 and token not in {"and", "for", "the", "with"}
        }
        for course_name in unique_courses
    }

    while queue and len(visited_urls) < max_pages and time.monotonic() < deadline:
        page_url = queue.pop(0)
        if page_url in processed_urls:
            continue
        processed_urls.add(page_url)

        try:
            response = requests.get(
                page_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; CareerPathAI/1.0)"},
                timeout=6
            )
        except requests.RequestException as exc:
            logging.info(f"Could not inspect official course listing {page_url}: {exc}")
            continue

        if response.status_code >= 400:
            continue
        if not response.url.startswith("https://") or not _is_official_domain_url(response.url, domain):
            continue
        if NON_COURSE_PATH_PATTERN.search(urllib.parse.urlparse(response.url).path):
            logging.info("Skipped non-course-specific URL path: %s", response.url)
            continue
        if response.url in visited_urls:
            continue
        visited_urls.add(response.url)

        if (
            "application/pdf" in response.headers.get("Content-Type", "").lower()
            or response.content.startswith(b"%PDF")
        ):
            for course_name in unique_courses:
                if (
                    course_name not in found
                    and _course_pdf_content_matches(response.content, course_name)
                ):
                    found[course_name] = response.url
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        course_candidates.pop(response.url, None)
        for course_name in unique_courses:
            if course_name not in found and _course_page_content_matches(response.text, course_name):
                found[course_name] = response.url

        course_links = []
        structural_links = []
        prioritized_structural_links = []
        for anchor in soup.select("a[href]"):
            label = anchor.get_text(" ", strip=True)
            candidate_url = urllib.parse.urljoin(response.url, anchor["href"])
            parsed_candidate = urllib.parse.urlparse(candidate_url)
            if parsed_candidate.scheme == "http" and _is_official_domain_url(candidate_url, domain):
                candidate_url = parsed_candidate._replace(scheme="https").geturl()
                parsed_candidate = urllib.parse.urlparse(candidate_url)
            if not _is_official_domain_url(candidate_url, domain) or candidate_url in queued_urls:
                continue

            matched_courses = [
                course_name for course_name in unique_courses
                if course_name not in found and _course_label_matches(label, course_name)
            ]
            link_text = f"{label} {parsed_candidate.path}".lower().replace("-", " ")
            link_tokens = set(re.findall(r"[a-z0-9]+", link_text))
            relevance = max(
                (
                    len(subject_tokens & link_tokens)
                    for subject_tokens in course_subject_terms.values()
                ),
                default=0
            )
            if matched_courses:
                course_candidates[candidate_url] = matched_courses
                course_links.append(candidate_url)
            elif parsed_candidate.path.lower().endswith(".pdf") or any(
                term in link_text for term in structural_terms
            ) or relevance:
                structural_links.append(candidate_url)
                is_hierarchy_link = any(
                    term in link_text
                    for term in ("department", "faculties", "faculty", "school")
                )
                if relevance or is_hierarchy_link:
                    priority = relevance * 10 + int(is_hierarchy_link)
                    prioritized_structural_links.append((priority, candidate_url))

        for _, candidate_url in sorted(
            prioritized_structural_links,
            key=lambda item: (-item[0], item[1])
        ):
            if candidate_url not in queued_urls:
                queued_urls.add(candidate_url)
                queue.insert(0, candidate_url)
        for candidate_url in course_links:
            queued_urls.add(candidate_url)
            queue.insert(0, candidate_url)
        for candidate_url in structural_links:
            if candidate_url not in queued_urls:
                queued_urls.add(candidate_url)
                queue.append(candidate_url)

    return found


def search_official_course_page(university_name, domain, course_name, backend="auto"):
    search_backends = list(dict.fromkeys((backend, "auto")))
    for query in _course_search_queries(domain, course_name, university_name):
        for search_backend in search_backends:
            try:
                with DDGS(timeout=8) as search:
                    results = list(search.text(query, max_results=5, backend=search_backend))
            except DDGSException as exc:
                if not str(exc).startswith("No results found"):
                    logging.warning(
                        "Official course-page search failed for %s (%s, %s): %s",
                        university_name,
                        course_name,
                        search_backend,
                        exc
                    )
                continue
            except Exception as exc:
                logging.warning(
                    "Official course-page search failed for %s (%s, %s): %s",
                    university_name,
                    course_name,
                    search_backend,
                    exc
                )
                continue

            if not results and search_backend != search_backends[-1]:
                continue

            for result in results:
                candidate_url = result.get("href", "")
                if _check_course_page(candidate_url, domain, course_name) is True:
                    return candidate_url
            if results:
                break
    return None


def _make_registry_entry(university_name, domain, course_name, target_type, url, source):
    now = _now_iso()
    return {
        "university": university_name.strip(),
        "domain": domain,
        "course": course_name.strip(),
        "target_type": target_type,
        "url": url,
        "source": source,
        "verified": True,
        "status": "verified",
        "verification_version": COURSE_PAGE_VERIFICATION_VERSION,
        "first_verified_at": now,
        "last_verified_at": now,
        "last_checked_at": now
    }


def run_course_page_maintenance():
    with _file_lock(MAINTENANCE_LOCK_FILE, blocking=False) as lock_acquired:
        if not lock_acquired:
            logging.info("Course-page maintenance is already running in another server worker.")
            return 0

        due_entries = [
            entry for entry in _registry_entries()
            if entry.get("url") and _entry_is_due(entry)
        ]
        checked_count = len(due_entries)
        if due_entries:
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(
                        _check_course_page,
                        entry["url"],
                        entry["domain"],
                        entry["course"]
                    ): entry
                    for entry in due_entries
                }
                for future in as_completed(futures):
                    entry = futures[future]
                    try:
                        result = future.result()
                    except Exception:
                        logging.exception(
                            "Unexpected error checking stored course link: %s",
                            entry["url"]
                        )
                        result = None
                    _store_check_result(entry, result)

        if checked_count:
            logging.info(f"Course-page maintenance checked {checked_count} stored course link(s).")
        return checked_count


def _course_page_maintenance_loop():
    while True:
        try:
            run_course_page_maintenance()
        except Exception:
            logging.exception("Stored course-link maintenance failed.")
        time.sleep(COURSE_LINK_MAINTENANCE_POLL_SECONDS)


def start_course_page_maintenance():
    global maintenance_thread
    with maintenance_thread_lock:
        if maintenance_thread and maintenance_thread.is_alive():
            return
        maintenance_thread = threading.Thread(
            target=_course_page_maintenance_loop,
            name="course-page-maintenance",
            daemon=True
        )
        maintenance_thread.start()


def _find_course_page_in_listings(domain, course_name):
    required_tokens = _course_tokens(course_name)
    if not required_tokens:
        return None

    queue = [
        f"https://www.{domain}/programmes",
        f"https://www.{domain}/programs",
        f"https://www.{domain}/courses",
        f"https://www.{domain}/",
        f"https://{domain}/programmes",
        f"https://{domain}/"
    ]
    queued_urls = set(queue)
    visited_urls = set()
    processed_urls = set()
    deadline = time.monotonic() + 20
    structural_terms = ("school", "faculty", "department", "programme", "program", "course")
    specific_tokens = required_tokens - {"science", "sciences", "arts", "art"}
    discovery_tokens = specific_tokens or required_tokens
    generic_tokens = {
        "department", "departments", "dept", "school", "schools",
        "faculty", "faculties", "programme", "programmes", "program",
        "programs", "course", "courses", "university", "of", "the", "and", "php"
    }
    best_listing_url = None
    best_listing_score = 0

    while queue and len(visited_urls) < 20 and time.monotonic() < deadline:
        page_url = queue.pop(0)
        if page_url in processed_urls:
            continue
        processed_urls.add(page_url)

        try:
            response = requests.get(
                page_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; CareerPathAI/1.0)"},
                timeout=4
            )
            response.raise_for_status()
        except requests.RequestException:
            continue

        if not response.url.startswith("https://") or not _is_official_domain_url(response.url, domain):
            continue
        if response.url in visited_urls:
            continue
        visited_urls.add(response.url)

        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
        path_and_title = f"{urllib.parse.urlparse(response.url).path.lower()} {title}"
        has_course = _verify_course_page(response.url, domain, course_name)

        anchors = soup.find_all("a", href=True)
        course_links = []
        structural_links = []
        for anchor in anchors:
            label = anchor.get_text(" ", strip=True).lower()
            candidate_url = urllib.parse.urljoin(response.url, anchor["href"])
            parsed_candidate = urllib.parse.urlparse(candidate_url)
            if parsed_candidate.scheme == "http" and _is_official_domain_url(candidate_url, domain):
                candidate_url = parsed_candidate._replace(scheme="https").geturl()

            if _course_label_matches(label, course_name):
                if _verify_course_page(candidate_url, domain, course_name):
                    return candidate_url

        if has_course and any(term in path_and_title for term in structural_terms):
            specific_tokens = required_tokens - {"science", "sciences", "arts", "art"}
            ranking_tokens = specific_tokens or required_tokens
            path_tokens = set(re.findall(r"[a-z0-9]+", path_and_title))
            listing_score = (
                10 * len(path_tokens.intersection(ranking_tokens))
                - len(path_tokens - ranking_tokens - generic_tokens)
            )
            if best_listing_url is None or listing_score > best_listing_score:
                best_listing_url = response.url
                best_listing_score = listing_score

        for anchor in anchors:
            label = anchor.get_text(" ", strip=True).lower()
            candidate_url = urllib.parse.urljoin(response.url, anchor["href"])
            parsed_candidate = urllib.parse.urlparse(candidate_url)
            if parsed_candidate.scheme == "http" and _is_official_domain_url(candidate_url, domain):
                candidate_url = parsed_candidate._replace(scheme="https").geturl()
            if not _is_official_domain_url(candidate_url, domain) or candidate_url in queued_urls:
                continue
            combined_tokens = set(re.findall(r"[a-z0-9]+", f"{label} {parsed_candidate.path.lower()}"))
            if any(token in combined_tokens for token in discovery_tokens):
                path_tokens = set(re.findall(r"[a-z0-9]+", parsed_candidate.path.lower()))
                label_tokens = set(re.findall(r"[a-z0-9]+", label))
                course_links.append((
                    (
                        -len(path_tokens.intersection(discovery_tokens)),
                        len(path_tokens - discovery_tokens - generic_tokens),
                        len(label_tokens - discovery_tokens - generic_tokens)
                    ),
                    candidate_url
                ))
            elif any(term in f"{label} {parsed_candidate.path.lower()}" for term in structural_terms):
                structural_links.append(candidate_url)

        for _, candidate_url in sorted(course_links):
            if candidate_url not in queued_urls:
                queued_urls.add(candidate_url)
                queue.insert(0, candidate_url)
        for candidate_url in structural_links:
            if candidate_url not in queued_urls:
                queued_urls.add(candidate_url)
                queue.append(candidate_url)

    return best_listing_url


# --- Official course-page retrieval and verification ---
def get_course_url(university_name, course_name, target_type="institution"):
    domain_hint = _official_domain_for_university(university_name)
    if not domain_hint:
        logging.warning(f"No official domain found for {university_name}; cannot verify a course page.")
        return None

    cached_entry = _find_registry_entry(domain_hint, course_name, target_type)
    if not cached_entry or not cached_entry.get("url"):
        logging.info(f"No curated course page is stored for {university_name}: {course_name}")
        return None
    if (
        cached_entry.get("verified") is not True
        or cached_entry.get("status") != "verified"
    ):
        logging.info(f"No currently verified course page is stored for {university_name}: {course_name}")
        return None
    if (
        cached_entry.get("verification_version") == COURSE_PAGE_VERIFICATION_VERSION
        and not _entry_is_due(cached_entry)
    ):
        return cached_entry["url"]

    check_result = _check_course_page(cached_entry["url"], domain_hint, course_name)
    _store_check_result(cached_entry, check_result)
    if check_result is True:
        return cached_entry["url"]

    logging.warning(
        f"Stored course page did not pass current verification for {university_name}: {course_name}; "
        "no link will be returned."
    )
    return None


def _official_domain_for_university(university_name):
    uni_key = university_name.lower().strip()
    domain = KENET_DOMAINS.get(uni_key)
    if domain:
        return domain
    matching_domains = [
        (name, candidate_domain) for name, candidate_domain in KENET_DOMAINS.items()
        if name in uni_key or uni_key in name
    ]
    if not matching_domains:
        return None
    return max(matching_domains, key=lambda match: len(match[0]))[1]


def verify_and_store_course_link(university_name, course_name, url, target_type="institution"):
    domain = _official_domain_for_university(university_name)
    if not domain:
        raise ValueError(f"No configured official domain for {university_name}.")
    if not _is_official_domain_url(url, domain):
        raise ValueError(f"Candidate URL is not on the configured official domain {domain}.")

    result = _check_course_page(url, domain, course_name)
    if result is None:
        raise RuntimeError(f"Could not verify candidate URL because the site was unavailable: {url}")
    if result is not True:
        raise ValueError(f"Candidate URL did not pass strict course-page verification: {url}")

    entry = _make_registry_entry(
        university_name,
        domain,
        course_name,
        target_type,
        url,
        "manually_curated_official_course_page"
    )
    previous = _find_registry_entry(domain, course_name, target_type)
    if previous:
        for field in ("catalog", "kuccps_program_code", "kuccps_program_codes", "kuccps_pdf_page"):
            if field in previous:
                entry[field] = previous[field]
    _update_registry_entry(entry)
    return entry
