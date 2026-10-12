"""Defensive parsing of model replies: anything off-allowlist is dropped."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..models import TransformKind

MAX_DIFF_CHARS = 20_000
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_OBJECT = re.compile(r"\{.*\}", re.S)


@dataclass
class ModelChoice:
    transforms: list[TransformKind]
    diff: str | None = None
    rationale: str = ""
    dropped: list[str] = field(default_factory=list)


def _load_object(raw: str) -> dict[str, object] | None:
    for pattern in (_FENCE, _OBJECT):
        match = pattern.search(raw)
        if not match:
            continue
        try:
            obj = json.loads(match.group(1) if pattern is _FENCE else match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def parse_reply(
    raw: str, allowed: list[TransformKind], *, allow_diff: bool = False
) -> ModelChoice | None:
    """Return the validated choice, or None when the reply is unusable."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 100_000:
        return None
    obj = _load_object(raw)
    if obj is None:
        return None
    rationale = str(obj.get("rationale", ""))[:500]
    diff = obj.get("diff")
    if allow_diff and isinstance(diff, str) and diff.strip() and len(diff) <= MAX_DIFF_CHARS:
        return ModelChoice(transforms=[], diff=diff, rationale=rationale)
    items = obj.get("transforms")
    if not isinstance(items, list):
        return None
    chosen: list[TransformKind] = []
    dropped: list[str] = []
    allowed_values = {k.value: k for k in allowed}
    for item in items[:50]:
        key = item if isinstance(item, str) else repr(item)
        kind = allowed_values.get(key.strip()) if isinstance(item, str) else None
        if kind is None or kind in chosen:
            dropped.append(key[:80])
        else:
            chosen.append(kind)
    if not chosen:
        return None
    return ModelChoice(transforms=chosen, rationale=rationale, dropped=dropped)
