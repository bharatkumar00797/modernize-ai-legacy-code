from __future__ import annotations

from ..config import Settings
from .base import Provider
from .mock import MockProvider
from .openai_compat import OpenAICompatibleProvider


def make_provider(settings: Settings, name: str | None = None) -> Provider:
    choice = (name or settings.provider).lower()
    if choice == "mock":
        return MockProvider()
    if choice == "openai":
        return OpenAICompatibleProvider(settings)
    raise ValueError(f"unknown provider {choice!r} (expected 'mock' or 'openai')")
