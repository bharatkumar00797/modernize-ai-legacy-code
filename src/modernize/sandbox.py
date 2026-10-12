"""Copy-on-write sandbox: originals are never touched.

The project is copied into a private temp directory (symlinks refused, only
allowlisted file types, size caps). Writes are limited to allowlisted source files
outside protected paths (tests, test config), and the only process ever started
is a fixed ``python -m pytest`` argv - no shell, scrubbed env, timeout, rlimits
and optional network isolation.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from types import TracebackType

from .models import SuiteSnapshot

COPY_EXTENSIONS = {".py", ".ini", ".cfg", ".toml", ".txt", ".json", ".md", ".rst"}
WRITE_EXTENSIONS = {".py"}
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    ".pytest_cache",
    "build",
    "dist",
}
PROTECTED_PREFIXES = ("tests/", "test/")
PROTECTED_NAMES = {
    "conftest.py",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "pyproject.toml",
    "modernize.json",
}
MAX_FILES = 300
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_CHARS = 12_000
_ENV_PASSTHROUGH = ("LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR")
_RESULT_LINE = re.compile(r"^(PASSED|FAILED|ERROR|XPASS)\s+(\S+)")
_SUMMARY_HEADER = re.compile(r"^=+ short test summary info =+$", re.M)


class SandboxViolation(Exception):
    """Raised when an operation would escape or weaken the sandbox."""


def truncate_middle(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters truncated] ...\n{text[-half:]}"


def check_relpath(rel: str) -> PurePosixPath:
    """Validate a sandbox-relative POSIX path (no absolute, '..', NUL or backslashes)."""
    if not rel or len(rel) > 255 or "\x00" in rel or "\\" in rel:
        raise SandboxViolation(f"invalid path: {rel!r}")
    path = PurePosixPath(rel)
    if path.is_absolute() or any(part in {"..", ""} for part in path.parts) or rel.startswith("~"):
        raise SandboxViolation(f"path escapes the workspace: {rel!r}")
    return path


def is_protected(rel: str) -> bool:
    path = check_relpath(rel)
    return (
        path.name in PROTECTED_NAMES
        or any(str(path).startswith(p) or f"/{p}" in f"/{path}" for p in PROTECTED_PREFIXES)
        or path.name.startswith("test_")
    )


@dataclass
class TestRun:
    exit_code: int
    passed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    errors: set[str] = field(default_factory=set)
    output: str = ""
    duration_s: float = 0.0
    timed_out: bool = False

    __test__ = False  # not a pytest test class

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and bool(self.passed) and not self.failed and not self.errors

    def snapshot(self) -> SuiteSnapshot:
        return SuiteSnapshot(
            passed=len(self.passed),
            failed=len(self.failed),
            errors=len(self.errors),
            green=self.green,
            timed_out=self.timed_out,
        )


def parse_pytest_output(output: str) -> tuple[set[str], set[str], set[str]]:
    """Read results from the *last* short-summary block (-rA), ignoring test stdout."""
    passed: set[str] = set()
    failed: set[str] = set()
    errors: set[str] = set()
    headers = list(_SUMMARY_HEADER.finditer(output))
    if not headers:
        return passed, failed, errors
    for line in output[headers[-1].end() :].splitlines():
        match = _RESULT_LINE.match(line.strip())
        if not match:
            continue
        status, node = match.groups()
        {"PASSED": passed, "XPASS": passed, "FAILED": failed, "ERROR": errors}[status].add(node)
    return passed, failed, errors


@lru_cache(maxsize=1)
def network_isolation_available() -> bool:
    if not sys.platform.startswith("linux") or shutil.which("unshare") is None:
        return False
    try:
        probe = subprocess.run(
            ["unshare", "-rn", "true"], capture_output=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


@lru_cache(maxsize=1)
def _pytest_site_dir() -> str | None:
    import importlib.util

    spec = importlib.util.find_spec("pytest")
    if spec is None or spec.origin is None:
        return None
    return str(Path(spec.origin).resolve().parents[1])


class Sandbox:
    """A private, disposable copy of a project."""

    def __init__(
        self,
        source_dir: Path,
        *,
        pythonpath: list[str] | None = None,
        test_timeout: int = 60,
        isolate_network: bool = True,
    ) -> None:
        self.source_dir = source_dir.resolve(strict=True)
        if not self.source_dir.is_dir():
            raise SandboxViolation(f"not a directory: {source_dir}")
        self._tmp = tempfile.TemporaryDirectory(prefix="modernize-")
        self.root = Path(self._tmp.name).resolve() / "workspace"
        self.test_timeout = test_timeout
        self.isolate_network = isolate_network and network_isolation_available()
        self.pythonpath = [str(check_relpath(p)) for p in pythonpath or ["."]]
        self.skipped: list[str] = []
        self._copy()

    # -- lifecycle ---------------------------------------------------------------
    def __enter__(self) -> Sandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def _copy(self) -> None:
        count = 0
        total = 0
        self.root.mkdir()
        for dirpath, dirnames, filenames in os.walk(self.source_dir, followlinks=False):
            here = Path(dirpath)
            keep = []
            for d in sorted(dirnames):
                if d in SKIP_DIRS or d.startswith("."):
                    continue
                if (here / d).is_symlink():
                    self.skipped.append(f"{(here / d).relative_to(self.source_dir)}/ (symlink)")
                    continue
                keep.append(d)
            dirnames[:] = keep
            for name in sorted(filenames):
                src = here / name
                rel = src.relative_to(self.source_dir).as_posix()
                if src.is_symlink():
                    self.skipped.append(f"{rel} (symlink)")
                    continue
                if src.suffix not in COPY_EXTENSIONS:
                    continue
                size = src.stat().st_size
                count += 1
                total += size
                if size > MAX_FILE_BYTES:
                    raise SandboxViolation(f"{rel} is larger than {MAX_FILE_BYTES} bytes")
                if count > MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise SandboxViolation("project is too large for the sandbox")
                dest = self.root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest, follow_symlinks=False)

    # -- files -------------------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        path = check_relpath(rel)
        target = self.root.joinpath(*path.parts)
        current = self.root
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise SandboxViolation(f"symlinks are not allowed: {rel}")
        resolved = target.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxViolation(f"path escapes the workspace: {rel}")
        return resolved

    def read(self, rel: str) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise SandboxViolation(f"no such file: {rel}")
        return path.read_text(encoding="utf-8")

    def write(self, rel: str, text: str) -> None:
        path = self.resolve(rel)
        if path.suffix not in WRITE_EXTENSIONS:
            raise SandboxViolation(f"writing {path.suffix or 'extensionless'} files is not allowed")
        if is_protected(rel):
            raise SandboxViolation(f"{rel} is protected (tests and test config are read-only)")
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            raise SandboxViolation(f"{rel}: new content exceeds {MAX_FILE_BYTES} bytes")
        if not path.is_file():
            raise SandboxViolation(f"only existing files can be modified: {rel}")
        path.write_text(text, encoding="utf-8")

    def python_files(self) -> list[str]:
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*.py")
            if not p.is_symlink()
        )

    # -- tests -------------------------------------------------------------------
    def _env(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
        paths = [str(self.root / p) for p in self.pythonpath]
        site = _pytest_site_dir()
        if site:
            paths.append(site)
        env.update(
            PATH="/usr/local/bin:/usr/bin:/bin",
            HOME=str(self.root),
            PYTHONPATH=os.pathsep.join(paths),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONHASHSEED="0",
            NO_COLOR="1",
        )
        return env

    def _limits(self) -> None:  # pragma: no cover - runs in the child process
        import resource

        cpu = self.test_timeout + 5
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        fsize = 32 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))

    def test_argv(self) -> list[str]:
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rA",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "--color=no",
            "--rootdir",
            str(self.root),
        ]
        if (self.root / "tests").is_dir():
            argv.append("tests")
        return ["unshare", "-rn", *argv] if self.isolate_network else argv

    def run_tests(self) -> TestRun:
        posix = os.name == "posix"
        start = time.monotonic()
        proc = subprocess.Popen(
            self.test_argv(),
            cwd=self.root,
            env=self._env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=posix,
            preexec_fn=self._limits if posix else None,
        )
        timed_out = False
        try:
            output, _ = proc.communicate(timeout=self.test_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if posix:
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover
                proc.kill()
            output, _ = proc.communicate()
        passed, failed, errors = parse_pytest_output(output or "")
        exit_code = -9 if timed_out else proc.returncode
        if exit_code not in (0, 5) and not (passed or failed or errors):
            errors.add("<collection>")
        return TestRun(
            exit_code=exit_code,
            passed=passed,
            failed=failed,
            errors=errors,
            output=truncate_middle(output or ""),
            duration_s=time.monotonic() - start,
            timed_out=timed_out,
        )
