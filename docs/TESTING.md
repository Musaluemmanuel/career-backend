# Test feedback checklist

Run locally with `python -m unittest discover -s tests -v`.

The test module aims to verify that the production security wrapper:

- rejects missing bearer credentials;
- rejects unverified accounts and mismatched caller-supplied emails;
- uses the signed-in database account as the recommendation identity;
- issues a signed access token on successful login responses; and
- allows more than three calls, confirming the quota was removed.

Tests use an in-memory SQLite database and replace the recommendation view with a local JSON echo. No real provider requests or email sends should occur. A Render/Groq integration smoke test is still needed after deploy.

Important current integration feedback: the frontend repository's `login.html` and `index.html` currently do not persist/attach the bearer token. While the backend requires it on `/recommend`, the recommendation flow will return `401` until the frontend stores `access_token` from successful `/login` or `/google-login` and sends `Authorization: Bearer ...`. Token persistence, API authorization, and all other protected routes should be addressed before treating the full stack as production-ready.
