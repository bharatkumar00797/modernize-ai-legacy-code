"""OpenAI-compatible chat completions client (OpenAI, Groq, Ollama, vLLM, ...)."""

from __future__ import annotations

import time

import httpx

from ..config import Settings
from .base import DIFF_ADDENDUM, SYSTEM_PROMPT, RankRequest, build_prompt


class ProviderError(RuntimeError):
    pass


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("MODERNIZE_BASE_URL must be https (or a localhost endpoint)")
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.llm_timeout)

    def rank(self, request: RankRequest) -> str:
        system = SYSTEM_PROMPT + (DIFF_ADDENDUM if request.allow_diff else "")
        payload = {
            "model": self.settings.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": build_prompt(request)},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        last: Exception | None = None
        for attempt in range(self.settings.llm_retries + 1):
            try:
                resp = self.client.post(url, json=payload, headers=headers)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise ProviderError(f"transient HTTP {resp.status_code}")
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return str(content)[:100_000]
            except (httpx.HTTPError, ProviderError, KeyError, IndexError, ValueError) as exc:
                last = exc
                if attempt < self.settings.llm_retries:
                    time.sleep(min(2.0, 0.25 * 2**attempt))
        raise ProviderError(f"model request failed: {type(last).__name__}")
