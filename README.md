# CareerPath AI Backend

The CareerPath AI backend is a Django API for account management, career
recommendations, conversational guidance, and report delivery. The
recommendation and chat logic is shared from `utils/`.

## Requirements

- Python 3.10 or later
- Credentials for the external services you plan to use
- PostgreSQL for production, or SQLite for local development

## Local setup

From the backend directory, create and activate a virtual environment and
install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create a private `.env` file in this directory:

```dotenv
DJANGO_SECRET_KEY=replace_with_a_private_random_value
AUTH_TOKEN_SECRET=replace_with_a_separate_private_random_value
# Optional; defaults to 604800 seconds (7 days).
AUTH_TOKEN_TTL_SECONDS=604800
GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
MAIL_USERNAME=your_email_address
MAIL_PASSWORD=your_email_app_password
# Optional; SQLite is used when DATABASE_URL is not set.
DATABASE_URL=sqlite:///local_test.db
```

Groq supports text chat and recommendations. Gemini enables document, image,
and video analysis. SMTP credentials are required for verification and report
emails. Do not commit `.env` or real credentials.
Successful verified sign-in returns a signed bearer token. Set
`AUTH_TOKEN_SECRET` to a long, random value in production; recommendation
requests require that token and each verified account is limited to three
lifetime requests. The attempt counter is stored in `recommendation_usage`;
failed AI generation still consumes an attempt. A fourth request returns
HTTP 402 with `code: funding_required`.

Initialize the database and start the development server:

```powershell
python manage.py migrate
python manage.py runserver 0.0.0.0:5001
```

The API listens on port `5001` by default. `PORT` can override the port.
`DATABASE_URL` accepts SQLite or PostgreSQL URLs.

## Existing database migration

The Django models map to the existing `user` and `student_log` tables. For a
database created by the previous Flask service, the deployment command uses
`migrate --fake-initial` to record the initial Django migration when those
tables already exist, preserving their data. Review database backups and
migration state before deploying to a production database.

## API routes

- `POST /register`, `/verify`, `/login`, `/google-login`
- `POST /resend-code`, `/forgot-password`
- `POST /change-username`, `/change-password`, `/delete-account`
- `GET|POST /history`, `POST /save-history`
- `POST /chat`, `POST /recommend`
- `POST /scrape`, `POST /send-report`

Chat attachments can include PDF, JPEG, PNG, WebP, MP4, MOV, or WebM. Up to
three files are accepted per message, with a 5 MB limit per file. Image and
document analysis requires Gemini credentials.
`/recommend` requires `Authorization: Bearer <access_token>` from a verified
sign-in response. Tokens expire after `AUTH_TOKEN_TTL_SECONDS`.

## Tests and checks

```powershell
python manage.py check
python manage.py test
python -m unittest discover -s tests -v
```

The Django tests cover API behavior and upload validation. The `unittest`
suite covers AI guidance and course catalogue utilities.

## Deployment

The included `Procfile` and `Dockerfile` run migrations and launch Gunicorn
with `careerpath.wsgi:application`. Configure `DJANGO_SECRET_KEY`, database,
AI, and SMTP settings in the deployment environment. Do not expose the Django
development server on a production deployment.
