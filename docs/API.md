# API reference

Base URL: the deployed Flask API. JSON endpoints use `Content-Type: application/json`.

## `GET /`
Basic service health response. This does not verify that AI provider requests succeed.

## Login and access token

The production entry point (`secured_app:app`) adds `access_token`, `token_type`, and `expires_in` to successful verified responses from `POST /login` and `POST /google-login`, when `AUTH_TOKEN_SECRET` is configured. The signed token identifies the database account and expires after `AUTH_TOKEN_TTL_SECONDS` (default 604800 seconds / 7 days). Keep it private.

```json
{"access_token":"<signed-token>","token_type":"Bearer","expires_in":604800}
```

## `POST /recommend`

Requires a verified account's bearer token. The app binds recommendation ownership to the user encoded in the token; a caller-supplied email must match that account.

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

Example body:

```json
{
  "interest": "computer science and software development",
  "grades": {"Math":"A-","English":"B","Kiswahili":"B","Biology":"B+","Physics":"C","Chemistry":"C+","ComputerStudies":"A"}
}
```

### Usage cap

Three lifetime recommendation **requests** are allowed per authenticated account. Each request consumes one attempt as soon as it passes authentication and is admitted, including a request that later fails in the AI pipeline. Attempts are stored persistently in PostgreSQL. At the limit the API does not call the recommendation pipeline; it returns HTTP `402`:

```json
{
  "error": "You have used all 3 complimentary recommendation attempts. Please request funding to continue.",
  "code": "funding_required",
  "limit": 3,
  "funding_message": "Please contact the app administrator to request additional AI usage funding."
}
```

This is a usage gate only; it does not collect payment or automatically grant additional attempts. Funding approval and counter reset must be performed by an administrator. The cap applies to endpoint requests; internal provider retries within an admitted request are not individually counted.

### Error responses

- `401 authentication_required` — bearer token absent.
- `401 invalid_token` — invalid/expired token or deleted account.
- `403 account_not_verified` — account is not verified.
- `403 account_mismatch` — request email differs from signed-in account.
- `402 funding_required` — three attempts used; request funding.
- `503 auth_not_configured` — signing secret is missing.
- `503 quota_unavailable` — quota persistence failed; request is denied.

OPTIONS preflight requests are excluded from the quota.

## Other routes

Registration, verification, saved history, account settings, scraping, and email routes remain defined in `app.py`. This wrapper only applies token authentication and quota to `/recommend`. Review authorization of other routes separately.
