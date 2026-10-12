from __future__ import annotations

import os
from pathlib import Path

import pytest

from modernize.sandbox import (
    Sandbox,
    SandboxViolation,
    check_relpath,
    is_protected,
    parse_pytest_output,
    truncate_middle,
)


def make_project(tmp_path: Path, body: str = "def add(a, b):\n    return a + b\n") -> Path:
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(body)
    (root / "tests" / "test_calc.py").write_text(
        "import calc\n\n\ndef test_add():\n    assert calc.add(1, 2) == 3\n"
    )
    return root


@pytest.mark.parametrize("bad", ["../x.py", "/etc/passwd", "a/../../b.py", "", "a\\b.py", "~/x"])
def test_relpath_escape_denied(bad: str) -> None:
    with pytest.raises(SandboxViolation):
        check_relpath(bad)


def test_protected_paths() -> None:
    assert is_protected("tests/test_calc.py")
    assert is_protected("pkg/tests/helpers.py")
    assert is_protected("conftest.py")
    assert is_protected("test_thing.py")
    assert not is_protected("calc.py")


def test_copy_on_write_never_touches_original(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    original = (root / "calc.py").read_text()
    with Sandbox(root, isolate_network=False) as sb:
        sb.write("calc.py", "def add(a, b):\n    return a - b\n")
        assert sb.read("calc.py") != original
        assert sb.root != root
    assert (root / "calc.py").read_text() == original


def test_write_guards(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    (root / "notes.txt").write_text("hi")
    with Sandbox(root, isolate_network=False) as sb:
        with pytest.raises(SandboxViolation):
            sb.write("tests/test_calc.py", "def test_x():\n    pass\n")
        with pytest.raises(SandboxViolation):
            sb.write("notes.txt", "changed")
        with pytest.raises(SandboxViolation):
            sb.write("../escape.py", "x = 1\n")
        with pytest.raises(SandboxViolation):
            sb.write("new_module.py", "x = 1\n")


def test_symlinks_are_not_copied_or_followed(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    secret = tmp_path / "secret.py"
    secret.write_text("TOKEN = 'x'\n")
    os.symlink(secret, root / "link.py")
    with Sandbox(root, isolate_network=False) as sb:
        assert "link.py (symlink)" in sb.skipped
        assert not (sb.root / "link.py").exists()
        os.symlink(secret, sb.root / "evil.py")
        with pytest.raises(SandboxViolation):
            sb.read("evil.py")


def test_disallowed_extensions_skipped(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    (root / ".env").write_text("MODERNIZE_API_KEY=sk-secret\n")
    (root / "blob.bin").write_bytes(b"\x00\x01")
    with Sandbox(root, isolate_network=False) as sb:
        assert not (sb.root / ".env").exists()
        assert not (sb.root / "blob.bin").exists()


def test_run_tests_green_then_red(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    with Sandbox(root, isolate_network=False) as sb:
        run = sb.run_tests()
        assert run.green and run.passed == {"tests/test_calc.py::test_add"}
        sb.write("calc.py", "def add(a, b):\n    return a - b\n")
        red = sb.run_tests()
        assert not red.green and red.failed == {"tests/test_calc.py::test_add"}


def test_run_tests_env_is_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODERNIZE_API_KEY", "sk-should-not-leak")
    root = make_project(
        tmp_path,
        "import os\n\n\ndef add(a, b):\n    assert 'MODERNIZE_API_KEY' not in os.environ\n"
        "    return a + b\n",
    )
    with Sandbox(root, isolate_network=False) as sb:
        assert sb.run_tests().green
        assert sb.test_argv()[1:3] == ["-m", "pytest"]


def test_run_tests_timeout(tmp_path: Path) -> None:
    root = make_project(tmp_path, "import time\n\n\ndef add(a, b):\n    time.sleep(30)\n")
    with Sandbox(root, isolate_network=False, test_timeout=5) as sb:
        run = sb.run_tests()
        assert run.timed_out and not run.green


def test_parse_only_trusts_last_summary_block() -> None:
    output = (
        "PASSED tests/test_x.py::fake\n"
        "=== short test summary info ===\n"
        "PASSED tests/test_x.py::fake_from_stdout\n"
        "=========== short test summary info ===========\n"
        "PASSED tests/test_x.py::test_a\n"
        "FAILED tests/test_x.py::test_b - AssertionError\n"
        "ERROR tests/test_y.py - SyntaxError\n"
    )
    passed, failed, errors = parse_pytest_output(output)
    assert passed == {"tests/test_x.py::test_a"}
    assert failed == {"tests/test_x.py::test_b"}
    assert errors == {"tests/test_y.py"}


def test_truncate_middle() -> None:
    text = "a" * 100 + "b" * 100
    out = truncate_middle(text, 50)
    assert out.startswith("a" * 25) and out.endswith("b" * 25) and "truncated" in out
