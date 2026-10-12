# modernize-ai-legacy-code

**An AI agent that modernizes legacy Python code while preserving behavior.**

Deterministic rules find code smells (Python 2 syntax, bare `except`, mutable defaults,
`eval`, `%`-formatting, deprecated stdlib APIs, ...) and propose *allowlisted* transforms.
An LLM ranks those candidates (or, when enabled, proposes a unified diff), and every change
is applied in a copy-on-write sandbox and **kept only if the project's tests stay green**.
Originals are never touched.

> Status: early development. Works fully offline with the built-in deterministic mock
> provider - no API key required.

## Quickstart

```bash
pip install -e ".[dev]"
modernize examples
modernize run py2-style-utils --out modernize-output/py2
```

## How it works (short)

1. **Ingest** a packaged example or a project directory (tests under `tests/`).
2. **Analyze** each target module for smells (AST + tokens, so even Python 2 files work).
3. **Plan** candidate transforms from the smells; the provider ranks them (model output is
   filtered to the allowlist, with rule order as the fallback).
4. **Apply** one focused transform at a time inside a sandbox copy.
5. **Verify** by running the tests before and after; any regression reverts the step.
6. **Report** smells, kept/rejected steps, before/after snippets and residual risks.

## License

MIT
