# Test guide and feedback

Run the checks from the backend directory:

```powershell
python manage.py check
python manage.py test
python -m unittest discover -s tests -v
```

The Django API suite covers account flows, bearer-token issuance and
validation, verified-account recommendations, account binding, quota behavior,
upload validation, and recommendation/course-page verification. The separate
`unittest` suite checks AI prompt and course-catalogue behavior. Tests stub
recommendation generation; they do not consume provider tokens, send email,
or establish that a live provider/model is available.

## Latest verified test run

On 2026-11-07, from the backend repository root:

| Command/check | Result |
| --- | --- |
| `python manage.py test` | Passed: 25 Django tests; Django system check reported no issues. |
| `python -m unittest discover -s tests -v` | Passed: 7 utility/authentication tests. |
| Python AST parse of `utils/ai_engines.py`, `utils/web_scraper.py`, `api/views.py`, and `api/tests.py` | Passed. |
| `git diff --check` | Passed for backend and frontend working trees. |
| Live `/recommend` request without a bearer token | Returned the expected HTTP 401 `authentication_required`; authenticated production analysis was not exercised. |
| Live official-page lookup for Nursing at Kenyatta, University of Nairobi, and Moi, plus Computer Science at University of Nairobi | No official course page passed verification. The offering map is empty, so the API correctly returned no verified institution matches. |

These live checks depend on external services and are diagnostic rather than
repeatable unit tests. Do not treat an unverified search result or a successful
HTTP status alone as evidence that an institution offers a course.

After deployment, verify in staging that sign-in returns an access token, the
browser sends it to `/recommend`, three requests are admitted, and a fourth
returns HTTP `402 funding_required`. Confirm migrations preserved the existing
account and history tables and created `recommendation_usage`. Check the
Render and provider consoles separately for live database and AI access.

The quota caps HTTP recommendation attempts, not internal Groq calls or
retries made for one admitted attempt. Provider limits and any costs are
separate.

The frontend must store the returned `access_token` and send it as a bearer
token with recommendation requests. Without that integration, recommendation
requests receive HTTP 401.
