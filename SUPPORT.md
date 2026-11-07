# Support

## Setup and operations

Start with the [README](README.md), [API reference](docs/API.md),
[deployment guide](docs/DEPLOYMENT.md), and [testing guide](docs/TESTING.md).

For service operations, provide the service administrator with the approximate
UTC time, affected endpoint or feature, HTTP status, and a short sanitized log
excerpt. Do not send passwords, verification codes, bearer tokens, database
URLs, provider keys, or student records.

## Reporting a problem

Check existing repository discussions or issues first. If the repository issue
tracker is available, include:

- The deployed commit or release, if known.
- The endpoint and HTTP method.
- Expected and actual behavior.
- Sanitized request/response examples and relevant logs.
- Steps to reproduce without production data.

Do not report suspected vulnerabilities in public issues; use the private
process in [SECURITY.md](SECURITY.md).

## Provider and deployment issues

A successful `GET /` confirms only that the basic API route responds. It does
not verify database migrations, email delivery, AI provider credentials, or
quota-table access. Use the deployment checklist and staging checks in
`docs/DEPLOYMENT.md` and `docs/TESTING.md` before escalating.
