"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from modernize.api.jobs import JobState
from modernize.api.settings import AccessMode


class RunCreate(BaseModel):
    """Start a modernization run for a packaged example."""

    model_config = ConfigDict(extra="forbid")

    example: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
        description="packaged example id (see GET /api/examples)",
        examples=["py2-style-utils"],
    )
    max_steps: int | None = Field(default=None, ge=1, le=100)
    provider: Literal["mock", "openai"] | None = None


class RunSummaryOut(BaseModel):
    id: str
    state: JobState
    example: str
    title: str
    provider: str
    max_steps: int | None = None
    kept: int = 0
    rejected: int = 0
    smells_before: int = 0
    smells_after: int = 0
    tests_green: bool | None = None
    created_at: datetime
    finished_at: datetime | None = None


class RunDetailOut(RunSummaryOut):
    started_at: datetime | None = None
    report: dict[str, Any] | None = None
    event_count: int = 0
    error: str | None = None


class RunCreatedOut(RunDetailOut):
    """Response of ``POST /api/runs``.

    In background mode the run is still ``queued``/``running``: poll the events endpoint. In
    sync (serverless) mode it has finished and ``events`` holds every entry.
    """

    events: list[dict[str, Any]] = Field(default_factory=list)


class RunListOut(BaseModel):
    runs: list[RunSummaryOut]


class EventsOut(BaseModel):
    id: str
    state: JobState
    done: bool
    next_cursor: int = Field(description="pass as ?since= to fetch only newer events")
    events: list[dict[str, Any]]


class ExampleOut(BaseModel):
    id: str
    title: str
    description: str
    targets: list[str]


class ExampleListOut(BaseModel):
    examples: list[ExampleOut]


class TransformOut(BaseModel):
    id: str
    title: str
    description: str
    risk: str
    needs_parse: bool


class TransformListOut(BaseModel):
    transforms: list[TransformOut]


class ConfigOut(BaseModel):
    version: str
    auth_required: bool
    access_mode: AccessMode
    authenticated: bool = False
    providers: list[str]
    default_provider: str
    max_steps_cap: int
    sync_runs: bool = Field(description="runs finish within POST /api/runs")
    examples: list[str]


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    active_runs: int
