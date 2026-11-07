import json
import tempfile
import unittest
from pathlib import Path

from utils.discover_program_catalogue import (
    _course_matches,
    _page_record,
    _save_inventory,
    _search_inventory,
    _verify_discovered_course_links,
    discover_institution,
)


class DiscoverProgrammeCatalogueTests(unittest.TestCase):
    courses = [
        "BACHELOR OF SCIENCE (NURSING)",
        "BACHELOR OF SCIENCE (B.SC)",
        "BACHELOR OF LAWS (LL.B.)",
    ]

    def test_generic_words_do_not_match_unrelated_course_names(self):
        self.assertEqual(_course_matches("Resources", self.courses), [])
        self.assertEqual(_course_matches("Procurement", self.courses), [])
        self.assertEqual(
            _course_matches("Bachelor of Science Degree in Nursing", self.courses),
            ["BACHELOR OF SCIENCE (NURSING)"]
        )
        self.assertEqual(
            _course_matches(
                "Master of Science in Nursing",
                ["BACHELOR OF SCIENCE (NURSING)"]
            ),
            []
        )
        self.assertEqual(
            _course_matches(
                "Bachelor of Laws in Enforcement",
                ["BACHELOR OF LAWS (LL.B.)"]
            ),
            []
        )

    def test_specific_course_page_requires_matching_heading_and_details(self):
        html = """
        <html>
          <head><title>Bachelor of Science in Nursing</title></head>
          <body>
            <nav><h1>Academic Programmes</h1></nav>
            <main>
              <h1>Bachelor of Science in Nursing</h1>
              <p>Overview of clinical practice and nursing education.</p>
              <h2>Entry Requirements</h2>
              <p>KCSE minimum grade C+.</p>
              <a href="/courses/nursing">Bachelor of Science in Nursing</a>
              <a href="https://external.example/law">Bachelor of Laws</a>
            </main>
          </body>
        </html>
        """
        record, _ = _page_record(
            "https://example.edu/courses/nursing",
            html,
            self.courses,
            "test",
            "example.edu"
        )
        self.assertEqual(
            record["verification_status"],
            "verified_specific_course_page"
        )
        self.assertEqual(
            record["verified_course_matches"],
            ["BACHELOR OF SCIENCE (NURSING)"]
        )
        self.assertEqual(len(record["course_links"]), 1)

    def test_generic_programme_listing_is_not_verified(self):
        html = """
        <html>
          <head><title>Undergraduate Programmes</title></head>
          <body>
            <main>
              <h1>Undergraduate Programmes</h1>
              <h2>Bachelor of Science in Nursing</h2>
              <p>Entry requirements are KCSE grade C+.</p>
              <h2>Bachelor of Laws LL.B.</h2>
              <p>Entry requirements are KCSE grade C+.</p>
            </main>
          </body>
        </html>
        """
        record, _ = _page_record(
            "https://example.edu/undergraduate-programmes",
            html,
            self.courses,
            "test",
            "example.edu"
        )
        self.assertEqual(record["verification_status"], "discovered_unverified")
        self.assertEqual(record["verified_course_matches"], [])

    def test_course_lookup_returns_verified_links_only_by_default(self):
        inventory = {
            "schema_version": 1,
            "institutions": {
                "example university": {
                    "university": "EXAMPLE UNIVERSITY",
                    "course_index": {
                        "BACHELOR OF SCIENCE (NURSING)": [
                            {
                                "url": "https://example.edu/nursing",
                                "verification_status": "verified_specific_course_page",
                            },
                            {
                                "url": "https://example.edu/nursing",
                                "title": "Duplicate Nursing record",
                                "verification_status": "verified_specific_course_page",
                            },
                            {
                                "url": "https://example.edu/programmes",
                                "verification_status": "discovered_unverified",
                            },
                        ]
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "programme_pages.json"
            path.write_text(json.dumps(inventory), encoding="utf-8")
            verified = _search_inventory("bachelor of science nursing", path)
            all_discovered = _search_inventory(
                "BACHELOR OF SCIENCE (NURSING)",
                path,
                include_unverified=True
            )
        self.assertEqual(len(verified), 1)
        self.assertEqual(len(all_discovered), 2)

    def test_start_urls_must_use_the_official_https_domain(self):
        with self.assertRaises(ValueError):
            discover_institution(
                "EXAMPLE UNIVERSITY",
                "example.edu",
                [],
                start_urls=["http://example.edu/"]
            )

    def test_candidate_url_is_fetched_before_it_can_be_verified(self):
        html = """
        <html>
          <head><title>Bachelor of Science in Nursing</title></head>
          <body><main>
            <h1>Bachelor of Science in Nursing</h1>
            <p>Programme overview with clinical practice details.</p>
            <h2>Entry Requirements</h2><p>KCSE grade C+.</p>
          </main></body>
        </html>
        """

        class Response:
            ok = True
            url = "https://example.edu/courses/nursing"
            headers = {"Content-Type": "text/html"}
            text = html

        class Session:
            def get(self, url, **kwargs):
                self.requested_url = url
                return Response()

        records = {
            "https://example.edu/programmes": {
                "url": "https://example.edu/programmes",
                "course_links": [{
                    "title": "Bachelor of Science in Nursing",
                    "url": "https://example.edu/courses/nursing",
                    "matched_catalog_courses": ["BACHELOR OF SCIENCE (NURSING)"],
                }],
            }
        }
        requested_to_final = {}
        checked, failures = _verify_discovered_course_links(
            records,
            Session(),
            "example.edu",
            ["BACHELOR OF SCIENCE (NURSING)"],
            requested_to_final,
            timeout=1,
            max_checks=5,
            time_limit=5
        )
        page = records["https://example.edu/courses/nursing"]
        self.assertEqual(checked, 1)
        self.assertEqual(failures, {})
        self.assertEqual(page["verification_status"], "verified_specific_course_page")

    def test_previously_verified_urls_are_not_refetched(self):
        class Session:
            def get(self, url, **kwargs):
                raise AssertionError(f"Previously verified URL was fetched: {url}")

        records = {
            "https://example.edu/programmes": {
                "url": "https://example.edu/programmes",
                "course_links": [{
                    "title": "Bachelor of Science in Nursing",
                    "url": "https://example.edu/courses/nursing#overview",
                    "matched_catalog_courses": ["BACHELOR OF SCIENCE (NURSING)"],
                }],
            }
        }
        checked, failures = _verify_discovered_course_links(
            records,
            Session(),
            "example.edu",
            ["BACHELOR OF SCIENCE (NURSING)"],
            {},
            timeout=1,
            max_checks=5,
            time_limit=5,
            skip_urls={"https://example.edu/courses/nursing"},
        )
        self.assertEqual(checked, 0)
        self.assertEqual(failures, {})

    def test_repeated_discovery_runs_merge_the_same_institution(self):
        def result(url, discovered_at):
            return {
                "university": "EXAMPLE UNIVERSITY",
                "official_domain": "example.edu",
                "discovered_at": discovered_at,
                "crawl_settings": {"start_urls": [url]},
                "crawl_summary": {"pages_visited": 1},
                "verified_course_links": [],
                "course_index": {},
                "pages": [{"url": url, "page_type": "academic_navigation"}],
            }

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "programme_pages.json"
            _save_inventory(result("https://example.edu/", "first"), path)
            _save_inventory(result("https://school.example.edu/", "second"), path)
            saved = json.loads(path.read_text(encoding="utf-8"))
        institution = saved["institutions"]["example university"]
        self.assertEqual(
            {page["url"] for page in institution["pages"]},
            {"https://example.edu/", "https://school.example.edu/"}
        )
        self.assertEqual(len(institution["crawl_runs"]), 2)


if __name__ == "__main__":
    unittest.main()
