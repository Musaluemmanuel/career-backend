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

All routes accept the methods listed below. JSON request bodies should use
`Content-Type: application/json`, except multipart chat uploads.

| Route | Method | Purpose |
| --- | --- | --- |
| `/` | `GET` | Basic service health response. |
| `/register` | `POST` | Create an account and send an email verification code. |
| `/verify` | `POST` | Verify an account; may return a bearer token. |
| `/login` | `POST` | Sign in with email or username and password. |
| `/google-login` | `POST` | Verify a Google ID token and sign in. |
| `/resend-code` | `POST` | Send a new verification code. |
| `/forgot-password` | `POST` | Email a temporary password to the account address. |
| `/chat` | `POST` | Generate a chat response; supports multipart attachments. |
| `/recommend` | `POST` | Generate recommendations; requires bearer authentication and consumes one attempt. |
| `/history` | `GET`, `POST` | Read saved reports by username/email (`GET`) or email (`POST`). |
| `/save-history` | `POST` | Append a report to account history. |
| `/change-username` | `POST` | Change the username for an email address. |
| `/change-password` | `POST` | Change a password after checking the current password. |
| `/delete-account` | `POST`, `DELETE` | Permanently delete the account identified by email. |
| `/scrape` | `POST` | Look up an institution course page. |
| `/send-report` | `POST` | Queue a base64-encoded PDF report for email delivery. |

### Chat request limits

Send a JSON object with `messages`, an array of 1–24 `{ "role": "user" |
"assistant", "content": "..." }` entries. Each content string may contain up
to 12,000 characters, with a combined maximum of 24,000 characters. The latest
message must be from the student. The optional `assistant_name` must be 1–32
letters, numbers, spaces, or simple punctuation.

For attachments, use `multipart/form-data` with a `messages` JSON field and
repeat the `attachments` file field. Up to three non-empty PDF, JPEG, PNG,
WebP, MP4, MOV, or WebM files are accepted, each up to 5 MB. File extension,
MIME type, and basic file signature are checked. Attachment analysis requires
the Gemini provider to be configured.

### Report email request

`POST /send-report` expects JSON containing `email` and `pdf_data`; the latter
is a base64 string or data URL for a PDF of no more than 12 MB. The response
uses HTTP `202` when delivery is queued; it does not mean the recipient has
received the email.

## Authorization boundary

Bearer authentication and the three-attempt quota currently protect only
`/recommend`. Other account, history, chat, scraping, and email endpoints do
not consistently require a bearer token. Do not treat this API reference or a
frontend login state as proof that those routes are authorized for the
requesting user. Review and enforce server-side authorization before exposing
sensitive routes to untrusted clients. See the [security policy](../SECURITY.md).

## Error responses

Error objects are not fully standardized: endpoints may return a `message`
field or an `error` field, and recommendation authentication errors also
include a stable `code`. Clients should check the HTTP status and handle
missing optional fields rather than assuming every response has the same
shape.
