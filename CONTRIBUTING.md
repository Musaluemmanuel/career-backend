# Contributing

Thank you for helping improve CareerPath AI. The backend is a Django API with
legacy Flask modules retained in the repository; production starts through
`careerpath.wsgi:application`.

## Development setup

Use Python 3.11. From the backend root, create a virtual environment and
install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set local-only values in an untracked `.env`, or set environment variables for
the current terminal. Do not use production credentials or a production
database for tests. Initialize a local database with `python manage.py migrate`.

## Checks

Run the relevant checks before opening a change:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python -m unittest discover -s tests -v
```

The legacy Flask security tests are in `tests/`. They may require the provider
and database environment variables described in the README; use test-only
values and an isolated SQLite database. Do not run destructive migrations or
data scripts against production.

## Database and API changes

- Update Django models and include a migration for schema changes.
- Preserve compatibility with existing `user` and `student_log` tables.
- Test upgrades from an existing database; `--fake-initial` is intended only
  for adopting the legacy initial tables, not as a substitute for migrations.
- Update `docs/API.md` when routes, methods, request limits, authentication, or
  response shapes change.
- Coordinate frontend-visible API changes with the
  [frontend repository](https://github.com/aicareerrecommender-alt/career-frontend).
- Never weaken authentication or quota behavior without an explicit product
  and security review.

## Pull requests

Describe the motivation, behavior change, migration implications, and checks
performed. Include expected environment changes, deployment risks, and any
tests not run. Keep secrets, live user data, generated database files, and
unrelated local changes out of the pull request.
