# API reference

Base URL: your deployed Django API. JSON endpoints use
`Content-Type: application/json`.

## `GET /`

Returns a basic service health response. It does not check external AI-provider
availability.

## Sign-in and bearer token

Successful verified responses from `POST /login`, `/google-login`, and `/verify`
include `access_token`, `token_type`, and `expires_in` when
`AUTH_TOKEN_SECRET` (or the legacy `SECRET_KEY`) is configured. The signed
token identifies the account and expires after `AUTH_TOKEN_TTL_SECONDS`
(default 604800 seconds / seven days). Keep it private.

```json
{
  "access_token": "<signed-token>",
  "token_type": "Bearer",
  "expires_in": 604800
}
```

## `POST /recommend`

Requires a verified account's bearer token. A supplied email must match the
authenticated account; the API binds recommendation ownership to the signed-in
identity.

```http
Authorization: Bearer <access-token>
Content-Type: application/json
```

Example body:

```json
{
  "interest": "computer science and software development",
  "grades": {
    "Math": "A-",
    "English": "B",
    "Kiswahili": "B",
    "Biology": "B+",
    "Physics": "C",
    "Chemistry": "C+",
    "ComputerStudies": "A"
  }
}
```

### Three-attempt lifetime cap

Three admitted recommendation requests are allowed per authenticated account
over its lifetime. Each request consumes one attempt immediately after
authentication, including a request whose AI pipeline later fails. Attempts
persist in `recommendation_usage`. At the limit the AI pipeline is not called,
and a fourth request returns HTTP `402`:

```json
{
  "error": "You have used all 3 complimentary recommendation attempts. Please request funding to continue.",
  "code": "funding_required",
  "limit": 3,
  "funding_message": "Please contact the app administrator to request additional AI usage funding."
}
```

This is not a payment system. Funding approval and counter adjustments require
a secure administrator process; there is no public reset/funding endpoint. The
cap is on endpoint attempts, not internal LLM calls or retries.

### Errors

- `401 authentication_required` — bearer token absent.
- `401 invalid_token` — invalid/expired token or deleted account.
- `403 account_not_verified` — account is not verified.
- `403 account_mismatch` — request email differs from signed-in account.
- `402 funding_required` — three attempts used; request funding.
- `503 auth_not_configured` — signing secret is missing.
- `503 quota_unavailable` — persistent quota check failed; request is denied.

## Other routes

Registration, verification, saved history, account settings, scraping, chat,
and email routes remain available through the Django API. Bearer
authentication and the lifetime usage cap are applied to `/recommend`;
authorization for other account-management routes should be reviewed
separately.
