# Security Policy

Modernize AI is an agent that rewrites source code and runs tests inside a sandbox, so
bypasses of its safety controls are treated as vulnerabilities. The design is described in the
[security model](README.md#security-model) section of the README.

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | yes |
| < 1.0 | no |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

- Use GitHub's private vulnerability reporting:
  **Security → Report a vulnerability** on
  <https://github.com/bharatkumar00797/modernize-ai-legacy-code/security>.
- Include the affected version or commit, a description of the impact, and steps or a proof of
  concept to reproduce it.

You can expect an acknowledgement within 3 working days and a status update within 10 working
days. Once a fix is released we are happy to credit you in the changelog unless you prefer to
stay anonymous.

## Scope

In scope:

- A transform or model diff that lands outside the allowlist, or that writes to a protected
  path (`tests/`, `test_*.py`, `conftest.py`, pytest config, `modernize.json`).
- Escaping the sandbox (shell execution, network access when isolation is enabled, symlink /
  path traversal out of the workspace, reading `.env` or other non-allowlisted extensions).
- Introducing `eval` / `exec` / `pickle` / `shell=True` smells that the dangerous-pattern gate
  should have rejected.
- Prompt / source injection that causes an unintended kept transform (beyond picking a wrong
  ranking that the test gate then reverts).
- Authentication or authorization bypass in the REST API, a real provider in public-demo mode,
  or seeing another caller's runs.
- Bypassing rate limits or the request body cap; XSS or CSP bypass in the dashboard.
- Secrets exposed in API responses, logs or the published Docker image.

Out of scope:

- Choosing a suboptimal transform ranking when the test gate still reverts the bad step (model
  ranking is advisory; the gate is the safety boundary).
- Shared public-demo identity when `MODERNIZE_TRUST_PROXY` is unset behind a reverse proxy.
- In-memory state loss across restarts or serverless instances (documented caveat).
- Residual smells the agent deliberately leaves (e.g. `pickle.loads`, missing type hints).
- Vulnerabilities in third-party platforms or dependencies without a demonstrated impact on
  Modernize AI.
