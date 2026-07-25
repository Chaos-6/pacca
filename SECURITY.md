# Security Policy

## Scope of this policy

PACCA is published as an **evaluation and portfolio artifact**, not as a HIPAA-certified production system. The repository ships with synthetic patient data only. No real PHI is processed by, transmitted through, or stored by the demo deployments associated with this repository.

That said, several classes of vulnerability are relevant to this project and we want to hear about them.

## In scope

We welcome reports about:

- **Authentication and authorization** — JWT handling, password hashing, session management, privilege escalation.
- **Data exposure paths** — any code path that could leak case data, decisions, or rationales across user boundaries.
- **Input handling** — injection vulnerabilities (SQL, prompt injection that bypasses agent guardrails, command injection), unsafe deserialization, SSRF.
- **Dependency vulnerabilities** — CVEs in dependencies that affect this codebase as deployed.
- **CI / supply-chain** — workflow misconfigurations that could be exploited via PRs from forks.
- **Container / deployment** — Dockerfile or compose issues that could compromise hosts in a typical deployment.
- **LLM-specific issues** — prompt-injection attacks that could cause the system to leak system prompts, fabricate guideline citations, or route around the confidence-gating logic.

## Out of scope

- Anything requiring access to a real PHI dataset (none exists in this repo).
- Theoretical issues without a demonstrated exploit path.
- Self-XSS or social-engineering scenarios that require user cooperation.
- Automated scanner output without manual verification.
- Issues in third-party services (Anthropic API, hosting providers) — please report those directly to the relevant vendor.

## How to report

**Email:** drdgreed@gmail.com

Please include:

1. A clear description of the vulnerability.
2. The minimum reproduction steps.
3. The impact you believe it has.
4. Any suggested mitigation.

If your finding involves a working exploit, please include it as a private gist or attached file rather than a link to a public location.

## Response commitment

- **Acknowledgment** within 5 business days of receipt.
- **Initial assessment** within 14 days.
- **Remediation timeline** communicated after assessment, prioritized by severity.

## Responsible disclosure

Please give us a reasonable opportunity to address an issue before public disclosure. We will credit reporters in release notes unless they prefer to remain anonymous.

## Signing-key configuration

The JWT signing key is read from the `SECRET_KEY` environment variable and from
nowhere else — there is no default anywhere in the codebase. It is validated in
the FastAPI startup lifespan, in every environment, before any request is served.

A key is rejected outright if it is missing, shorter than 32 characters, or built
from fewer than 8 distinct characters. When `APP_ENV` is `production` or
`staging`, a key containing a known placeholder marker (`REPLACE-THIS`,
`change-in-production`, `dev-secret-key`, and similar) is also rejected: those
strings are published in this repository, so a deployment using one has a signing
key any reader can forge tokens with. In `development` and `test` a placeholder is
permitted — the test suite must not require a real secret — and is logged at
warning level rather than accepted silently.

`APP_ENV` selects how strict the check is; it cannot switch the check off.

## Production deployment

This repository is not certified for processing real PHI. Anyone deploying PACCA in a setting that handles real protected health information is responsible for: a Business Associate Agreement with the LLM provider, a HIPAA-compliant hosting environment, expanded audit logging, end-to-end encryption review, clinical validation by a qualified medical director, and any additional regulatory obligations applicable to the deployment jurisdiction.
