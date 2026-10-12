"""Load a packaged example or a user project directory/file."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .sandbox import SandboxViolation, check_relpath, is_protected

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
CONFIG_NAMES = ("modernize.json", "example.json")
_EXAMPLE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_TARGETS = 50

RelPy = Annotated[str, Field(min_length=4, max_length=255, pattern=r"^[A-Za-z0-9_./-]+\.py$")]


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=_EXAMPLE_ID.pattern)] | None = None
    title: Annotated[str, Field(max_length=120)] = ""
    description: Annotated[str, Field(max_length=1_000)] = ""
    targets: list[RelPy] = Field(default_factory=list, max_length=MAX_TARGETS)
    pythonpath: list[Annotated[str, Field(max_length=255)]] = Field(
        default_factory=lambda: ["."], max_length=10
    )


@dataclass(frozen=True)
class Project:
    id: str
    title: str
    description: str
    root: Path
    targets: list[str]
    pythonpath: list[str]
    packaged: bool


class ProjectError(ValueError):
    """The requested project cannot be modernized."""


def _read_config(root: Path) -> ProjectConfig:
    for name in CONFIG_NAMES:
        path = root / name
        if path.is_file() and not path.is_symlink():
            try:
                return ProjectConfig.model_validate(json.loads(path.read_text("utf-8")))
            except (ValueError, ValidationError) as exc:
                raise ProjectError(f"invalid {name}: {exc}") from exc
    return ProjectConfig()


def _discover_targets(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() or any(
            p.startswith(".") or p in {"venv", "__pycache__"} for p in rel.split("/")
        ):
            continue
        if not is_protected(rel):
            found.append(rel)
    return found[:MAX_TARGETS]


def _build(root: Path, *, packaged: bool, only: str | None = None) -> Project:
    config = _read_config(root)
    targets = [only] if only else (config.targets or _discover_targets(root))
    for rel in [*targets, *config.pythonpath]:
        try:
            check_relpath(rel)
        except SandboxViolation as exc:
            raise ProjectError(str(exc)) from exc
    for rel in targets:
        if is_protected(rel):
            raise ProjectError(f"{rel} is a protected test file and cannot be a target")
        candidate = (root / rel).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ProjectError(f"target not found inside the project: {rel}")
    if not targets:
        raise ProjectError("no Python modules to modernize were found")
    return Project(
        id=config.id or root.name,
        title=config.title or root.name,
        description=config.description,
        root=root,
        targets=list(targets),
        pythonpath=list(config.pythonpath),
        packaged=packaged,
    )


def list_examples() -> list[Project]:
    return [
        _build(path, packaged=True)
        for path in sorted(EXAMPLES_DIR.iterdir())
        if path.is_dir() and (path / "example.json").is_file()
    ]


def load_project(ref: str) -> Project:
    """Resolve ``ref`` as a packaged example id first, then as a filesystem path."""
    if _EXAMPLE_ID.match(ref) and (EXAMPLES_DIR / ref / "example.json").is_file():
        return _build(EXAMPLES_DIR / ref, packaged=True)
    path = Path(ref).expanduser()
    if not path.exists():
        raise ProjectError(f"unknown example or path: {ref}")
    if path.is_symlink():
        raise ProjectError("symlinked projects are not accepted")
    path = path.resolve()
    if path.is_file():
        if path.suffix != ".py":
            raise ProjectError("only .py files can be modernized")
        return _build(path.parent, packaged=False, only=path.name)
    return _build(path, packaged=False)
