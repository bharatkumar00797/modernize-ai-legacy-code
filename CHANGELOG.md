# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-10-13

First stable release.

### Added

- Closed modernization loop: detect smells → rank allowlisted transforms → apply one
  change → sandbox test gate → keep or revert.
- Token + AST smell detectors (18 kinds) that still work on unparseable Python 2 sources.
- Eleven pure, conservative transforms (print, xrange, unicode, dict iters, bare except,
  mutable defaults, eval→literal_eval, %→f-string, deprecated aliases, drop `object`
  base, add `from __future__ import annotations`) plus an opt-in model unified-diff path.
- Sandboxed workspace: temp copy with symlink refusal, extension allowlist, size caps,
  protected tests/config, fixed `python -m pytest` argv (no shell), scrubbed env, timeout
  + killpg, CPU/FSIZE rlimits, `unshare -rn` when available, output truncation, results
  parsed only from the last short-summary block.
- Pluggable LLM providers: deterministic offline mock (default) + OpenAI-compatible
  (OpenAI / Groq / Ollama) that only ranks allowlisted transform ids; prompts wrap source
  in `<untrusted-data>`; hallucinated ids are filtered out.
- Three packaged examples: `py2-style-utils` (0→5 green), `insecure-config` (3→6 green),
  `old-api-helpers` (2→4 green with a deliberate `%d`→f-string rejection + pickle residual).
- CLI: `modernize examples`, `modernize run`, `modernize serve`, `--version`.
- FastAPI service with runs / events / report.md / patch.diff / examples / transforms,
  OpenAPI docs, API-key / dev / public-demo access modes, rate limits, 64 KB body cap,
  strict CSP and security headers.
- No-build dashboard: smell tables, step timeline, colored diff, Markdown report,
  `#run=&tab=` deep links.
- Hardened multi-stage Docker image, docker compose, Render, Fly.io, Railway and Vercel
  configs, end-to-end smoke test script and Docker smoke job in CI.

[Unreleased]: https://github.com/bharatkumar00797/modernize-ai-legacy-code/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/bharatkumar00797/modernize-ai-legacy-code/releases/tag/v1.0.0
