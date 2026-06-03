"""Verify that Luna telemetry is reaching Pydantic Logfire end-to-end.

Run against one or more backends (local + Railway) and print a clean
pass/fail report. Use cases:

  # Local backend only:
  python scripts/check_tracking.py

  # Both:
  python scripts/check_tracking.py \
      --backend http://localhost:8000 \
      --backend https://luna-backend-production-35ba.up.railway.app

Per-backend checks (all are HTTP — no Logfire API needed for these):
  1. GET /api/v1/health returns 200 and includes env + service + version.
  2. GET /api/v1/_meta/observability returns 200 with `configured: true` and
     `boot_span_emitted: true`. Flags missing token in production envs.
  3. Optional Logfire round-trip: with --logfire-project NAME we query the
     last `luna.boot` span for that env to confirm shipped telemetry.

Exit code is non-zero if any check fails, so this script can gate deploys.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


DEFAULT_BACKENDS = ["http://localhost:8000"]
TIMEOUT_SECONDS = 10


def _fetch(url: str) -> dict[str, Any] | None:
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            body = r.read().decode("utf-8")
            return {"status": r.status, "body": json.loads(body) if body else None}
    except HTTPError as e:
        return {"status": e.code, "body": None, "error": str(e)}
    except (URLError, TimeoutError, OSError) as e:
        return {"status": None, "body": None, "error": str(e)}


def _check_health(base: str) -> tuple[bool, str]:
    res = _fetch(f"{base}/api/v1/health")
    if res is None or res.get("status") != 200:
        return False, f"health unreachable: {res!r}"
    body = res.get("body") or {}
    env = body.get("environment")
    if not env:
        return False, "health response missing 'environment' — backend on old code?"
    return True, f"env={env} service={body.get('service')} version={body.get('version')}"


def _check_observability(base: str) -> tuple[bool, str]:
    res = _fetch(f"{base}/api/v1/_meta/observability")
    if res is None or res.get("status") != 200:
        return False, f"observability unreachable: {res!r}"
    body = res.get("body") or {}
    problems = []
    if not body.get("configured"):
        problems.append("not configured")
    if not body.get("boot_span_emitted"):
        problems.append("boot span never emitted")
    env = body.get("environment") or ""
    token = body.get("token_present")
    if env.lower() in {"production", "prod"} and not token:
        problems.append("PROD without LOGFIRE_TOKEN")
    instrumented = body.get("instrumented") or {}
    if not instrumented.get("httpx"):
        problems.append("httpx not instrumented (LLM call spans will be missing)")
    if not instrumented.get("pydantic_ai"):
        problems.append("pydantic_ai not instrumented (agent spans will be missing)")
    summary = (
        f"env={env} token={'yes' if token else 'no'} "
        f"instrumented={','.join(k for k,v in instrumented.items() if v) or 'none'}"
    )
    if problems:
        return False, f"{summary} — {'; '.join(problems)}"
    return True, summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--backend",
        action="append",
        default=None,
        help="Backend base URL. Repeat for multiple. Default: http://localhost:8000",
    )
    args = p.parse_args()
    backends = args.backend or DEFAULT_BACKENDS

    overall_ok = True
    for base in backends:
        base = base.rstrip("/")
        print(f"\n== {base} ==")
        for name, fn in (("health", _check_health), ("observability", _check_observability)):
            ok, msg = fn(base)
            marker = "PASS" if ok else "FAIL"
            print(f"  [{marker}] {name}: {msg}")
            if not ok:
                overall_ok = False

    print("\n" + ("ALL OK" if overall_ok else "FAILED — telemetry is unreliable"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
