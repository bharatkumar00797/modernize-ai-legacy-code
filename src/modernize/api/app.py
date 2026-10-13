"""FastAPI application: REST endpoints for modernization runs plus the static dashboard."""

from __future__ import annotations

import hashlib
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi import status as http
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from modernize import __version__
from modernize.api.jobs import (
    QueueFull,
    RunFn,
    RunManager,
    RunRecord,
    default_run,
    list_example_ids,
)
from modernize.api.middleware import BodyLimitMiddleware, SecurityHeadersMiddleware
from modernize.api.schemas import (
    ConfigOut,
    EventsOut,
    ExampleListOut,
    ExampleOut,
    HealthOut,
    RunCreate,
    RunCreatedOut,
    RunDetailOut,
    RunListOut,
    RunSummaryOut,
    TransformListOut,
    TransformOut,
)
from modernize.api.security import ApiKeyAuth, RateLimiter
from modernize.api.settings import ApiSettings
from modernize.config import Settings
from modernize.ingest import ProjectError, list_examples, load_project
from modernize.report import render_markdown
from modernize.transforms import TRANSFORMS

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 64 * 1024
IdParam = Annotated[str, PathParam(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


@dataclass(frozen=True)
class Caller:
    """``owner`` tags new runs, ``scope`` limits visibility (``None`` = all),
    ``client`` keys rate limits and ``trusted`` unlocks real providers."""

    owner: str
    scope: str | None
    client: str
    trusted: bool


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        # The right-most hop is the one appended by the trusted edge proxy; anything to its
        # left is client-supplied and can be forged.
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
        hops = [h for h in hops if h]
        if hops:
            return hops[-1][:64]
    return request.client.host if request.client else "unknown"


def presented_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key and key.strip():
        return key.strip()
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def _retry_after(seconds: float) -> dict[str, str]:
    return {"Retry-After": str(max(1, math.ceil(seconds)))}


@dataclass(frozen=True)
class AccessGuard:
    """Authenticates a request and applies the general per-caller rate limit."""

    settings: ApiSettings
    auth: ApiKeyAuth
    limiter: RateLimiter

    def identify(self, request: Request) -> Caller | None:
        if self.auth.enabled:
            fp = self.auth.verify(presented_key(request))
            return Caller(f"key:{fp}", f"key:{fp}", f"key:{fp}", True) if fp else None
        ip = client_ip(request, self.settings.trust_proxy)
        if self.settings.dev_mode:
            return Caller("dev", None, f"ip:{ip}", True)
        anon = "anon:" + hashlib.sha256(ip.encode()).hexdigest()[:12]
        return Caller(anon, anon, f"ip:{ip}", False)

    def __call__(self, request: Request) -> Caller:
        who = self.identify(request)
        if who is None:
            raise HTTPException(
                http.HTTP_401_UNAUTHORIZED,
                "Missing or invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        retry = self.limiter.check(who.client)
        if retry is not None:
            raise HTTPException(
                http.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded", headers=_retry_after(retry)
            )
        return who


def get_caller(request: Request) -> Caller:
    guard: AccessGuard = request.app.state.guard
    return guard(request)


CallerDep = Annotated[Caller, Depends(get_caller)]


def _counts(record: RunRecord) -> tuple[int, int, int, int, bool | None]:
    with record.lock:
        report = record.report
    if report is None:
        return 0, 0, 0, 0, None
    return (
        len(report.kept),
        len(report.rejected),
        len(report.smells_before),
        len(report.smells_after),
        report.final.green,
    )


def _summary(record: RunRecord) -> RunSummaryOut:
    kept, rejected, before, after, green = _counts(record)
    with record.lock:
        state, finished = record.state, record.finished_at
    return RunSummaryOut(
        id=record.id,
        state=state,
        example=record.example_id,
        title=record.title,
        provider=record.provider,
        max_steps=record.max_steps,
        kept=kept,
        rejected=rejected,
        smells_before=before,
        smells_after=after,
        tests_green=green,
        created_at=record.created_at,
        finished_at=finished,
    )


def _detail(record: RunRecord) -> RunDetailOut:
    with record.lock:
        error, started, count = record.error, record.started_at, len(record.events)
        snap = None if record.report is None else record.report.model_dump(mode="json")
    return RunDetailOut(
        **_summary(record).model_dump(),
        started_at=started,
        error=error,
        event_count=count,
        report=snap,
    )


def create_app(
    settings: ApiSettings | None = None,
    base_settings: Settings | None = None,
    *,
    run_fn: RunFn = default_run,
) -> FastAPI:
    cfg = settings or ApiSettings.from_env()
    base = base_settings or Settings.from_env()
    auth = ApiKeyAuth(cfg.api_keys)
    run_limiter = RateLimiter(cfg.run_limit_per_minute)
    manager = RunManager(
        max_workers=cfg.max_concurrent_runs,
        max_active=cfg.max_queued_runs,
        max_kept=cfg.max_runs_kept,
        run_fn=run_fn,
        inline=cfg.sync_runs,
    )
    example_ids = list_example_ids()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        manager.shutdown()

    app = FastAPI(
        title="Modernize AI API",
        version=__version__,
        description=(
            "AI legacy code modernization agent: rule-detected smells, LLM-ranked allowlisted "
            "refactors, sandboxed and gated by your tests."
        ),
        lifespan=lifespan,
    )
    app.state.guard = AccessGuard(cfg, auth, RateLimiter(cfg.rate_limit_per_minute))
    app.state.manager = manager

    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-API-Key", "Authorization"],
            allow_credentials=False,
            max_age=600,
        )
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(SecurityHeadersMiddleware)

    def load(run_id: str, who: Caller) -> RunRecord:
        record = manager.get(run_id, who.scope)
        if record is None:
            raise HTTPException(http.HTTP_404_NOT_FOUND, "Run not found")
        return record

    # ------------------------------------------------------------------ meta
    @app.get("/healthz", response_model=HealthOut, tags=["meta"])
    def healthz() -> HealthOut:
        return HealthOut(version=__version__, active_runs=manager.active_count())

    @app.get("/api/config", response_model=ConfigOut, tags=["meta"])
    def config(request: Request) -> ConfigOut:
        guard: AccessGuard = request.app.state.guard
        who = guard.identify(request)
        return ConfigOut(
            version=__version__,
            auth_required=cfg.auth_enabled,
            access_mode=cfg.access_mode,
            authenticated=who is not None and cfg.auth_enabled,
            providers=["mock"] if cfg.public_demo else sorted({"mock", base.provider}),
            default_provider="mock" if cfg.public_demo else base.provider,
            max_steps_cap=cfg.max_steps_cap,
            sync_runs=cfg.sync_runs,
            examples=example_ids,
        )

    @app.get("/api/examples", response_model=ExampleListOut, tags=["meta"])
    def examples(_: CallerDep) -> ExampleListOut:
        return ExampleListOut(
            examples=[
                ExampleOut(id=ex.id, title=ex.title, description=ex.description, targets=ex.targets)
                for ex in list_examples()
            ]
        )

    @app.get("/api/transforms", response_model=TransformListOut, tags=["meta"])
    def transforms(_: CallerDep) -> TransformListOut:
        out = [
            TransformOut(
                id=kind.value,
                title=transform.title,
                description=transform.description,
                risk=transform.risk,
                needs_parse=transform.needs_parse,
            )
            for kind, transform in TRANSFORMS.items()
        ]
        return TransformListOut(transforms=out)

    # ----------------------------------------------------------------- runs
    @app.post(
        "/api/runs",
        response_model=RunCreatedOut,
        status_code=http.HTTP_202_ACCEPTED,
        tags=["runs"],
        responses={200: {"description": "Finished run (sync mode)"}},
    )
    def create_run(body: RunCreate, who: CallerDep, response: Response) -> RunCreatedOut:
        use_configured = body.provider is None and not cfg.public_demo
        provider_name = base.provider if use_configured else (body.provider or "mock")
        if provider_name != "mock" and not who.trusted:
            raise HTTPException(
                http.HTTP_403_FORBIDDEN, "Only the offline mock provider is enabled here"
            )
        if body.provider == "openai" and not who.trusted:
            raise HTTPException(
                http.HTTP_403_FORBIDDEN, "Only the offline mock provider is enabled here"
            )
        try:
            project = load_project(body.example)
        except ProjectError as exc:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)[:300]) from exc
        if not project.packaged:
            raise HTTPException(
                http.HTTP_422_UNPROCESSABLE_CONTENT,
                "only packaged examples are accepted via the API",
            )
        max_steps = body.max_steps
        if max_steps is not None and max_steps > cfg.max_steps_cap:
            raise HTTPException(
                http.HTTP_422_UNPROCESSABLE_CONTENT,
                f"max_steps cannot exceed {cfg.max_steps_cap}",
            )
        # Only well-formed requests count towards the stricter run-creation limit.
        retry = run_limiter.check(who.client)
        if retry is not None:
            raise HTTPException(
                http.HTTP_429_TOO_MANY_REQUESTS,
                "Run creation limit exceeded",
                headers=_retry_after(retry),
            )
        run_settings = replace(base, provider=provider_name)
        try:
            record = manager.submit(
                owner=who.owner,
                project=project,
                settings=run_settings,
                max_steps=max_steps,
            )
        except QueueFull as exc:
            raise HTTPException(
                http.HTTP_503_SERVICE_UNAVAILABLE, str(exc), headers={"Retry-After": "10"}
            ) from exc
        detail = _detail(record).model_dump()
        if not manager.inline:
            return RunCreatedOut(**detail)  # 202: poll the events endpoint
        response.status_code = http.HTTP_200_OK
        events = record.events_since(0)
        return RunCreatedOut(**detail, events=events)

    @app.get("/api/runs", response_model=RunListOut, tags=["runs"])
    def list_runs(who: CallerDep, limit: Annotated[int, Query(ge=1, le=100)] = 25) -> RunListOut:
        return RunListOut(runs=[_summary(r) for r in manager.list(who.scope, limit)])

    @app.get("/api/runs/{run_id}", response_model=RunDetailOut, tags=["runs"])
    def get_run(run_id: IdParam, who: CallerDep) -> RunDetailOut:
        return _detail(load(run_id, who))

    @app.get("/api/runs/{run_id}/events", response_model=EventsOut, tags=["runs"])
    def get_events(
        run_id: IdParam,
        who: CallerDep,
        since: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> EventsOut:
        record = load(run_id, who)
        with record.lock:
            state, done = record.state, record.done
            events = record.events_since(since)
        return EventsOut(
            id=record.id,
            state=state,
            done=done,
            next_cursor=since + len(events),
            events=events,
        )

    @app.get(
        "/api/runs/{run_id}/report.md",
        response_class=PlainTextResponse,
        tags=["runs"],
        responses={200: {"content": {"text/markdown": {}}}},
    )
    def get_report_markdown(run_id: IdParam, who: CallerDep) -> PlainTextResponse:
        record = load(run_id, who)
        with record.lock:
            report = record.report
        if report is None:
            raise HTTPException(http.HTTP_409_CONFLICT, "Run has not finished yet")
        return PlainTextResponse(render_markdown(report), media_type="text/markdown; charset=utf-8")

    @app.get(
        "/api/runs/{run_id}/patch.diff",
        response_class=PlainTextResponse,
        tags=["runs"],
        responses={200: {"content": {"text/x-diff": {}}}},
    )
    def get_patch_diff(run_id: IdParam, who: CallerDep) -> PlainTextResponse:
        record = load(run_id, who)
        with record.lock:
            report = record.report
        if report is None:
            raise HTTPException(http.HTTP_409_CONFLICT, "Run has not finished yet")
        return PlainTextResponse(report.diff, media_type="text/x-diff; charset=utf-8")

    # ------------------------------------------------------------- dashboard
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
