"""Settings read from MODERNIZE_* environment variables (secrets only ever via env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

ProviderName = Literal["mock", "openai"]


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class Settings:
    provider: ProviderName = "mock"
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    llm_timeout: int = 30
    llm_retries: int = 2
    allow_model_diffs: bool = False
    max_steps: int = 20
    test_timeout: int = 60
    isolate_network: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        provider = os.environ.get("MODERNIZE_PROVIDER", "mock").strip().lower() or "mock"
        if provider not in ("mock", "openai"):
            raise ValueError(f"MODERNIZE_PROVIDER must be 'mock' or 'openai', got {provider!r}")
        return cls(
            provider="openai" if provider == "openai" else "mock",
            model=os.environ.get("MODERNIZE_MODEL", cls.model).strip() or cls.model,
            base_url=os.environ.get("MODERNIZE_BASE_URL", cls.base_url).strip() or cls.base_url,
            api_key=os.environ.get("MODERNIZE_API_KEY", "").strip(),
            llm_timeout=env_int("MODERNIZE_LLM_TIMEOUT", 30, lo=1, hi=300),
            llm_retries=env_int("MODERNIZE_LLM_RETRIES", 2, lo=0, hi=5),
            allow_model_diffs=env_bool("MODERNIZE_ALLOW_MODEL_DIFFS", False),
            max_steps=env_int("MODERNIZE_MAX_STEPS", 20, lo=1, hi=100),
            test_timeout=env_int("MODERNIZE_TEST_TIMEOUT", 60, lo=5, hi=600),
            isolate_network=env_bool("MODERNIZE_ISOLATE_NETWORK", True),
        )
