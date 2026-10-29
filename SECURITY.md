# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.
Report privately through GitHub: **Security** tab → **Report a vulnerability**
(<https://github.com/knightchrism/census-pipeline/security/advisories/new>).

Include what you found, how to reproduce it, and the impact you expect. You should get an acknowledgement
within 5 business days and a status update at least every 14 days until it is resolved. Fixes are released
on `main`; there are no maintained older versions.

## Scope

In scope: code, workflows, container/Kubernetes manifests and documentation in this repository.
Out of scope: the upstream public data sources and third-party services this project reads from.

## Handling secrets

No credentials belong in this repository. `.env` files are git-ignored, CI runs a gitleaks secret scan on every push
and pull request, and a `pre-commit` config (`pre-commit install`) blocks secrets and private keys before they are committed.
If you believe a secret was exposed, report it as above so it can be rotated.
