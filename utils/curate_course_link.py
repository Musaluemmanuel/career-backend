import argparse
import json

from utils.web_scraper import verify_and_store_course_link


def main():
    parser = argparse.ArgumentParser(
        description="Strictly verify and store one manually discovered official course page."
    )
    parser.add_argument("--university", required=True, help="Institution name as used in the registry.")
    parser.add_argument("--course", required=True, help="Exact programme name to verify on the page.")
    parser.add_argument("--url", required=True, help="Candidate HTTPS URL on the institution's official domain.")
    args = parser.parse_args()

    try:
        entry = verify_and_store_course_link(args.university, args.course, args.url)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"Course link not stored: {exc}\n")

    print(json.dumps({
        "university": entry["university"],
        "course": entry["course"],
        "url": entry["url"],
        "verified": entry["verified"],
        "status": entry["status"],
        "verification_version": entry["verification_version"]
    }, indent=2))


if __name__ == "__main__":
    main()
