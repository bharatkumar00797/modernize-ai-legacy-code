"""Deterministic offline provider: a fixed, reviewed policy order."""

from __future__ import annotations

import json

from ..models import TransformKind
from .base import RankRequest

#: Syntax-level Python 2 fixes first (they unblock parsing and AST checks), then
#: security/correctness fixes, then cosmetic modernizations.
POLICY: list[TransformKind] = [
    TransformKind.PY2_PRINT_TO_FUNCTION,
    TransformKind.XRANGE_TO_RANGE,
    TransformKind.UNICODE_TO_STR,
    TransformKind.DICT_ITER_TO_VIEW,
    TransformKind.DEPRECATED_API_ALIASES,
    TransformKind.EVAL_TO_AST_LITERAL,
    TransformKind.MUTABLE_DEFAULT_TO_NONE,
    TransformKind.BARE_EXCEPT_TO_EXCEPTION,
    TransformKind.PERCENT_FORMAT_TO_FSTRING,
    TransformKind.DROP_OBJECT_BASE,
    TransformKind.ADD_FUTURE_ANNOTATIONS,
]


class MockProvider:
    name = "mock"

    def rank(self, request: RankRequest) -> str:
        ordered = [k for k in POLICY if k in request.candidates]
        return json.dumps(
            {
                "transforms": [k.value for k in ordered],
                "rationale": "fixed policy: unblock parsing, then security, then style",
            }
        )
