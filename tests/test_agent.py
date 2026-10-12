from __future__ import annotations

import json
from pathlib import Path

import pytest

from modernize.agent import Agent, PatchError, apply_unified_diff
from modernize.config import Settings
from modernize.ingest import load_project
from modernize.llm import MockProvider, RankRequest
from modernize.models import SmellKind, TransformKind
from modernize.report import render_markdown


class ScriptedProvider:
    """Replays canned replies (then repeats the last one)."""

    def __init__(self, replies: list[str], name: str = "openai") -> None:
        self.replies = replies
        self.name = name
        self.requests: list[RankRequest] = []

    def rank(self, request: RankRequest) -> str:
        self.requests.append(request)
        return self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]


class BrokenProvider:
    name = "openai"

    def rank(self, request: RankRequest) -> str:
        raise RuntimeError("network down")


@pytest.fixture(scope="module")
def mock_runs() -> dict[str, object]:
    settings = Settings(isolate_network=False)
    return {
        ex: Agent(MockProvider(), settings).run(load_project(ex))
        for ex in ["py2-style-utils", "insecure-config", "old-api-helpers"]
    }


def test_py2_example_goes_red_to_green(mock_runs: dict[str, object]) -> None:
    result = mock_runs["py2-style-utils"]
    report = result.report  # type: ignore[attr-defined]
    assert not report.baseline.green and report.final.green
    kept = [s.step.transform for s in report.kept]
    assert kept[0] == TransformKind.PY2_PRINT_TO_FUNCTION
    assert {TransformKind.XRANGE_TO_RANGE, TransformKind.UNICODE_TO_STR} <= set(kept)
    after = {s.kind for s in report.smells_after}
    assert SmellKind.PY2_PRINT not in after and SmellKind.SYNTAX_ERROR not in after
    compile(result.modernized["legacy/textutils.py"], "textutils.py", "exec")  # type: ignore[attr-defined]


def test_insecure_example_neutralizes_dangers(mock_runs: dict[str, object]) -> None:
    result = mock_runs["insecure-config"]
    report = result.report  # type: ignore[attr-defined]
    assert report.baseline.failed == 3 and report.final.green
    src = result.modernized["legacy/config_loader.py"]  # type: ignore[attr-defined]
    assert "ast.literal_eval(raw)" in src and "eval(raw)" not in src.replace("literal_eval", "")
    assert "except Exception:" in src and "defaults=None" in src
    after = {s.kind for s in report.smells_after}
    assert not after & {SmellKind.EVAL_USAGE, SmellKind.BARE_EXCEPT, SmellKind.MUTABLE_DEFAULT}


def test_old_api_example_shows_the_gate_rejecting_a_breaking_step(
    mock_runs: dict[str, object],
) -> None:
    report = mock_runs["old-api-helpers"].report  # type: ignore[attr-defined]
    assert report.final.green
    rejected = {s.step.transform: s for s in report.rejected}
    assert TransformKind.PERCENT_FORMAT_TO_FSTRING in rejected
    assert rejected[TransformKind.PERCENT_FORMAT_TO_FSTRING].regressions
    src = mock_runs["old-api-helpers"].modernized["legacy/report_helpers.py"]  # type: ignore[attr-defined]
    assert '"%d%% complete" % ratio' in src and "time.perf_counter()" in src
    assert any("insecure_deserialization" in r for r in report.residual_risks)


def test_originals_never_mutated(mock_runs: dict[str, object]) -> None:
    project = load_project("py2-style-utils")
    on_disk = (project.root / "legacy" / "textutils.py").read_text()
    assert "print line" in on_disk
    assert mock_runs["py2-style-utils"].originals["legacy/textutils.py"] == on_disk  # type: ignore[attr-defined]


def test_report_markdown(mock_runs: dict[str, object]) -> None:
    md = render_markdown(mock_runs["old-api-helpers"].report)  # type: ignore[attr-defined]
    for heading in [
        "# Modernization report",
        "## Smells found",
        "## Steps",
        "## Rejected steps",
        "## Residual risks",
        "## Full diff",
    ]:
        assert heading in md
    assert "Would have broken" in md


def test_hallucinated_ids_fall_back_and_are_noted(tmp_path: Path) -> None:
    provider = ScriptedProvider(['{"transforms": ["rewrite_everything", "xrange_to_range"]}'])
    result = Agent(provider, Settings(isolate_network=False), max_steps=3).run(
        load_project("py2-style-utils")
    )
    assert any("rewrite_everything" in n for n in result.report.notes)
    assert all(s.step.transform != "rewrite_everything" for s in result.report.steps)


def test_invalid_reply_retries_with_feedback_then_falls_back() -> None:
    provider = ScriptedProvider(["I think you should refactor it all", "still not json"])
    result = Agent(provider, Settings(isolate_network=False), max_steps=2).run(
        load_project("insecure-config")
    )
    assert provider.requests[1].feedback is not None
    assert result.report.steps[0].step.chosen_by == "fallback"
    assert any(e["event"] == "invalid_reply" for e in result.events)


def test_provider_errors_fall_back_to_rules() -> None:
    result = Agent(BrokenProvider(), Settings(isolate_network=False)).run(
        load_project("insecure-config")
    )
    assert result.report.final.green
    assert any("provider error" in n for n in result.report.notes)


def test_max_steps_cap_and_progress_hook() -> None:
    seen: list[str] = []
    result = Agent(
        MockProvider(),
        Settings(isolate_network=False),
        max_steps=2,
        on_progress=lambda kind, data: seen.append(kind),
    ).run(load_project("py2-style-utils"))
    assert len(result.report.steps) == 2
    assert seen[0] == "analyzed" and seen[-1] == "finished" and seen.count("step") == 2


def test_model_diff_is_gated_by_tests() -> None:
    bad_diff = (
        "--- a/legacy/config_loader.py\n+++ b/legacy/config_loader.py\n"
        "@@ -28,2 +28,2 @@\n def describe(settings):\n"
        '-    return "%d settings: %s" % (len(settings), sorted(settings))\n'
        '+    return "broken"\n'
    )
    provider = ScriptedProvider([json.dumps({"diff": bad_diff})])
    settings = Settings(isolate_network=False, allow_model_diffs=True)
    result = Agent(provider, settings, max_steps=1).run(load_project("insecure-config"))
    step = result.report.steps[0]
    assert step.step.transform == TransformKind.MODEL_DIFF
    assert step.status == "rejected" and "regressed" in step.reason


def test_model_diff_that_adds_eval_is_rejected_before_running() -> None:
    diff = (
        "@@ -28,2 +28,2 @@\n def describe(settings):\n"
        '-    return "%d settings: %s" % (len(settings), sorted(settings))\n'
        '+    return eval("1") or "%d settings: %s" % (len(settings), sorted(settings))\n'
    )
    provider = ScriptedProvider([json.dumps({"diff": diff})])
    settings = Settings(isolate_network=False, allow_model_diffs=True)
    result = Agent(provider, settings, max_steps=1).run(load_project("insecure-config"))
    assert "eval/exec/pickle/shell" in result.report.steps[0].reason


def test_mock_provider_never_proposes_diffs_even_if_enabled() -> None:
    settings = Settings(isolate_network=False, allow_model_diffs=True)
    result = Agent(MockProvider(), settings).run(load_project("insecure-config"))
    assert all(s.step.transform != TransformKind.MODEL_DIFF for s in result.report.steps)


def test_apply_unified_diff_strict() -> None:
    src = "a\nb\nc\n"
    assert apply_unified_diff(src, "@@ -2,1 +2,1 @@\n-b\n+B\n", "m.py") == "a\nB\nc\n"
    with pytest.raises(PatchError):
        apply_unified_diff(src, "@@ -2,1 +2,1 @@\n-x\n+B\n", "m.py")
    with pytest.raises(PatchError):
        apply_unified_diff(src, "--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-a\n+A\n", "m.py")
    with pytest.raises(PatchError):
        apply_unified_diff(src, "no hunks here", "m.py")
