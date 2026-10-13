"""Configuration for the HTTP service, loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from modernize.config import env_bool, env_int

AccessMode = Literal["api-key", "dev", "public-demo"]


def _env_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def running_serverless() -> bool:
    """True on serverless platforms where background threads do not outlive a request."""
    return any(os.getenv(name) for name in ("VERCEL", "AWS_LAMBDA_FUNCTION_NAME"))


@dataclass(frozen=True)
class ApiSettings:
    """Service settings.

    Access modes:

    * keys configured (``MODERNIZE_API_KEYS``) -> every ``/api`` call needs ``X-API-Key`` or a
      Bearer token; callers may use the configured LLM provider.
    * ``dev_mode`` -> no auth and full access; meant for ``localhost`` only.
    * neither -> public demo: anonymous callers may only run packaged examples with the offline
      mock provider, and only see their own runs.

    ``sync_runs`` runs the agent inside ``POST /api/runs`` (serverless hosts freeze background
    threads once a response is sent); it is auto-enabled on Vercel / Lambda.
    """

    api_keys: tuple[str, ...] = ()
    dev_mode: bool = False
    cors_origins: tuple[str, ...] = ()
    rate_limit_per_minute: int = 120
    run_limit_per_minute: int = 6
    max_concurrent_runs: int = 2
    max_queued_runs: int = 8
    max_runs_kept: int = 200
    max_steps_cap: int = 40
    trust_proxy: bool = False
    sync_runs: bool = False

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)

    @property
    def access_mode(self) -> AccessMode:
        if self.auth_enabled:
            return "api-key"
        return "dev" if self.dev_mode else "public-demo"

    @property
    def public_demo(self) -> bool:
        return self.access_mode == "public-demo"

    @classmethod
    def from_env(cls) -> ApiSettings:
        return cls(
            api_keys=_env_list("MODERNIZE_API_KEYS"),
            dev_mode=env_bool("MODERNIZE_DEV_MODE", False),
            cors_origins=_env_list("MODERNIZE_CORS_ORIGINS"),
            rate_limit_per_minute=env_int("MODERNIZE_RATE_LIMIT_PER_MINUTE", 120, lo=1, hi=10_000),
            run_limit_per_minute=env_int("MODERNIZE_RUN_LIMIT_PER_MINUTE", 6, lo=1, hi=1_000),
            max_concurrent_runs=env_int("MODERNIZE_MAX_CONCURRENT_RUNS", 2, lo=1, hi=32),
            max_queued_runs=env_int("MODERNIZE_MAX_QUEUED_RUNS", 8, lo=1, hi=256),
            max_runs_kept=env_int("MODERNIZE_MAX_RUNS_KEPT", 200, lo=10, hi=10_000),
            max_steps_cap=env_int("MODERNIZE_MAX_STEPS_CAP", 40, lo=1, hi=100),
            trust_proxy=env_bool("MODERNIZE_TRUST_PROXY", False),
            sync_runs=env_bool("MODERNIZE_SYNC_RUNS", running_serverless()),
        )
