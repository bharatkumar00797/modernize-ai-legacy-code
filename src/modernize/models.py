"""Strict data models shared by the analyzer, agent and report."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT = 2_000
MAX_SNIPPET = 4_000

ShortText = Annotated[str, Field(max_length=MAX_TEXT)]
RelPath = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_./-]+$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SmellKind(StrEnum):
    PY2_PRINT = "py2_print"
    PY2_XRANGE = "py2_xrange"
    PY2_UNICODE = "py2_unicode"
    PY2_DICT_ITER = "py2_dict_iter"
    PERCENT_FORMAT = "percent_format"
    BARE_EXCEPT = "bare_except"
    MUTABLE_DEFAULT = "mutable_default"
    EVAL_USAGE = "eval_usage"
    EXEC_USAGE = "exec_usage"
    INSECURE_DESERIALIZATION = "insecure_deserialization"
    ASSERT_VALIDATION = "assert_validation"
    SHELL_INJECTION = "shell_injection"
    DEPRECATED_API = "deprecated_api"
    DEPRECATED_MODULE = "deprecated_module"
    OBJECT_BASE = "explicit_object_base"
    MISSING_TYPE_HINTS = "missing_type_hints"
    MISSING_FUTURE_ANNOTATIONS = "missing_future_annotations"
    SYNTAX_ERROR = "syntax_error"


class TransformKind(StrEnum):
    """Allowlisted transform ids. Nothing outside this enum can ever be applied."""

    PY2_PRINT_TO_FUNCTION = "py2_print_to_function"
    XRANGE_TO_RANGE = "xrange_to_range"
    UNICODE_TO_STR = "unicode_to_str"
    DICT_ITER_TO_VIEW = "dict_iter_to_view"
    BARE_EXCEPT_TO_EXCEPTION = "bare_except_to_exception"
    MUTABLE_DEFAULT_TO_NONE = "mutable_default_to_none"
    EVAL_TO_AST_LITERAL = "eval_to_ast_literal"
    PERCENT_FORMAT_TO_FSTRING = "percent_format_to_fstring"
    DEPRECATED_API_ALIASES = "deprecated_api_aliases"
    DROP_OBJECT_BASE = "drop_object_base"
    ADD_FUTURE_ANNOTATIONS = "add_future_annotations"
    MODEL_DIFF = "model_diff"  # only reachable when MODERNIZE_ALLOW_MODEL_DIFFS=true


class Smell(_Strict):
    kind: SmellKind
    path: RelPath
    line: int = Field(ge=1, le=1_000_000)
    message: ShortText
    severity: Severity
    snippet: Annotated[str, Field(max_length=300)] = ""
    fixable: bool = False


class PlanStep(_Strict):
    index: int = Field(ge=1, le=1_000)
    transform: TransformKind
    path: RelPath
    addresses: list[SmellKind] = Field(default_factory=list, max_length=32)
    chosen_by: Literal["model", "fallback", "mock"] = "fallback"
    rationale: ShortText = ""


class SuiteSnapshot(_Strict):
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    green: bool
    timed_out: bool = False


class StepResult(_Strict):
    step: PlanStep
    status: Literal["kept", "rejected", "noop"]
    reason: ShortText
    tests_before: SuiteSnapshot | None = None
    tests_after: SuiteSnapshot | None = None
    regressions: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=200
    )
    fixed_tests: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=200
    )
    before_snippet: Annotated[str, Field(max_length=MAX_SNIPPET)] = ""
    after_snippet: Annotated[str, Field(max_length=MAX_SNIPPET)] = ""


class ModernizationReport(_Strict):
    target: Annotated[str, Field(max_length=255)]
    provider: Annotated[str, Field(max_length=64)]
    files: list[RelPath] = Field(max_length=200)
    smells_before: list[Smell] = Field(max_length=2_000)
    smells_after: list[Smell] = Field(max_length=2_000)
    steps: list[StepResult] = Field(max_length=1_000)
    baseline: SuiteSnapshot
    final: SuiteSnapshot
    residual_risks: list[ShortText] = Field(default_factory=list, max_length=200)
    notes: list[ShortText] = Field(default_factory=list, max_length=200)
    diff: Annotated[str, Field(max_length=200_000)] = ""

    @property
    def kept(self) -> list[StepResult]:
        return [s for s in self.steps if s.status == "kept"]

    @property
    def rejected(self) -> list[StepResult]:
        return [s for s in self.steps if s.status == "rejected"]
