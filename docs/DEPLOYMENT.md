# Deployment guide (Render)

## Web service

- Runtime: Python 3.11.
- Build command: `pip install -r requirements.txt`.
- Start command: configured in `Procfile`; it runs Django migrations and then
  Gunicorn with `careerpath.wsgi:application`.
- Health-check path: `/`.

If the Render dashboard overrides the start command, use the Django WSGI
application shown in the `Procfile`, not `secured_app:app` or `app:app`. The
Flask entry point imports SQLAlchemy directly and is no longer the production
server after the Django migration.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes in production | URL for the active Render PostgreSQL database. Keep credentials private. |
| `DJANGO_SECRET_KEY` | Yes | Private Django signing key. |
| `AUTH_TOKEN_SECRET` | Yes | Long, random signing key for bearer tokens. Changing it invalidates issued tokens. |
| `AUTH_TOKEN_TTL_SECONDS` | No | Token lifetime; defaults to 604800 seconds (seven days). |
| `GROQ_API_KEY` | For Groq calls | Provider API key. Keep it in Render environment settings; never put it in frontend code. |
| `GROQ_MODEL` | No | Defaults to `openai/gpt-oss-20b`; confirm model access with the provider. |
| `GEMINI_API_KEY` | If Gemini code paths are used | Server-side provider key. |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | If SMTP is used | Server-side mail credentials. |
| `CORS_ALLOWED_ORIGINS` | If restricting browser origins | Comma-separated frontend origins. |

`psycopg2-binary` is included for Django's PostgreSQL backend. The legacy Flask
service also lists psycopg v3; the Django WSGI startup does not import the
legacy SQLAlchemy application. If a deployment still reports
`ModuleNotFoundError: No module named 'psycopg'`, verify that its start command
uses `careerpath.wsgi:application`, redeploy with a fresh build, and confirm
`psycopg2-binary` was installed from `requirements.txt`.

There is no GPT model password. Groq provides the configured open-weight
model; availability, plan limits, and cost depend on the provider account.
Do not assume usage is free or unlimited.

## Database migration

Back up production data and verify the database connection before deploy. The
start command runs `migrate --fake-initial` so Django can adopt the existing
Flask `user` and `student_log` tables without recreating them. The later
recommendation-usage migration creates the `recommendation_usage` quota table.
Inspect migration logs and confirm both the existing tables and new quota table
are intact after deployment.

## Quota and funding

The Django API atomically records up to three lifetime requests per account
in `recommendation_usage`. It reserves an attempt before AI generation, so
failed provider requests count. A fourth request returns HTTP 402 with
`funding_required`. It does not process payments or reset counters; any
funding approval or counter adjustment must use a secure administrator
procedure.

## Deploy checklist

1. Set secrets in Render's Environment page; do not commit them.
2. Verify that `DATABASE_URL` points to the active database.
3. Confirm the service start command uses the Django WSGI application.
4. Deploy and verify Gunicorn listens on `0.0.0.0:$PORT` and Render reports the service live.
5. Check `GET /`, then sign in and verify the response includes `access_token`.
6. Send three authenticated recommendation POSTs; the next must return `402 funding_required` without invoking the recommender.
7. Confirm the frontend sends the bearer token. Without it, recommendation requests receive 401.
8. Review logs for migration errors, database connectivity, and provider rate/model errors. A successful health check does not prove AI generation works.

## Security notes

- Never put API keys, passwords, database URLs, or access tokens in Git or logs.
- Only `/recommend` requires bearer authentication; review authorization of other routes separately.
- Never expose destructive database-reset functionality in production.
