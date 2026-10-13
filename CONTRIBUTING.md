# Contributing to Modernize AI

Thanks for your interest in improving this legacy-code modernization agent. Bug reports,
new smell detectors or transforms, packaged examples, documentation fixes and pull requests
are all welcome.

## Development setup

Requirements: Python 3.11 to 3.13, `git`. Docker is optional (only for the container checks).

```bash
git clone https://github.com/bharatkumar00797/modernize-ai-legacy-code.git
cd modernize-ai-legacy-code
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Everything runs offline with the default `mock` provider, so no API key is needed for
development or tests. To try a real model, copy `.env.example` to `.env` and set
`MODERNIZE_PROVIDER` / `MODERNIZE_BASE_URL` / `MODERNIZE_API_KEY` / `MODERNIZE_MODEL`.
Never commit `.env` or any key.

## Checks

Run these before opening a pull request; CI runs the same commands on Python 3.11, 3.12 and 3.13.

```bash
ruff check .            # lint
ruff format .           # format (CI uses: ruff format --check .)
mypy                    # strict type checking of src/modernize
pytest                  # full test suite, offline
```

Optional end-to-end checks:

```bash
modernize run py2-style-utils
modernize run insecure-config
modernize run old-api-helpers
modernize serve --dev                               # dashboard on http://127.0.0.1:8000
python scripts/smoke_test.py http://127.0.0.1:8000  # all 3 examples
docker compose up --build --wait                    # hardened container
```

## Guidelines

- Keep the mock provider the default: every feature must work without network access or keys.
- New code is fully typed (`mypy --strict` must pass) and comes with tests.
- Security decisions stay deterministic: the model only ranks allowlisted transform ids (or,
  when `MODERNIZE_ALLOW_MODEL_DIFFS` is on, proposes a unified diff that must apply exactly).
  The test gate decides what is kept. Never let the model invent a transform kind.
- Source snippets fed to the model must stay wrapped in `<untrusted-data>` with delimiter
  look-alikes defanged (`<` escaped).
- Transforms are pure `source -> source` edits on exact token/AST spans; be conservative and
  no-op when unsure. Document the behavioral risk on every new transform.
- The sandbox remains the only place code is written or tests are run. Protected paths
  (`tests/`, `test_*.py`, `conftest.py`, pytest config, `modernize.json`) stay read-only.
- Anything touching auth, access modes, rate limiting or the body cap needs a test proving the
  control holds (see `tests/test_api.py`).
- Adding an example: ship it under `src/modernize/examples/<id>/` with `modernize.json` and
  cover it in `scripts/smoke_test.py` plus an agent test.

## Commit style

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(transforms): convert simple %-formatting to f-strings
fix(sandbox): parse results only from the last short-summary block
docs: document the test-gate keep/revert loop
test: cover percent_format rejection on old-api-helpers
ci: smoke-test the Docker image
```

Keep commits small and focused, write the subject in the imperative mood, and update
`CHANGELOG.md` under **Unreleased** for user-visible changes.

## Reporting security issues

Please do not open public issues for vulnerabilities; see [SECURITY.md](SECURITY.md).
