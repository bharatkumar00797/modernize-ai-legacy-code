"""Markdown modernization report."""

from __future__ import annotations

from collections import Counter

from .models import ModernizationReport, SuiteSnapshot
from .transforms import TRANSFORMS


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _fence(text: str, lang: str = "python") -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text}\n{fence}"


def _suite(s: SuiteSnapshot) -> str:
    state = "green" if s.green else ("timed out" if s.timed_out else "red")
    return f"{state} ({s.passed} passed, {s.failed} failed, {s.errors} errors)"


def render_markdown(report: ModernizationReport) -> str:
    kept = report.kept
    rejected = report.rejected
    out: list[str] = [
        f"# Modernization report: {report.target}",
        "",
        f"- Provider: `{report.provider}`",
        f"- Files: {', '.join(f'`{f}`' for f in report.files)}",
        f"- Tests before: {_suite(report.baseline)}",
        f"- Tests after: {_suite(report.final)}",
        f"- Smells: {len(report.smells_before)} before -> {len(report.smells_after)} after",
        f"- Steps: {len(kept)} kept, {len(rejected)} rejected, "
        f"{sum(1 for s in report.steps if s.status == 'noop')} no-op",
        "",
        "## Smells found",
        "",
    ]
    counts = Counter(s.kind.value for s in report.smells_before)
    after_counts = Counter(s.kind.value for s in report.smells_after)
    if counts:
        out += ["| Smell | Before | After |", "| --- | ---: | ---: |"]
        for kind, n in sorted(counts.items()):
            out.append(f"| `{kind}` | {n} | {after_counts.get(kind, 0)} |")
        for kind, n in sorted(after_counts.items()):
            if kind not in counts:
                out.append(f"| `{kind}` | 0 | {n} |")
    else:
        out.append("No smells detected.")
    out += ["", "## Steps", ""]
    if report.steps:
        out += [
            "| # | Transform | File | Chosen by | Result | Reason |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
        for r in report.steps:
            out.append(
                f"| {r.step.index} | `{r.step.transform.value}` | `{r.step.path}` | "
                f"{r.step.chosen_by} | **{r.status}** | {_cell(r.reason)} |"
            )
    else:
        out.append("No steps were attempted.")
    for title, items in (("Applied changes", kept), ("Rejected steps", rejected)):
        if not items:
            continue
        out += ["", f"## {title}", ""]
        for r in items:
            t = TRANSFORMS.get(r.step.transform)
            name = t.title if t else "model-proposed diff"
            out += [f"### {r.step.index}. {name} (`{r.step.path}`)", "", f"{_cell(r.reason)}", ""]
            if r.fixed_tests:
                out.append("Newly passing: " + ", ".join(f"`{x}`" for x in r.fixed_tests[:10]))
                out.append("")
            if r.regressions:
                out.append("Would have broken: " + ", ".join(f"`{x}`" for x in r.regressions[:10]))
                out.append("")
            if r.before_snippet:
                out += ["Before:", "", _fence(r.before_snippet), ""]
            if r.after_snippet:
                out += ["After:", "", _fence(r.after_snippet), ""]
    out += ["", "## Residual risks", ""]
    out += [f"- {_cell(r)}" for r in report.residual_risks] or ["- None identified."]
    if report.notes:
        out += ["", "## Notes", ""] + [f"- {_cell(n)}" for n in report.notes]
    if report.diff:
        out += ["", "## Full diff", "", _fence(report.diff, "diff")]
    return "\n".join(out).rstrip() + "\n"
