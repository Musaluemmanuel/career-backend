# Backend architecture

## Runtime and entry point

The production service is a Django application. `Procfile` and `Dockerfile`
run `python manage.py migrate --noinput --fake-initial` and start Gunicorn
with `careerpath.wsgi:application`. The older Flask application and its
utilities remain in the repository for compatibility and the legacy test
suite; they are not the Django production entry point.

## Components

| Component | Responsibility |
| --- | --- |
| `careerpath/settings.py` | Environment configuration, database URL parsing, mail, token settings, and CORS. |
| `careerpath/urls.py` | HTTP route mapping to the API views. |
| `api/views.py` | Account flows, chat/recommendation orchestration, reports, and response validation. |
| `api/models.py` | Django mappings for legacy accounts/history and recommendation usage. |
| `api/migrations/` | Database schema adoption and quota-table evolution. |
| `utils/ai_engines.py` | AI guidance, grade handling, and conversational responses. |
| `utils/web_scraper.py` | Course and institution URL lookup. |
| `data/` and `courses.json` | Local course and institution reference data. |

## Persistence

`User` maps to the existing `user` table and stores account verification and
saved report history. `StudentLog` maps to `student_log`. The
`RecommendationUsage` model stores per-account lifetime attempts in
`recommendation_usage`. Initial migration adoption is handled with
`--fake-initial`; subsequent schema changes must use Django migrations.

## Request flow and trust boundaries

The static frontend calls the Django API over HTTP. The API forwards chat and
recommendation work to server-side provider clients. API keys and database
credentials belong only in the backend environment.

Verified sign-in can return a signed token. `/recommend` verifies that token,
binds identity to the account, and reserves one of three lifetime attempts
before calling the AI pipeline. Other routes do not consistently enforce
per-account bearer authorization. This is a known authorization boundary;
refer to the [API reference](API.md#authorization-boundary) and
[security policy](../SECURITY.md) before exposing account-sensitive routes.

## Operational constraints

- Production requires a persistent PostgreSQL database and properly
  configured secrets and provider credentials.
- Provider calls can fail independently of the API health response.
- The legacy Flask modules are not interchangeable with the Django WSGI
  entry point.
- `CORS_ALLOWED_ORIGINS` should be set to the exact deployed frontend origins.
- `docs/DEPLOYMENT.md` describes migration and release checks.
