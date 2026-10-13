from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import modernize
from modernize.api import create_app
from modernize.api.settings import ApiSettings
from modernize.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject() -> None:
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"]["version"] == modernize.__version__
    assert modernize.__version__ == "1.0.0"


def test_version_is_reported_by_api_and_cli(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app(ApiSettings(dev_mode=True))
    with TestClient(app) as client:
        assert client.get("/healthz").json()["version"] == modernize.__version__
        assert client.get("/api/config").json()["version"] == modernize.__version__
        assert client.get("/openapi.json").json()["info"]["version"] == modernize.__version__
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.strip() == f"modernize {modernize.__version__}"
