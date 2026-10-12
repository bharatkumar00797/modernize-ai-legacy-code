"""Strict unified-diff applier for model-proposed edits (no fuzz, no offsets)."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
MAX_PATCH_LINES = 2_000


class PatchError(ValueError):
    """The diff is malformed, targets another file or does not apply exactly."""


def _strip_prefix(name: str) -> str:
    name = name.split("\t", 1)[0].strip()
    return name[2:] if name.startswith(("a/", "b/")) else name


def apply_unified_diff(source: str, diff: str, path: str) -> str:
    lines = diff.splitlines()
    if len(lines) > MAX_PATCH_LINES:
        raise PatchError("diff is too large")
    old = source.splitlines(keepends=True)
    out: list[str] = []
    pos = 0
    i = 0
    hunks = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(("--- ", "+++ ")):
            name = _strip_prefix(line[4:])
            if name not in {path, "/dev/null"} or name == "/dev/null":
                raise PatchError(f"diff targets {name!r}, expected {path!r}")
            i += 1
            continue
        if line.startswith(("diff ", "index ")):
            i += 1
            continue
        match = _HUNK.match(line)
        if not match:
            if line.strip():
                raise PatchError(f"unexpected line outside a hunk: {line[:60]!r}")
            i += 1
            continue
        hunks += 1
        start = int(match.group(1)) - 1 if int(match.group(2) or "1") else int(match.group(1))
        if start < pos or start > len(old):
            raise PatchError("hunks overlap or start beyond the end of the file")
        out.extend(old[pos:start])
        pos = start
        i += 1
        while i < len(lines) and not lines[i].startswith(("@@", "--- ", "diff ")):
            body = lines[i]
            tag, text = (body[:1], body[1:]) if body else (" ", "")
            if tag == "\\":
                i += 1
                continue
            if tag in {" ", "-"}:
                if pos >= len(old) or old[pos].rstrip("\r\n") != text:
                    raise PatchError(f"context mismatch at line {pos + 1}")
                if tag == " ":
                    out.append(old[pos])
                pos += 1
            elif tag == "+":
                out.append(text + "\n")
            else:
                raise PatchError(f"invalid hunk line: {body[:60]!r}")
            i += 1
    if hunks == 0:
        raise PatchError("diff contains no hunks")
    out.extend(old[pos:])
    return "".join(out)
