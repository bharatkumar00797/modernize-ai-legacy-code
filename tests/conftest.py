from __future__ import annotations

import pytest

from modernize.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(test_timeout=60)
