# CareerPath AI Backend

Flask API for personalized Kenyan course and career recommendations. It stores users and recommendation history in PostgreSQL (SQLite fallback for local development), generates recommendations through Groq, verifies university links, and serves the API with Gunicorn on Render.

## Repository contents

- `app.py` — Flask routes, models, database configuration, recommendation pipeline.
- `secured_app.py` — production entry point that issues signed bearer tokens after verified sign-in, requires them for `/recommend`, and selects one Groq model for Groq requests.
- `utils/ai_engines.py` — course eligibility, validation, and AI recommendation generation.
- `utils/web_scraper.py` — university link lookup and URL cache.
- `requirements.txt`, `Procfile`, `Dockerfile` — Python dependencies and deployment configuration.
- `docs/API.md`, `docs/DEPLOYMENT.md` — API and deployment instructions.
- `tests/` — standard-library unit tests for the security wrapper.

## Run locally

Python 3.11 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

Set environment variables (use a local/test database and never commit secrets):

```bash
export DATABASE_URL='sqlite:///local_test.db'
export AUTH_TOKEN_SECRET='replace-with-a-long-random-secret'
export GROQ_API_KEY='your-groq-api-key'
export GROQ_MODEL='openai/gpt-oss-20b'
```

Run the same secured entry point used by the Procfile:

```bash
gunicorn secured_app:app --bind 0.0.0.0:${PORT:-10000} --timeout 300
```

The root endpoint is `GET /`. Authenticated users must send `Authorization: Bearer <access_token>` on `POST /recommend`. Details are in [API documentation](docs/API.md).

## Tests

Run the focused security-wrapper tests using Python's standard library:

```bash
python -m unittest discover -s tests -v
```

The tests use an isolated in-memory SQLite database and mock the recommendation handler; they do not call Groq or send email. They do not replace integration testing against a staging Render database or a Groq account with the selected model enabled.

## Deployment

Render must start `secured_app:app` (the Procfile is configured for this) and set `AUTH_TOKEN_SECRET`, `DATABASE_URL`, and `GROQ_API_KEY`. `GROQ_MODEL` is optional; the default is `openai/gpt-oss-20b`. Check [deployment guidance](docs/DEPLOYMENT.md) before deploying.

## Cost and security notes

- The recommendation quota has been removed. The bearer-token requirement authenticates `/recommend`; it does not cap AI usage.
- A Groq model being usable without charge is subject to Groq's current plan, model availability, and organization limits. This repository cannot guarantee free or unlimited inference. Confirm the model and limits in your Groq console.
- The current web frontend must preserve the `access_token` from login/Google login and send it on `/recommend`. Until that client integration is present, authenticated recommendations will receive `401`.
- Only `/recommend` is protected by `secured_app.py`'s bearer-token hook. Other legacy API routes are not made authenticated by this wrapper.
- `db.create_all()` currently runs during import in `app.py`; use migrations for production schema changes and do not expose destructive database-reset routes publicly.
- Never put API keys, database URLs, or signing secrets in source control or client-side code.
