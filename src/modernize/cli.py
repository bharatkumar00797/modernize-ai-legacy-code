"""Command line interface: ``modernize examples`` and ``modernize run``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .agent import Agent
from .config import Settings
from .ingest import ProjectError, list_examples, load_project
from .llm import make_provider
from .report import render_markdown
from .sandbox import SandboxViolation, check_relpath


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modernize",
        description="AI agent that modernizes legacy Python code behind a test gate.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("examples", help="list the packaged legacy examples")
    run = sub.add_parser("run", help="modernize an example id or a project path")
    run.add_argument("target", help="example id (see `modernize examples`) or a path")
    run.add_argument("--provider", choices=["mock", "openai"], default=None)
    run.add_argument("--max-steps", type=int, default=None)
    run.add_argument("--out", type=Path, default=None, help="write report.md, patch.diff, files")
    run.add_argument("--json", action="store_true", help="print the report as JSON")
    run.add_argument("--quiet", action="store_true", help="no progress output")
    return parser


def _write_outputs(out: Path, result: Any) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render_markdown(result.report), encoding="utf-8")
    (out / "report.json").write_text(result.report.model_dump_json(indent=2), encoding="utf-8")
    (out / "patch.diff").write_text(result.report.diff, encoding="utf-8")
    for rel, text in result.modernized.items():
        dest = out / "modernized" / check_relpath(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")


def _progress(kind: str, data: dict[str, Any]) -> None:
    if kind == "baseline":
        print(
            f"baseline tests: {data['passed']} passed, {data['failed']} failed, "
            f"{data['errors']} errors",
            file=sys.stderr,
        )
    elif kind == "step":
        print(
            f"  step {data['index']:>2} {data['status']:<8} {data['transform']} - {data['reason']}",
            file=sys.stderr,
        )
    elif kind == "invalid_reply":
        print("  model reply invalid; retrying / falling back to rules", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "examples":
        for ex in list_examples():
            print(f"{ex.id:<18} {ex.title}")
            print(f"{'':<18} {ex.description}")
        return 0
    if args.command != "run":
        parser.print_help()
        return 0
    try:
        settings = Settings.from_env()
        project = load_project(args.target)
        provider = make_provider(settings, args.provider)
    except (ProjectError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    hook = None if args.quiet or args.json else _progress
    agent = Agent(provider, settings, max_steps=args.max_steps, on_progress=hook)
    try:
        result = agent.run(project)
    except SandboxViolation as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out is not None:
        _write_outputs(args.out, result)
    report = result.report
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print(
            f"{project.id}: tests {'GREEN' if report.final.green else 'RED'} "
            f"({report.baseline.passed} -> {report.final.passed} passing); "
            f"{len(report.kept)} kept, {len(report.rejected)} rejected; "
            f"smells {len(report.smells_before)} -> {len(report.smells_after)}"
        )
        if args.out is not None:
            print(f"report: {args.out / 'report.md'}")
    return 0 if report.final.green else 1
