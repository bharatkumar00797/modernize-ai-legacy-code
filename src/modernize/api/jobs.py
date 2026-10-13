"""Background modernization runs with bounded concurrency and an in-memory store."""

from __future__ import annotations

import copy
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from modernize.agent import Agent, ProgressHook, RunResult
from modernize.config import Settings
from modernize.ingest import Project, list_examples, load_project
from modernize.llm import make_provider
from modernize.models import ModernizationReport

JobState = Literal["queued", "running", "finished", "error"]
RunFn = Callable[[Project, Settings, int | None, ProgressHook], RunResult]


def default_run(
    project: Project, settings: Settings, max_steps: int | None, on_progress: ProgressHook
) -> RunResult:
    provider = make_provider(settings, settings.provider)
    return Agent(provider, settings, max_steps=max_steps, on_progress=on_progress).run(project)


def _now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return "run_" + secrets.token_hex(6)


class QueueFull(RuntimeError):
    """Too many runs are queued or running."""


@dataclass
class RunRecord:
    """One modernization run as tracked by the service.

    Events are append-only so polling is cheap. Readers only ever see deep-copied snapshots of
    the report published when the run finishes (or after each step via events).
    """

    id: str
    owner: str
    example_id: str
    title: str
    provider: str
    max_steps: int | None
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    state: JobState = "queued"
    report: ModernizationReport | None = None
    modernized: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def done(self) -> bool:
        return self.state in ("finished", "error")

    def record_event(self, kind: str, data: dict[str, Any]) -> None:
        with self.lock:
            self.events.append({"seq": len(self.events), "event": kind, **copy.deepcopy(data)})

    def events_since(self, since: int) -> list[dict[str, Any]]:
        with self.lock:
            return copy.deepcopy(self.events[since:])

    def report_snapshot(self) -> dict[str, Any] | None:
        with self.lock:
            if self.report is None:
                return None
            return self.report.model_dump(mode="json")


class RunManager:
    """Owns the worker pool and the run records.

    ``max_active`` bounds queued + running work (callers get :class:`QueueFull` -> 503); the
    oldest finished runs are evicted beyond ``max_kept``. With ``inline=True`` the agent runs
    inside :meth:`submit` (serverless mode).
    """

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_active: int = 8,
        max_kept: int = 200,
        run_fn: RunFn = default_run,
        inline: bool = False,
    ) -> None:
        self.inline = inline
        self._executor = (
            None
            if inline
            else ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="modernize")
        )
        self._max_active = max_active
        self._max_kept = max_kept
        self._run_fn = run_fn
        self._records: OrderedDict[str, RunRecord] = OrderedDict()
        self._lock = threading.Lock()

    def submit(
        self, *, owner: str, project: Project, settings: Settings, max_steps: int | None
    ) -> RunRecord:
        record = RunRecord(
            id=new_id(),
            owner=owner,
            example_id=project.id,
            title=project.title,
            provider=settings.provider,
            max_steps=max_steps,
        )
        with self._lock:
            if sum(1 for r in self._records.values() if not r.done) >= self._max_active:
                raise QueueFull("Too many modernization runs in progress; try again shortly")
            self._records[record.id] = record
            self._evict_unlocked()
        if self._executor is None:
            self._execute(record, project, settings, max_steps)
        else:
            self._executor.submit(self._execute, record, project, settings, max_steps)
        return record

    def get(self, run_id: str, owner: str | None) -> RunRecord | None:
        """Look up a run; ``owner=None`` means the caller may see every run."""
        with self._lock:
            record = self._records.get(run_id)
        if record is None or (owner is not None and record.owner != owner):
            return None
        return record

    def list(self, owner: str | None, limit: int = 50) -> list[RunRecord]:
        with self._lock:
            records = [
                r for r in reversed(self._records.values()) if owner is None or r.owner == owner
            ]
        return records[:limit]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values() if not r.done)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _evict_unlocked(self) -> None:
        overflow = len(self._records) - self._max_kept
        if overflow <= 0:
            return
        for rid in [rid for rid, r in self._records.items() if r.done][:overflow]:
            del self._records[rid]

    def _execute(
        self, record: RunRecord, project: Project, settings: Settings, max_steps: int | None
    ) -> None:
        with record.lock:
            record.state = "running"
            record.started_at = _now()

        def on_progress(kind: str, data: dict[str, Any]) -> None:
            record.record_event(kind, data)

        try:
            result = self._run_fn(project, settings, max_steps, on_progress)
        except Exception as exc:  # a crashed run must never take down the worker
            with record.lock:
                record.state = "error"
                record.error = f"{type(exc).__name__}: {exc}"[:500]
                record.finished_at = _now()
            return
        with record.lock:
            record.report = result.report
            record.modernized = dict(result.modernized)
            record.state = "finished"
            record.finished_at = _now()


def list_example_ids() -> list[str]:
    return [ex.id for ex in list_examples()]


def load_example(example_id: str) -> Project:
    return load_project(example_id)
