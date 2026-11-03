# Deployment guide (Render)

## Web service

- Python runtime: 3.11.
- Build command: `pip install -r requirements.txt`.
- Start command: `gunicorn secured_app:app --bind 0.0.0.0:$PORT --timeout 300` (also configured in `Procfile`).
- Optional health-check path: `/`.

If a Render dashboard Start Command overrides `Procfile`, ensure it uses `secured_app:app`, not `app:app`; the secured entry point enforces the request cap and bearer authentication.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes in production | Full URL for the active Render Postgres database. Use the service's current internal URL where available; keep credentials private. |
| `AUTH_TOKEN_SECRET` | Yes | Long, random signing key for access tokens. Changing it invalidates issued tokens. |
| `GROQ_API_KEY` | For Groq calls | Provider API key. Keep it server-side in Render; never send it in chat or frontend code. |
| `GROQ_MODEL` | No | Defaults to `openai/gpt-oss-20b`; verify the exact model is enabled for your Groq org and check current billing/free-tier terms. |
| `AUTH_TOKEN_TTL_SECONDS` | No | Signed-token lifetime; defaults to seven days. |
| `GEMINI_API_KEY` | If Gemini code paths are used | Server-side provider key. |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | If SMTP is used | Server-side mail credentials. |

There is no GPT model password. The project makes provider API requests through Groq; configure a Groq API key in the Render environment. Never share credentials in source code or support messages.

## Three-attempt cap

The authenticated recommendation wrapper atomically records up to three lifetime requests per account in `recommendation_usage`. It charges a request before AI execution, preventing users from bypassing the cap through repeated provider failures. The fourth request returns HTTP 402 with `funding_required`. It does not process payments or reset the counter. Admin funding approval and any counter adjustment must be handled through a secure operational process, not a public endpoint.

`secured_app.py` creates the new table if missing using `db.create_all()`. Back up production data and verify the database connection before deploying. Future schema changes should use migrations.

## Deploy checklist and feedback

1. Set secrets in Render's Environment page; do not commit them.
2. Verify web service and database connectivity. For DNS errors, compare the configured `DATABASE_URL` with the active database's Internal Database URL and check Render network/region settings.
3. Deploy latest `main` and confirm Gunicorn binds to `0.0.0.0:$PORT` and Render reports service live.
4. Check `GET /`, then sign in and confirm the response includes `access_token`.
5. Send three authenticated recommendation POSTs; each admitted POST uses one attempt. A fourth must return `402 funding_required` and must not run recommendation generation.
6. Confirm the frontend sends the bearer token. Without it, recommendation requests receive 401.
7. Watch logs for provider `model_not_found`, rate-limit responses, and DB exceptions. A live health check alone does not establish AI-provider health.

## Security notes

- No API keys, passwords, connection strings, or tokens in Git, browser storage logs, or diagnostics.
- Only `/recommend` is protected by this wrapper; other legacy routes need an authorization review.
- Remove or secure `/reset-db-now`; it drops application tables.
- `db.create_all()` is not a migration system.
