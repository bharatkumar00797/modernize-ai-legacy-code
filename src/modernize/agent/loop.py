"""The modernization agent.

Each iteration re-analyzes the current (sandboxed) source, asks the provider to
rank the allowlisted transforms that address the remaining smells, applies the
top choice and runs the tests. A step is kept only when no previously passing
test regresses (and a green suite stays green); otherwise it is reverted.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ..config import Settings
from ..ingest import Project
from ..llm.base import Provider, RankRequest
from ..llm.mock import POLICY
from ..llm.parsing import ModelChoice, parse_reply
from ..models import (
    ModernizationReport,
    PlanStep,
    Severity,
    Smell,
    SmellKind,
    StepResult,
    TransformKind,
)
from ..sandbox import Sandbox, SandboxViolation, TestRun
from ..smells import FIXES, candidate_transforms, detect, parse
from ..transforms import TRANSFORMS
from .patch import PatchError, apply_unified_diff

ProgressHook = Callable[[str, dict[str, Any]], None]
DANGEROUS = {
    SmellKind.EVAL_USAGE,
    SmellKind.EXEC_USAGE,
    SmellKind.INSECURE_DESERIALIZATION,
    SmellKind.SHELL_INJECTION,
}
MAX_DIFF_ATTEMPTS_PER_FILE = 2
ChosenBy = Literal["model", "fallback", "mock"]
Status = Literal["kept", "rejected", "noop"]


@dataclass
class RunResult:
    report: ModernizationReport
    originals: dict[str, str]
    modernized: dict[str, str]
    test_output: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)


def _snippets(old: str, new: str, limit: int = 12) -> tuple[str, str]:
    removed: list[str] = []
    added: list[str] = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-") and len(removed) < limit:
            removed.append(line[1:])
        elif line.startswith("+") and len(added) < limit:
            added.append(line[1:])
    return "\n".join(removed)[:4000], "\n".join(added)[:4000]


def _dangerous(smells: list[Smell]) -> int:
    return sum(1 for s in smells if s.kind in DANGEROUS)


class Agent:
    def __init__(
        self,
        provider: Provider,
        settings: Settings | None = None,
        *,
        max_steps: int | None = None,
        on_progress: ProgressHook | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or Settings()
        self.max_steps = max(1, min(100, max_steps or self.settings.max_steps))
        self.on_progress = on_progress
        self.events: list[dict[str, Any]] = []
        self.notes: list[str] = []

    # -- helpers -----------------------------------------------------------------
    def _emit(self, kind: str, **data: Any) -> None:
        event = {"event": kind, **data}
        self.events.append(event)
        if self.on_progress is not None:
            self.on_progress(kind, data)

    @property
    def _diffs_enabled(self) -> bool:
        return self.settings.allow_model_diffs and self.provider.name != "mock"

    def _choose(self, request: RankRequest) -> tuple[ModelChoice, ChosenBy]:
        """Ask the provider; recover once from an invalid reply, then fall back to rules."""
        feedback: str | None = None
        for _ in range(2):
            req = RankRequest(**{**request.__dict__, "feedback": feedback})
            try:
                raw = self.provider.rank(req)
            except Exception as exc:  # provider/network failures never stop the run
                self.notes.append(f"provider error ({type(exc).__name__}); used rule order")
                break
            choice = parse_reply(raw, request.candidates, allow_diff=request.allow_diff)
            if choice is not None:
                if choice.dropped:
                    self.notes.append(
                        "ignored non-allowlisted transform ids from the model: "
                        + ", ".join(choice.dropped[:10])
                    )
                return choice, "mock" if self.provider.name == "mock" else "model"
            feedback = (
                'reply was not valid JSON {"transforms": [...]} using only the allowlisted ids'
            )
            self._emit("invalid_reply", path=request.path)
        ordered = [k for k in POLICY if k in request.candidates] or list(request.candidates)
        return ModelChoice(transforms=ordered, rationale="rule order fallback"), "fallback"

    def _gate(
        self,
        sandbox: Sandbox,
        path: str,
        old: str,
        new: str,
        step: PlanStep,
        current: TestRun,
    ) -> tuple[StepResult, TestRun]:
        before_snip, after_snip = _snippets(old, new)

        def result(status: Status, reason: str, **extra: Any) -> StepResult:
            return StepResult(
                step=step,
                status=status,
                reason=reason[:2000],
                before_snippet=before_snip,
                after_snippet=after_snip,
                **extra,
            )

        if new == old:
            return result("noop", "no safe, mechanical rewrite applies here; left for a human"), (
                current
            )
        if parse(old) is not None and parse(new) is None:
            return result("rejected", "would produce invalid Python"), current
        if _dangerous(detect(new, path)) > _dangerous(detect(old, path)):
            return result("rejected", "introduces new eval/exec/pickle/shell usage"), current
        try:
            sandbox.write(path, new)
        except SandboxViolation as exc:
            return result("rejected", f"sandbox: {exc}"), current
        after = sandbox.run_tests()
        regressions = sorted(current.passed - after.passed)
        if after.timed_out or regressions or (current.green and not after.green):
            sandbox.write(path, old)
            if after.timed_out:
                reason = "tests timed out"
            elif regressions:
                reason = "tests regressed: " + ", ".join(regressions[:5])
            else:
                reason = "suite was green and is no longer green"
            return result(
                "rejected",
                reason,
                tests_before=current.snapshot(),
                tests_after=after.snapshot(),
                regressions=[r[:300] for r in regressions[:200]],
            ), current
        fixed = sorted(after.passed - current.passed)
        return result(
            "kept",
            f"tests still pass; fixed {len(fixed)} test(s)" if fixed else "tests still pass",
            tests_before=current.snapshot(),
            tests_after=after.snapshot(),
            fixed_tests=[f[:300] for f in fixed[:200]],
        ), after

    # -- main loop ---------------------------------------------------------------
    def run(self, project: Project) -> RunResult:
        self.events = []
        self.notes = []
        with Sandbox(
            project.root,
            pythonpath=project.pythonpath,
            test_timeout=self.settings.test_timeout,
            isolate_network=self.settings.isolate_network,
        ) as sandbox:
            if sandbox.skipped:
                self.notes.append("skipped (not copied): " + ", ".join(sandbox.skipped[:10]))
            if self.settings.isolate_network and not sandbox.isolate_network:
                self.notes.append(
                    "network isolation unavailable on this host; tests ran with network"
                )
            originals = {p: sandbox.read(p) for p in project.targets}
            smells_before = [s for p, src in originals.items() for s in detect(src, p)]
            self._emit("analyzed", smells=len(smells_before), files=len(originals))
            current = sandbox.run_tests()
            baseline = current
            self._emit("baseline", **current.snapshot().model_dump())

            attempted: dict[str, set[TransformKind]] = {p: set() for p in project.targets}
            diff_attempts: dict[str, int] = dict.fromkeys(project.targets, 0)
            steps: list[StepResult] = []
            while len(steps) < self.max_steps:
                pick: tuple[str, str, list[Smell], list[TransformKind]] | None = None
                for path in project.targets:
                    src = sandbox.read(path)
                    smells = detect(src, path)
                    cands = [k for k in candidate_transforms(smells) if k not in attempted[path]]
                    if cands:
                        pick = (path, src, smells, cands)
                        break
                if pick is None:
                    break
                path, src, smells, cands = pick
                allow_diff = (
                    self._diffs_enabled and diff_attempts[path] < MAX_DIFF_ATTEMPTS_PER_FILE
                )
                request = RankRequest(
                    path=path,
                    smells=smells,
                    candidates=cands,
                    source=src,
                    allow_diff=allow_diff,
                    history=[k.value for k in attempted[path]],
                )
                choice, chosen_by = self._choose(request)
                index = len(steps) + 1
                if choice.diff is not None:
                    diff_attempts[path] += 1
                    step = PlanStep(
                        index=index,
                        transform=TransformKind.MODEL_DIFF,
                        path=path,
                        chosen_by="model",
                        rationale=choice.rationale[:2000],
                    )
                    try:
                        new = apply_unified_diff(src, choice.diff, path)
                    except PatchError as exc:
                        result = StepResult(
                            step=step, status="rejected", reason=f"diff did not apply: {exc}"
                        )
                    else:
                        result, current = self._gate(sandbox, path, src, new, step, current)
                else:
                    kind = choice.transforms[0]
                    attempted[path].add(kind)
                    step = PlanStep(
                        index=index,
                        transform=kind,
                        path=path,
                        addresses=sorted({s.kind for s in smells if FIXES.get(s.kind) == kind}),
                        chosen_by=chosen_by,
                        rationale=choice.rationale[:2000],
                    )
                    new = TRANSFORMS[kind].apply(src)
                    result, current = self._gate(sandbox, path, src, new, step, current)
                steps.append(result)
                self._emit(
                    "step",
                    index=index,
                    transform=step.transform.value,
                    path=path,
                    status=result.status,
                    reason=result.reason,
                )

            modernized = {p: sandbox.read(p) for p in project.targets}
            smells_after = [s for p, src in modernized.items() for s in detect(src, p)]
            final = current
            diff = "".join(
                "".join(
                    difflib.unified_diff(
                        originals[p].splitlines(keepends=True),
                        modernized[p].splitlines(keepends=True),
                        fromfile=f"a/{p}",
                        tofile=f"b/{p}",
                    )
                )
                for p in project.targets
            )
            report = ModernizationReport(
                target=project.id[:255],
                provider=self.provider.name[:64],
                files=list(project.targets),
                smells_before=smells_before[:2000],
                smells_after=smells_after[:2000],
                steps=steps,
                baseline=baseline.snapshot(),
                final=final.snapshot(),
                residual_risks=self._residual_risks(smells_after, steps, final)[:200],
                notes=[n[:2000] for n in dict.fromkeys(self.notes)][:200],
                diff=diff[:200_000],
            )
            self._emit("finished", **final.snapshot().model_dump())
            return RunResult(
                report=report,
                originals=originals,
                modernized=modernized,
                test_output=final.output,
                events=list(self.events),
            )

    @staticmethod
    def _residual_risks(smells: list[Smell], steps: list[StepResult], final: TestRun) -> list[str]:
        risks: list[str] = []
        if not final.green:
            risks.append("The test suite is still not green; the module needs manual work.")
        for result in steps:
            if result.status == "rejected":
                risks.append(
                    f"{result.step.transform.value} on {result.step.path} was rejected "
                    f"({result.reason}); the underlying smell remains."
                )
        for smell in smells:
            if smell.severity in {Severity.MEDIUM, Severity.HIGH}:
                risks.append(f"{smell.path}:{smell.line} {smell.kind.value}: {smell.message}")
        hints = sum(1 for s in smells if s.kind == SmellKind.MISSING_TYPE_HINTS)
        if hints:
            risks.append(f"{hints} function(s) still lack type hints (not auto-generated).")
        return [r[:2000] for r in risks]
