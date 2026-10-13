#!/usr/bin/env python3
"""End-to-end smoke test against a running server (stdlib only).

Usage: python3 scripts/smoke_test.py [BASE_URL] [--api-key KEY]

For every packaged example: start a run, wait until finished (works with background and sync
mode), and check the expected outcome. Exits non-zero on the first failure.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any

# Expected: example_id -> (min_kept, must_be_green)
EXPECTED = {
    "py2-style-utils": (5, True),
    "insecure-config": (2, True),
    "old-api-helpers": (1, True),
}


class Client:
    def __init__(self, base: str, api_key: str | None) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("X-API-Key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read().decode()
                ctype = res.headers.get("content-type", "")
                payload = json.loads(raw) if "json" in ctype else raw
                return res.status, payload, res.headers
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"FAIL {method} {path}: HTTP {exc.code} {exc.read()[:300]!r}") from exc


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL {message}")


def wait_done(client: Client, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while True:
        _, ev, _ = client.request("GET", f"/api/runs/{run_id}/events?since=0")
        if ev["done"]:
            _, detail, _ = client.request("GET", f"/api/runs/{run_id}")
            return detail
        check(time.monotonic() < deadline, f"{run_id}: run timed out")
        time.sleep(0.5)


def run_example(client: Client, name: str) -> None:
    min_kept, must_green = EXPECTED[name]
    status, run, _ = client.request("POST", "/api/runs", {"example": name})
    check(status in (200, 202), f"{name}: create returned {status}")
    detail = (
        run if status == 200 and run.get("state") == "finished" else wait_done(client, run["id"])
    )
    check(detail["state"] == "finished", f"{name}: state {detail['state']} ({detail.get('error')})")
    check(detail["kept"] >= min_kept, f"{name}: kept {detail['kept']} < {min_kept}")
    if must_green:
        check(detail.get("tests_green") is True, f"{name}: expected GREEN suite")
    _, md, _ = client.request("GET", f"/api/runs/{detail['id']}/report.md")
    check(isinstance(md, str) and len(md) > 40, f"{name}: empty report.md")
    _, patch, _ = client.request("GET", f"/api/runs/{detail['id']}/patch.diff")
    check(isinstance(patch, str), f"{name}: missing patch.diff")
    print(
        f"PASS {name}: kept={detail['kept']} rejected={detail['rejected']} "
        f"smells {detail['smells_before']}->{detail['smells_after']} "
        f"{'GREEN' if detail.get('tests_green') else 'RED'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()
    client = Client(args.base, args.api_key)

    deadline = time.monotonic() + 60
    while True:
        try:
            _, health, _ = client.request("GET", "/healthz")
            break
        except (urllib.error.URLError, ConnectionError, SystemExit):
            check(time.monotonic() < deadline, "server did not become healthy")
            time.sleep(1)
    check(health["status"] == "ok", "health status")
    _, page, headers = client.request("GET", "/")
    check("Modernize AI" in page, "dashboard page")
    check("default-src 'self'" in headers.get("content-security-policy", ""), "CSP header")
    _, config, _ = client.request("GET", "/api/config")
    print(f"PASS health + dashboard: v{health['version']}, mode {config['access_mode']}")
    missing = sorted(set(EXPECTED) - set(config["examples"]))
    check(not missing, f"examples not served: {missing}")
    for name in sorted(EXPECTED):
        run_example(client, name)
    print(f"ALL PASS ({len(EXPECTED)} examples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
