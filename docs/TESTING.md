# Tests and expected feedback

Run:

```bash
python -m unittest discover -s tests -v
```

The unit suite is intended to check missing/invalid authentication, user binding, email mismatch, unverified users, issuance of login tokens, exactly three admitted lifetime requests, and a fourth `402 funding_required` response. It uses isolated SQLite and a stub recommendation handler, so it must not make provider calls or consume tokens.

After deploying, also verify via staging: login returns a token; the browser attaches it; three calls reach the recommendation route; the fourth receives the funding message; the stored count remains three; and OPTIONS preflight does not consume quota.

Limit caveat: this caps recommendation HTTP attempts, not the number of internal LLM calls/retries made during each admitted request. Provider/org limits remain an additional cost control.
