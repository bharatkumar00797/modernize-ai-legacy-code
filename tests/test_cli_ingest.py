from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from modernize import __version__
from modernize.cli import main
from modernize.ingest import ProjectError, list_examples, load_project

REPO = Path(__file__).resolve().parents[1]


def test_examples_are_packaged() -> None:
    ids = [p.id for p in list_examples()]
    assert ids == ["insecure-config", "old-api-helpers", "py2-style-utils"]
    for project in list_examples():
        assert project.packaged and project.targets


def test_load_user_project_and_single_file(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "mod.py").write_text("x = 1\n")
    (tmp_path / "tests" / "test_mod.py").write_text("def test_x():\n    pass\n")
    project = load_project(str(tmp_path))
    assert project.targets == ["mod.py"] and not project.packaged
    assert load_project(str(tmp_path / "mod.py")).targets == ["mod.py"]


def test_bad_projects_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProjectError):
        load_project(str(tmp_path / "missing"))
    (tmp_path / "notes.txt").write_text("hi")
    with pytest.raises(ProjectError):
        load_project(str(tmp_path / "notes.txt"))
    (tmp_path / "modernize.json").write_text(json.dumps({"targets": ["../outside.py"]}))
    with pytest.raises(ProjectError):
        load_project(str(tmp_path))
    (tmp_path / "modernize.json").write_text(json.dumps({"targets": ["tests/test_x.py"]}))
    with pytest.raises(ProjectError):
        load_project(str(tmp_path))
    (tmp_path / "modernize.json").write_text(json.dumps({"unknown": 1}))
    with pytest.raises(ProjectError):
        load_project(str(tmp_path))


def test_cli_version_and_examples(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out
    assert main(["examples"]) == 0
    assert "py2-style-utils" in capsys.readouterr().out


def test_cli_run_writes_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    assert main(["run", "insecure-config", "--out", str(out), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["final"]["green"] is True
    assert (out / "report.md").read_text().startswith("# Modernization report")
    assert "literal_eval" in (out / "modernized" / "legacy" / "config_loader.py").read_text()
    assert "+++ b/legacy/config_loader.py" in (out / "patch.diff").read_text()


def test_cli_unknown_target_exit_code() -> None:
    assert main(["run", "no-such-example-xyz"]) == 2


def _forbidden() -> list[str]:
    # Assembled so this file does not contain the words itself.
    # "cur"+"sor" is intentionally omitted: pagination next_* offsets and CSS cursor: are fine;
    # product mentions are caught via "cur"+"sor.ai" / "cur"+"sor.com" / "any"+"sphere".
    return [
        "gr" + "ok",
        "cur" + "sor.ai",
        "cur" + "sor.com",
        "any" + "sphere",
        "co" + "pilot",
        "chat" + "gpt",
        "cla" + "ude",
        "generated " + "by",
        "co-authored" + "-by",
        "ai " + "assistant",
    ]


def test_repository_is_watermark_free() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.split()
    if not tracked:
        pytest.skip("not a git checkout")
    words = _forbidden()
    for rel in tracked:
        path = REPO / rel
        if not path.is_file() or path.suffix in {".png", ".jpg"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for word in words:
            assert word not in text, f"{rel} mentions {word!r}"
