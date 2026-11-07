import base64
import binascii
import json
import logging
import os
import re
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from utils.ai_engines import is_course_in_catalog


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FOLDER = os.path.join(PROJECT_ROOT, "data")
COURSE_URLS_FILE = os.path.join(DATA_FOLDER, "course_urls.json")
SCHOOL_DOMAINS_FILE = os.path.join(DATA_FOLDER, "school_domains.json")
SEARCH_URL = "https://www.bing.com/search"


def _load_json_mapping(path, description):
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        logging.error("Could not load %s from %s: %s", description, path, error)
        return {}

    if not isinstance(data, dict):
        logging.error("%s at %s must contain a JSON object.", description, path)
        return {}
    return data


def load_course_url_database():
    return _load_json_mapping(COURSE_URLS_FILE, "course URL database")


def load_school_domains():
    return _load_json_mapping(SCHOOL_DOMAINS_FILE, "institution domain database")


OFFICIAL_DOMAINS = load_school_domains()


def _normalized_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _course_title_words(value):
    text = str(value).casefold()
    text = re.sub(r"\bb\s*\.?\s*sc\.?\b", " bachelor of science ", text)
    text = re.sub(r"\bb\s*\.?\s*ed\.?\b", " bachelor of education ", text)
    text = re.sub(r"\bb\s*\.?\s*a\.?\b", " bachelor of arts ", text)
    text = re.sub(r"\b(?:of|in|and|the|with|for)\b", " ", text)
    return set(re.findall(r"[a-z0-9]+", text))


def _course_title_is_present(course_name, text):
    course_words = _course_title_words(course_name)
    return bool(course_words) and course_words.issubset(_course_title_words(text))


def _official_domain(university_name):
    key = _normalized_key(university_name)
    for name, domain in OFFICIAL_DOMAINS.items():
        if _normalized_key(name) == key and isinstance(domain, str) and domain.strip():
            domain = domain.strip().lower().removeprefix("www.")
            if re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", domain):
                return domain
            logging.warning("Ignoring invalid official domain for institution %s.", university_name)
            return None
    return None


def _url_is_on_domain(url, domain):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and (host == domain or host.endswith(f".{domain}"))
    )


def _search_result_url(url):
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return None
    if parsed.hostname not in {"bing.com", "www.bing.com"}:
        return url

    encoded_target = parse_qs(parsed.query).get("u", [""])[0]
    if not encoded_target.startswith("a1"):
        return None
    encoded_target = encoded_target[2:]
    encoded_target += "=" * (-len(encoded_target) % 4)
    try:
        return base64.urlsafe_b64decode(encoded_target).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        logging.warning("Ignoring a malformed course-search result URL.")
        return None


def _local_course_url(university_name, course_name, domain):
    database = load_course_url_database()
    university_key = _normalized_key(university_name)
    for name, courses in database.items():
        if _normalized_key(name) != university_key or not isinstance(courses, dict):
            continue
        course_key = _normalized_key(course_name)
        for title, url in courses.items():
            if _normalized_key(title) == course_key and isinstance(url, str):
                if _url_is_on_domain(url, domain):
                    return url
                logging.warning(
                    "Ignoring non-official course URL for %s at %s.",
                    course_name,
                    university_name,
                )
    return None


def _page_heading(response):
    soup = BeautifulSoup(response.text, "html.parser")
    headings = [soup.title.get_text(" ", strip=True)] if soup.title else []
    headings.extend(heading.get_text(" ", strip=True) for heading in soup.find_all(["h1", "h2"]))
    return " ".join(headings)


def _search_official_course_page(university_name, course_name, domain):
    query = f'site:{domain} "{course_name}"'
    try:
        search_response = requests.get(
            SEARCH_URL,
            params={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=8,
        )
    except requests.RequestException as error:
        logging.warning("Official course search failed for %s at %s: %s", course_name, university_name, error)
        return None

    if search_response.status_code != 200:
        logging.warning(
            "Official course search returned HTTP %s for %s at %s.",
            search_response.status_code,
            course_name,
            university_name,
        )
        return None

    soup = BeautifulSoup(search_response.text, "html.parser")
    for result in soup.select("li.b_algo"):
        link = result.select_one("h2 a[href]")
        if not link:
            continue
        url = _search_result_url(link.get("href"))
        title = link.get_text(" ", strip=True)
        snippet = result.get_text(" ", strip=True)
        if not isinstance(url, str) or not _url_is_on_domain(url, domain):
            continue
        if not _course_title_is_present(course_name, f"{title} {snippet}"):
            continue

        try:
            response = requests.get(
                url,
                headers={"User-Agent": "CareerPath course-page verifier"},
                timeout=8,
                allow_redirects=True,
            )
        except requests.RequestException as error:
            logging.warning("Could not open official course page %s: %s", url, error)
            continue

        final_url = response.url
        if response.status_code != 200 or not _url_is_on_domain(final_url, domain):
            continue
        if _course_title_is_present(course_name, _page_heading(response)):
            return final_url

    logging.info("No official course page verified for %s at %s.", course_name, university_name)
    return None


def get_course_url(university_name, course_name, target_type="institution"):
    """Return a direct official course page only when catalog and page evidence agree."""
    if not isinstance(university_name, str) or not university_name.strip():
        return None
    if not isinstance(course_name, str) or not is_course_in_catalog(course_name):
        logging.info("Skipping course-page search for a course absent from the KUCCPS catalog.")
        return None

    domain = _official_domain(university_name)
    if not domain:
        logging.info("No official domain is registered for institution %s.", university_name)
        return None

    local_url = _local_course_url(university_name, course_name, domain)
    if local_url:
        return local_url

    return _search_official_course_page(university_name, course_name, domain)
