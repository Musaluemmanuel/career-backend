# API reference

Base URL: your deployed Flask API. JSON endpoints use `Content-Type: application/json`.

## `GET /`
Basic service health response. This does not verify that AI provider requests succeed.

## Sign-in and bearer token

The production entry point (`secured_app:app`) adds `access_token`, `token_type`, and `expires_in` to successful verified responses from `POST /login`, `POST /google-login`, and `POST /verify` when `AUTH_TOKEN_SECRET` is configured. The signed token identifies the database account and expires after `AUTH_TOKEN_TTL_SECONDS` (default 604800 seconds / 7 days). Keep it private.

```json
{"access_token":"<signed-token>","token_type":"Bearer","expires_in":604800}
```

## `POST /recommend`

Requires the verified account's bearer token. The app binds recommendation ownership to the authenticated user; an optional email in the request must match that account.

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

### Three-attempt lifetime cap

Three admitted recommendation requests are allowed per authenticated account over its lifetime. Each request consumes one attempt immediately after authentication, including a request that later fails in the AI pipeline. Attempts persist in PostgreSQL. At the limit the recommendation pipeline is not called, and a fourth request returns HTTP `402`:

```json
{
  "error": "You have used all 3 complimentary recommendation attempts. Please request funding to continue.",
  "code": "funding_required",
  "limit": 3,
  "funding_message": "Please contact the app administrator to request additional AI usage funding."
}
```

This is not a payment system. Funding approval and counter adjustments require a secure administrator process; there is no public reset/funding endpoint. The cap is on endpoint attempts, not the number of internal LLM calls/retries within each admitted request. OPTIONS requests do not consume attempts.

### Errors

- `401 authentication_required` — bearer token absent.
- `401 invalid_token` — invalid/expired token or deleted account.
- `403 account_not_verified` — account is not verified.
- `403 account_mismatch` — request email differs from signed-in account.
- `402 funding_required` — three attempts used; request funding.
- `503 auth_not_configured` — signing secret is missing.
- `503 quota_unavailable` — persistent quota check failed; request is denied.

## Other routes

Registration, verification, saved history, account settings, scraping, and email routes remain defined in `app.py`. This wrapper applies bearer authentication and the usage cap only to `/recommend`. Review authorization of the other routes separately.
