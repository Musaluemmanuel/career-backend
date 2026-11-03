# Test guide and feedback

Run tests with:

```bash
python -m unittest discover -s tests -v
```

The focused suite checks the secured entry point's missing-token rejection, account binding, email mismatch, unverified-user rejection, token issuance, quota behavior (three allowed and the fourth returns `402 funding_required`), and OPTIONS preflight behavior. It uses in-memory SQLite and a stub recommendation handler; it makes no provider calls and sends no email.

After deployment, verify in staging: successful sign-in returns an access token; the browser attaches it; three calls enter the recommendation handler; a fourth returns the funding response; usage remains at three; and OPTIONS does not use a trial. Check the Render and provider consoles separately for live DB/model access.

The quota caps HTTP recommendation attempts, not internal Groq calls/retries made for one admitted attempt. Groq account limits and any costs remain separate.

Frontend integration is required: `login.html` must save the returned `access_token`, and the dashboard's recommendation request must send `Authorization: Bearer <token>`. Until that frontend update deploys, its requests will receive HTTP 401.
