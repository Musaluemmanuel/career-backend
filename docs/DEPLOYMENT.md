# Deployment guide (Render)

## Web service settings

- Runtime: Python 3.11.
- Build command: `pip install -r requirements.txt`.
- Start command: `gunicorn secured_app:app --bind 0.0.0.0:$PORT --timeout 300`.
- Health check path (optional): `/`.

The checked-in Procfile uses the same start command. If the Render dashboard overrides it, update the dashboard command as well.

## Required environment variables

| Name | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | Yes in production | Copy the current Internal Database URL from the correct Render Postgres service. Keep the web service and database on a compatible Render private network/region. Do not paste credentials into issues or logs. |
| `AUTH_TOKEN_SECRET` | Yes | Long, random secret used to sign bearer tokens. Changing it invalidates issued tokens. Do not use a public/default value. |
| `GROQ_API_KEY` | For Groq | Server-side API key. Never expose it to frontend code. |
| `GROQ_MODEL` | No | Defaults to `openai/gpt-oss-20b`; confirm model availability and pricing/plan terms in your Groq console. |
| `AUTH_TOKEN_TTL_SECONDS` | No | Token expiration, default 604800 seconds (7 days). |
| `GEMINI_API_KEY` | If Gemini paths are used | Server-side key. |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | If email functions are used | SMTP credentials. |

`DATABASE_URL` should be the full URL, not only a hostname. If logs show `could not translate host name`, verify the active database's Internal Database URL and runtime environment overrides. If logs show `ModuleNotFoundError: psycopg`, the project includes both psycopg driver packages; verify the deploy installed the current requirements and redeploy after dependency changes.

## Deploy and smoke test

1. Save environment variables and deploy the latest commit.
2. Confirm logs report Gunicorn listening on `0.0.0.0` and Render marks the service live.
3. Request `GET /`; expect HTTP 200.
4. Sign in, verify the response has `access_token`, and make a recommendation request with `Authorization: Bearer <token>`.
5. Check application logs for Groq `model_not_found`, quota/rate-limit, database, or email errors. A live health check does not prove upstream AI calls succeed.

## Database and security cautions

- `app.py` runs `db.create_all()` during import. It creates missing tables but does not manage schema changes. Use migrations for schema changes.
- Do not expose `/reset-db-now` publicly; it drops all application tables.
- `secured_app.py` authenticates `/recommend`, not all routes. Treat other account/history routes as a separate security review.
- Set secrets in Render's environment UI, not Git. Do not log full connection URLs or bearer tokens.
- There is currently no per-user recommendation quota. Groq's own usage limits are provider/org-level and can change.
