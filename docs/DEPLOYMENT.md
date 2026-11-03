# Deployment guide (Render)

## Web service

- Runtime: Python 3.11.
- Build command: `pip install -r requirements.txt`.
- Start command: `gunicorn secured_app:app --bind 0.0.0.0:$PORT --timeout 300` (configured in `Procfile`).
- Optional health-check path: `/`.

If the Render dashboard overrides the Procfile, ensure the command uses `secured_app:app`, not `app:app`; otherwise authentication and quota handling are bypassed.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes in production | Full URL for the active Render Postgres database. Use the current internal URL as appropriate. Keep credentials private. |
| `AUTH_TOKEN_SECRET` | Yes | Long, random signing key for access tokens. Changing it invalidates issued tokens. |
| `GROQ_API_KEY` | For Groq calls | Provider API key. Keep it in Render environment settings; never put it in frontend code. |
| `GROQ_MODEL` | No | Defaults to `openai/gpt-oss-20b`; verify access and current pricing/plan terms for your Groq organization. |
| `AUTH_TOKEN_TTL_SECONDS` | No | Token lifetime; defaults to 604800 seconds (7 days). |
| `GEMINI_API_KEY` | If Gemini code paths are used | Server-side provider key. |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | If SMTP is used | Server-side mail credentials. |

There is no GPT model password. This service calls Groq's API; configure a Groq API key in the backend environment. The `openai/gpt-oss-20b` model ID names an open-weight GPT-OSS model served by Groq; it is not an OpenAI ChatGPT subscription/API credential. Free-tier access and limits are subject to Groq policies and model availability; do not assume usage is free or unlimited.

## Quota and funding

The secured wrapper atomically records up to three lifetime requests per account in `recommendation_usage`. It reserves an attempt before AI generation, so failed provider requests count. A fourth request returns HTTP 402 with `funding_required`. It does not process payments or reset counters. Any funding approval/counter adjustment must be carried out through a secure admin procedure.

`secured_app.py` uses `db.create_all()` to create the missing quota table. Back up production data and verify the database connection before deploy. Use migrations for future schema changes.

## Deploy checklist and feedback

1. Set secrets in Render's Environment page; do not commit them.
2. Check web service/database network and region, and verify `DATABASE_URL` matches the active DB's URL.
3. Deploy latest `main` and verify Gunicorn listens on `0.0.0.0:$PORT` and Render reports service live.
4. Check `GET /`, then sign in and verify response includes `access_token`.
5. Send three authenticated recommendation POSTs; the next must return `402 funding_required` without invoking the recommender.
6. Confirm the frontend sends the bearer token. Without it, recommendation requests receive 401.
7. Watch logs for provider `model_not_found`, rate-limit, and database exceptions. A successful health check doesn't prove AI generation works.

## Security notes

- Never put API keys, passwords, database URLs, or access tokens in Git or logs.
- Only `/recommend` is authenticated by this wrapper; other legacy routes need review.
- Secure or remove `/reset-db-now`; it drops application tables.
- `db.create_all()` is not a database migration system.
