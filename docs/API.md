# API reference

Base URL: your deployed Flask service URL. JSON endpoints expect `Content-Type: application/json`.

## Health check

`GET /` returns a simple success message. This does not verify that every upstream AI provider is functioning.

## Sign-in and bearer token

The production Procfile starts `secured_app:app`. On successful verified password sign-in (`POST /login`) or Google sign-in (`POST /google-login`), the wrapper adds these fields to the existing success response when `AUTH_TOKEN_SECRET` is configured:

```json
{
  "access_token": "<signed-token>",
  "token_type": "Bearer",
  "expires_in": 604800
}
```

The token is signed by the server, identifies the database user ID, and expires after `AUTH_TOKEN_TTL_SECONDS` (default: seven days). The client must store it safely and send it in the `Authorization` header. Sign-in response shapes otherwise remain as implemented by `app.py`.

## Recommendations

`POST /recommend` requires a valid bearer token for an existing verified account. The request body includes the user's interest and grades. Any supplied email must match the signed-in user's email; the server sets the email and name from the authenticated account before invoking the existing route.

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

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

Responses include the existing recommendation result. Errors from the auth wrapper:

- `401 authentication_required` — bearer token absent.
- `401 invalid_token` — signature invalid, expired, or account missing.
- `403 account_not_verified` — the account is not verified.
- `403 account_mismatch` — request email differs from the authenticated account.
- `503 auth_not_configured` — server has no `AUTH_TOKEN_SECRET`/`SECRET_KEY`.

There is no per-user recommendation quota. OPTIONS preflight requests are allowed through. The front end must attach the returned token to recommendation requests or the API will return `401`.

## Legacy routes

Registration, verification, saved-history, account settings, scraping, and email routes are implemented in `app.py`. Review each route's current authorization and validation before exposing sensitive operations; the wrapper currently applies bearer-token authentication only to `/recommend`.
