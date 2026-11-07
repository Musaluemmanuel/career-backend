# Security Policy

## Supported versions

Security fixes are intended for the current default branch. Older deployments
may not receive fixes; verify the deployed revision with the service
maintainer.

## Reporting a vulnerability

Do not publish credentials, exploitable details, or personal data in a public
issue. Use a private repository security advisory if that feature is
available. Otherwise, contact a repository maintainer privately and include
the affected endpoint or component, impact, reproduction context, and a safe
way to follow up. Do not include live tokens, database URLs, or user records.

## Operational security

- Keep Django, bearer-token, provider, SMTP, and database secrets in the
  deployment environment, never source control or client-side code.
- Use HTTPS for deployed API and frontend origins; restrict CORS to intended
  origins. The current middleware reflects origins when
  `CORS_ALLOWED_ORIGINS` is unset, so configure an explicit production allowlist.
- The signed bearer token and quota protect `/recommend`. Other API routes do
  not consistently enforce per-account authorization; review the explicit
  [API authorization boundary](docs/API.md#authorization-boundary) before
  exposing those routes.
- Back up production data and review migrations before deployment.
- Avoid logging passwords, verification codes, access tokens, or request
  bodies containing personal information.
- Keep provider calls, quotas, SMTP, and database access under server-side
  configuration; do not expose credentials in exception messages.

Report third-party dependency concerns with the package name, affected
version, and impact. Follow the private reporting process above.
