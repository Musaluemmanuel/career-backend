# CareerPath AI Backend

The CareerPath AI backend is a Django API for account management, personalized
career and course recommendations, chat, and report delivery. It reuses the
recommendation and chat logic in `utils/`.

## Requirements

- Python 3.11
- Credentials for the external services you plan to use
- PostgreSQL for production or SQLite for local development

## Local setup

From the backend directory, create and activate a virtual environment and
install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create a private `.env` file:

```dotenv
DJANGO_SECRET_KEY=replace_with_a_private_random_value
AUTH_TOKEN_SECRET=replace_with_a_separate_private_random_value
AUTH_TOKEN_TTL_SECONDS=604800
GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
MAIL_USERNAME=your_email_address
MAIL_PASSWORD=your_email_app_password
# Optional locally; SQLite is used when DATABASE_URL is not set.
DATABASE_URL=sqlite:///local_test.db
```

Initialize the database and start the development server:

```powershell
python manage.py migrate
python manage.py runserver 0.0.0.0:5001
```

`DATABASE_URL` accepts SQLite or PostgreSQL URLs. Groq supports text chat and
recommendations; Gemini enables document, image, and video analysis. SMTP
credentials are used for verification and report emails. Never commit `.env`
or real credentials.

## Authentication and recommendation limits

Successful verified sign-in (`/login`, `/google-login`, or `/verify`) returns a
signed bearer token. Set `AUTH_TOKEN_SECRET` to a long, random value in
production. Tokens expire after `AUTH_TOKEN_TTL_SECONDS` (seven days by
default). If the signing secret is unset, recommendation requests fail closed
with `503 auth_not_configured`.

`POST /recommend` requires a bearer token from a verified sign-in response.
Each verified account can start three recommendation requests over its
lifetime. An attempt is reserved before AI generation, so failed provider
requests count; a fourth request returns HTTP `402` with
`code: funding_required`. The counter is stored in `recommendation_usage`.
This is a usage limit, not a payment or funding system; there is no public
counter-reset endpoint.

## API routes

- `POST /register`, `/verify`, `/login`, `/google-login`
- `POST /resend-code`, `/forgot-password`
- `POST /change-username`, `/change-password`, `/delete-account`
- `GET|POST /history`, `POST /save-history`
- `POST /chat`, `POST /recommend`
- `POST /scrape`, `POST /send-report`

Chat accepts up to three PDF, JPEG, PNG, WebP, MP4, MOV, or WebM attachments
with a 5 MB limit per file. Image and document analysis requires Gemini
credentials.

## Existing database migration

The Django models map to the existing `user` and `student_log` tables. For a
database created by the previous Flask service, deployment runs
`migrate --fake-initial` to record the initial Django migration when those
tables already exist, preserving their data. The recommendation quota table is
created by the next Django migration. Back up the production database and
review migration state before deployment.

## Deployment

The `Procfile` and `Dockerfile` run migrations and launch Gunicorn with
`careerpath.wsgi:application`. On Render, configure `DATABASE_URL`,
`DJANGO_SECRET_KEY`, `AUTH_TOKEN_SECRET`, and the AI/email provider credentials
in the service environment. The service binds to the provided `PORT`.

## Tests

```powershell
python manage.py check
python manage.py test
python -m unittest discover -s tests -v
```

The Django tests cover API behavior, token issuance, authenticated
recommendations, quota enforcement, and upload validation. The `unittest`
suite covers AI guidance and course catalogue utilities. Tests stub provider
calls; they do not prove live credentials or provider availability.

## Provider secrets and cost

This application calls Groq's API; it does not use a ChatGPT account password.
The default model is `openai/gpt-oss-20b`, an open-weight model served by Groq.
Availability, usage limits, and pricing depend on the provider account. Keep
API keys, passwords, database URLs, and signing secrets in the service's
environment settings, never in frontend code or logs.

See the [API guide](docs/API.md), [deployment guide](docs/DEPLOYMENT.md), and
[test guide](docs/TESTING.md).
