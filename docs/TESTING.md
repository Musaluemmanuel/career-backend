# Test guide and feedback

Run the checks from the backend directory:

```powershell
python manage.py check
python manage.py test
python -m unittest discover -s tests -v
```

The Django API suite covers account flows, bearer-token issuance and
validation, verified-account recommendations, account binding, quota behavior,
and upload validation. The separate `unittest` suite checks AI prompt and
course-catalogue behavior. Tests stub recommendation generation; they do not
consume provider tokens, send email, or establish that a live provider/model is
available.

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
