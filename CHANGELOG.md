# Changelog

Notable backend changes are recorded here. Entries are grouped by release;
the repository does not currently use formal version tags.

## Unreleased

- Run the API through Django and preserve the existing account and student-log
  tables during initial migration.
- Issue signed bearer tokens after verified sign-in and require them for
  recommendation requests.
- Enforce and persist a three-attempt recommendation limit per account.
- Document API behavior, deployment, testing, security boundaries, and
  contributor workflows.
