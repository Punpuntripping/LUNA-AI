"""Per-run JSON logger for artifact_summarizer.

Mirrors the deep_search pattern: one JSON file per run, written under
``agents/memory/artifact_summarizer/logs/{timestamp}_{slug}.json``.

Logging is best-effort — every method swallows I/O failures so a log write
never fails the agent run.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).parent / "logs"
_SLUG_MAX = 32


class ArtifactSummaryLogger:
    """Per-run JSON logger.

    Construct one per agent run. ``write_run`` writes the full record.
    """

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or f"{int(time.time() * 1000)}"

    def write_run(self, input: Any, output: Any, duration_s: float) -> None:
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("artifact_summarizer logger: mkdir failed: %s", exc)
            return

        slug = _slugify(getattr(input, "title", "") or self.run_id)
        path = _LOG_DIR / f"{self.run_id}_{slug}.json"

        payload = {
            "run_id": self.run_id,
            "duration_s": round(duration_s, 3),
            "input": _to_jsonable(input),
            "output": _to_jsonable(output),
        }
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.debug("artifact_summarizer logger: write failed: %s", exc)


def _slugify(text: str) -> str:
    """ASCII-ish slug so the filename is safe across OSes."""
    text = text.strip().lower()
    text = re.sub(r"[^\w؀-ۿ-]+", "_", text)  # keep Arabic + word chars
    text = text.strip("_")[:_SLUG_MAX]
    return text or "run"


def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:  # noqa: BLE001
            pass
    return obj
