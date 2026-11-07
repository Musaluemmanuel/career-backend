import argparse
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pdfplumber

from utils import web_scraper


PROGRAMMES_PDF = os.path.join(
    os.path.dirname(__file__),
    "DEGREE_PROGRAMMES_2025 (1).pdf"
)
KUCCPS_SOURCE = "KUCCPS 2025/2026 DEGREE PROGRAMMES"
KUCCPS_ALIASES = {
    "jomo kenyatta university of agriculture and technology": "jkuat.ac.ke",
    "multimedia university of kenya": "mmu.ac.ke",
    "st pauls university": "spu.ac.ke",
    "kenyatta university mama ngina university college": "mnu.ac.ke",
    "kiriri womens university of science and technology": "kwust.ac.ke",
    "kenya highlands evangelical university": "khu.ac.ke",
    "tangaza university": "tangaza.ac.ke",
    "uzima university": "uzimauniversity.ac.ke",
    "presbyterian university of east africa": "puea.ac.ke",
    "kenya assemblies of god east university": "east.university",
    "management university of africa": "mua.ac.ke",
    "koitaleel samoei university college": "ksu.ac.ke"
}


def _normalize_name(value):
    normalized = " ".join(re.findall(r"[a-z0-9]+", value.lower().replace("&", " and ")))
    return normalized.removeprefix("the ")


def _domain_for_institution(institution):
    normalized = _normalize_name(institution)
    if normalized in KUCCPS_ALIASES:
        return KUCCPS_ALIASES[normalized]

    normalized_domains = {
        _normalize_name(name): domain
        for name, domain in web_scraper.KENET_DOMAINS.items()
    }
    if normalized in normalized_domains:
        return normalized_domains[normalized]
    return None


def extract_offerings(pdf_path=PROGRAMMES_PDF):
    offerings = []
    with pdfplumber.open(pdf_path) as document:
        for page_number, page in enumerate(document.pages, start=1):
            for table in page.extract_tables() or []:
                for row in table:
                    if len(row) < 4 or not row[1]:
                        continue

                    program_code = row[1].strip()
                    if not re.fullmatch(r"[0-9A-Z]{7,10}", program_code):
                        continue

                    institution = re.sub(r"\s+", " ", row[2] or "").strip()
                    course = re.sub(r"\s+", " ", row[3] or "").strip()
                    if not institution or not course:
                        logging.warning(
                            "Ignoring KUCCPS row with a program code but missing institution/course "
                            "(page %s, code %s).",
                            page_number,
                            program_code
                        )
                        continue

                    offerings.append({
                        "institution": institution,
                        "course": course,
                        "program_code": program_code,
                        "page": page_number
                    })

    unique_offerings = {}
    for offering in offerings:
        key = (
            _normalize_name(offering["institution"]),
            _normalize_name(offering["course"]),
            offering["program_code"]
        )
        unique_offerings[key] = offering
    return list(unique_offerings.values())


def seed_course_links(offerings, limit=None, institution_filter=None, search_fallback=True):
    selected = offerings
    if institution_filter:
        filter_key = _normalize_name(institution_filter)
        selected = [
            offering for offering in selected
            if _normalize_name(offering["institution"]) == filter_key
        ]
    if limit is not None:
        selected = selected[:limit]

    institution_groups = {}
    unmapped_offerings = []
    for offering in selected:
        domain = _domain_for_institution(offering["institution"])
        if not domain:
            unmapped_offerings.append(offering)
            continue
        key = (_normalize_name(offering["institution"]), domain)
        group = institution_groups.setdefault(key, {
            "institution": offering["institution"],
            "domain": domain,
            "offerings": []
        })
        group["offerings"].append(offering)

    existing_entries = {
        (
            entry.get("domain", "").lower(),
            _normalize_name(entry.get("course", "")),
            entry.get("target_type", "institution")
        ): entry
        for entry in web_scraper._registry_entries()
    }
    program_codes_by_course = {}
    for group in institution_groups.values():
        for offering in group["offerings"]:
            key = (
                _normalize_name(offering["institution"]),
                _normalize_name(offering["course"])
            )
            program_codes_by_course.setdefault(key, []).append(offering["program_code"])

    def scan_group(group):
        courses = list(dict.fromkeys(item["course"] for item in group["offerings"]))
        found = {}
        for course in courses:
            cached = existing_entries.get(
                (group["domain"], _normalize_name(course), "institution")
            )
            if (
                cached
                and cached.get("verified")
                and cached.get("status") == "verified"
                and cached.get("verification_version")
                == web_scraper.COURSE_PAGE_VERIFICATION_VERSION
                and not web_scraper._entry_is_due(cached)
            ):
                found[course] = cached["url"]

        missing_courses = [course for course in courses if course not in found]
        if missing_courses:
            found.update(web_scraper.find_official_course_pages(
                group["institution"],
                group["domain"],
                missing_courses
            ))
        for course in courses:
            if search_fallback and course not in found:
                url = web_scraper.search_official_course_page(
                    group["institution"],
                    group["domain"],
                    course,
                    backend="yahoo"
                )
                if url:
                    found[course] = url
        newly_verified = []
        checked_at = datetime.now(timezone.utc).isoformat()
        for course in missing_courses:
            url = found.get(course)
            if not url:
                continue
            previous = existing_entries.get(
                (group["domain"], _normalize_name(course), "institution"),
                {}
            )
            entry = dict(previous)
            entry.update({
                "university": group["institution"],
                "domain": group["domain"],
                "course": course,
                "target_type": "institution",
                "url": url,
                "source": "kuccps_official_course_page",
                "verified": True,
                "status": "verified",
                "verification_version": web_scraper.COURSE_PAGE_VERIFICATION_VERSION,
                "first_verified_at": previous.get("first_verified_at", checked_at),
                "last_verified_at": checked_at,
                "last_checked_at": checked_at,
                "catalog": KUCCPS_SOURCE,
                "kuccps_program_code": program_codes_by_course[
                    (_normalize_name(group["institution"]), _normalize_name(course))
                ][0],
                "kuccps_program_codes": program_codes_by_course[
                    (_normalize_name(group["institution"]), _normalize_name(course))
                ]
            })
            entry.pop("last_error", None)
            newly_verified.append(entry)
        web_scraper._update_registry_entries(newly_verified)
        return group, found

    verified_urls = {}
    failed_groups = set()
    max_workers = min(8, max(1, len(institution_groups)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(scan_group, group): key
            for key, group in institution_groups.items()
        }
        for completed_count, future in enumerate(as_completed(futures), start=1):
            group_key = futures[future]
            try:
                group, found = future.result()
                for course, url in found.items():
                    verified_urls[(group["domain"], _normalize_name(course))] = url
            except Exception:
                logging.exception(
                    "Official course-page scan failed for institution group %s.",
                    group_key[0]
                )
                failed_groups.add(group_key)
            if completed_count % 5 == 0:
                logging.info(
                    "Official institution scans completed: %s/%s",
                    completed_count,
                    len(institution_groups)
                )

    checked_at = datetime.now(timezone.utc).isoformat()
    program_codes_by_offering = {}
    for offering in selected:
        key = (
            _normalize_name(offering["institution"]),
            _normalize_name(offering["course"])
        )
        program_codes_by_offering.setdefault(key, []).append(offering["program_code"])

    output_entries = []
    verified_count = 0
    unresolved_count = 0
    for offering in selected:
        institution = offering["institution"]
        course = offering["course"]
        domain = _domain_for_institution(institution)
        if not domain:
            entry = {
                "university": institution,
                "domain": "",
                "course": course,
                "target_type": "institution",
                "url": None,
                "source": "kuccps_catalog",
                "verified": False,
                "status": "unmapped",
                "last_checked_at": checked_at,
                "last_error": "No confirmed official domain is configured for this institution."
            }
            unresolved_count += 1
        else:
            url = verified_urls.get((domain, _normalize_name(course)))
            previous = existing_entries.get((domain, _normalize_name(course), "institution"), {})
            if url:
                entry = dict(previous)
                entry.update({
                    "university": institution,
                    "domain": domain,
                    "course": course,
                    "target_type": "institution",
                    "url": url,
                    "source": "kuccps_official_course_page",
                    "verified": True,
                    "status": "verified",
                    "verification_version": web_scraper.COURSE_PAGE_VERIFICATION_VERSION,
                    "first_verified_at": previous.get("first_verified_at", checked_at),
                    "last_verified_at": checked_at,
                    "last_checked_at": checked_at,
                    "catalog": KUCCPS_SOURCE,
                    "kuccps_program_code": offering["program_code"]
                })
                entry.pop("last_error", None)
                verified_count += 1
            else:
                key = (_normalize_name(institution), domain)
                reason = (
                    "Official-domain scan failed; no link was verified."
                    if key in failed_groups
                    else "No exact course page or official programme-listing row was verified."
                )
                entry = {
                    "university": institution,
                    "domain": domain,
                    "course": course,
                    "target_type": "institution",
                    "url": None,
                    "source": "kuccps_catalog",
                    "verified": False,
                    "status": "not_found",
                    "last_checked_at": checked_at,
                    "last_error": reason,
                    "catalog": KUCCPS_SOURCE,
                    "kuccps_program_code": offering["program_code"]
                }
                unresolved_count += 1
        entry["catalog"] = KUCCPS_SOURCE
        program_codes = program_codes_by_offering[
            (_normalize_name(institution), _normalize_name(course))
        ]
        entry["kuccps_program_code"] = program_codes[0]
        entry["kuccps_program_codes"] = program_codes
        entry["kuccps_pdf_page"] = offering["page"]
        output_entries.append(entry)

    web_scraper._update_registry_entries(output_entries)

    return {
        "selected": len(selected),
        "verified": verified_count,
        "unresolved": unresolved_count,
        "unmapped": len(unmapped_offerings),
        "institutions_scanned": len(institution_groups),
        "failed_institution_scans": len(failed_groups)
    }


def retry_unresolved_course_links(limit=20, retry_after_days=30):
    with web_scraper._file_lock(web_scraper.DISCOVERY_LOCK_FILE, blocking=False) as lock_acquired:
        if not lock_acquired:
            logging.info("Unresolved course-link discovery is already running in another worker.")
            return {"selected": 0, "skipped": True}

        now = datetime.now(timezone.utc)
        existing_entries = {
            (
                _normalize_name(entry.get("university", "")),
                _normalize_name(entry.get("course", ""))
            ): entry
            for entry in web_scraper._registry_entries()
        }
        offerings = extract_offerings()
        eligible = {}
        for offering in offerings:
            if not _domain_for_institution(offering["institution"]):
                continue
            key = (
                _normalize_name(offering["institution"]),
                _normalize_name(offering["course"])
            )
            entry = existing_entries.get(key)
            if (
                entry
                and entry.get("status") == "verified"
                and entry.get("verified") is True
                and entry.get("verification_version")
                == web_scraper.COURSE_PAGE_VERIFICATION_VERSION
            ):
                continue
            if entry and entry.get("status") == "temporarily_unavailable":
                continue

            last_checked = (
                web_scraper._parse_timestamp(entry.get("last_checked_at"))
                if entry else None
            )
            if last_checked and now - last_checked < timedelta(days=retry_after_days):
                continue
            eligible.setdefault(key, offering)

        due_keys = sorted(
            eligible,
            key=lambda key: (
                web_scraper._parse_timestamp(
                    existing_entries.get(key, {}).get("last_checked_at")
                ) or datetime.min.replace(tzinfo=timezone.utc),
                key
            )
        )[:max(0, limit)]
        if not due_keys:
            return {
                "selected": 0,
                "verified": 0,
                "unresolved": 0,
                "unmapped": 0,
                "institutions_scanned": 0,
                "failed_institution_scans": 0
            }

        selected_keys = set(due_keys)
        selected_offerings = [
            offering for offering in offerings
            if (
                _normalize_name(offering["institution"]),
                _normalize_name(offering["course"])
            ) in selected_keys
        ]
        logging.info(
            "Retrying %s unresolved institution-course offerings.",
            len(due_keys)
        )
        return seed_course_links(selected_offerings)


def main():
    parser = argparse.ArgumentParser(
        description="Verify and store official course pages listed in the KUCCPS degree programmes PDF."
    )
    parser.add_argument("--institution", help="Only process one exact institution name from the PDF.")
    parser.add_argument("--course", help="Only process one exact course name from the PDF.")
    parser.add_argument("--limit", type=int, help="Process at most this many rows.")
    parser.add_argument(
        "--unresolved-only",
        action="store_true",
        help="Retry only offerings without a currently verified registry entry."
    )
    parser.add_argument(
        "--sitemap-only",
        action="store_true",
        help="Use official-site sitemap and page crawling without external search providers."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print record and mapping counts without web requests.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    offerings = extract_offerings()
    if args.course:
        course_key = _normalize_name(args.course)
        offerings = [
            offering for offering in offerings
            if _normalize_name(offering["course"]) == course_key
        ]
    if args.institution:
        institution_key = _normalize_name(args.institution)
        offerings = [
            offering for offering in offerings
            if _normalize_name(offering["institution"]) == institution_key
        ]
    if args.unresolved_only:
        verified_offerings = {
            (
                _normalize_name(entry.get("university", "")),
                _normalize_name(entry.get("course", ""))
            )
            for entry in web_scraper._registry_entries()
            if entry.get("status") == "verified"
            and entry.get("verified") is True
            and entry.get("url")
            and entry.get("verification_version")
            == web_scraper.COURSE_PAGE_VERIFICATION_VERSION
        }
        offerings = [
            offering for offering in offerings
            if (
                _normalize_name(offering["institution"]),
                _normalize_name(offering["course"])
            ) not in verified_offerings
        ]

    mapped = sum(1 for offering in offerings if _domain_for_institution(offering["institution"]))
    print(
        f"KUCCPS source rows selected: {len(offerings)}; "
        f"rows with an official-domain mapping: {mapped}; "
        f"unmapped rows: {len(offerings) - mapped}"
    )

    if args.dry_run:
        return

    result = seed_course_links(
        offerings,
        limit=args.limit,
        search_fallback=not args.sitemap_only
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
