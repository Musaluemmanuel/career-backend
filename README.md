# CareerPath AI Backend

Flask API for personalized Kenyan course and career guidance. It stores account and recommendation history in SQLAlchemy, calls Groq for AI generation, verifies institution links, and is deployed as a Gunicorn web service.

## Project layout

- `app.py` — Flask routes, database models, and recommendation pipeline.
- `secured_app.py` — production entry point: bearer-token authentication for recommendations, three-attempt per-account lifetime cap, funding-required response, and shared Groq model configuration.
- `utils/ai_engines.py` — course eligibility, validation, and career recommendation generation.
- `utils/web_scraper.py` — institution-link lookup and local cache.
- `docs/API.md` — API and authentication details.
- `docs/DEPLOYMENT.md` — Render settings and environment variables.
- `tests/` — isolated security and quota tests.

## Local setup

Python 3.11 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
export DATABASE_URL='sqlite:///local_test.db'
export AUTH_TOKEN_SECRET='use-a-long-random-secret'
export GROQ_API_KEY='your-provider-api-key'
export GROQ_MODEL='openai/gpt-oss-20b'
```

Run the production entry point:

```bash
gunicorn secured_app:app --bind 0.0.0.0:${PORT:-10000} --timeout 300
```

See the [API guide](docs/API.md) and [deployment guide](docs/DEPLOYMENT.md).

## Recommendation attempt policy

Each verified account can start at most three recommendation requests over its lifetime. A request reserves one attempt before AI execution, including a request whose provider call later fails, so repeated retries cannot evade the cap. A fourth request receives HTTP `402` with `code: funding_required` and a request-funding message. The counter is stored in the `recommendation_usage` table. This is an application usage limit, not a payment system; no payment is collected or automatically enabled.

The recommendation endpoint requires a valid bearer token returned by successful verified login. Frontends must persist the `access_token` and send `Authorization: Bearer <token>` with `POST /recommend`.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests use an in-memory SQLite database and stub the recommendation endpoint, so they do not consume provider tokens, send email, or verify Groq availability. Run staging integration tests after deployment.

## Provider secrets and cost

This API uses Groq's API, not a ChatGPT account password. Do not send API keys, passwords, database URLs, or signing secrets in chat, source code, frontend code, or logs. Configure `GROQ_API_KEY` and `AUTH_TOKEN_SECRET` in Render's environment settings. The default Groq model is `openai/gpt-oss-20b`; model availability, free-tier eligibility, and provider limits depend on your Groq organization and must be checked in its console. There is no password for the model.

## Security limitations to review

The wrapper requires a bearer token specifically on `/recommend`; other legacy routes in `app.py` have not been comprehensively protected by this change. `app.py` invokes `db.create_all()` during import. Use database migrations for future schema changes and disable/remove destructive routes such as `/reset-db-now` before production exposure.
