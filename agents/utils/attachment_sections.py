"""Wait for the segmenter, then load the stored sections — for the critical consumers.

Plan: ``.claude/plans/attachment_segmentation.md`` §4.6.4 / §4.7 and
``.claude/plans/helper_frame.md`` §5.6. Called from
``agents/deep_search_v4/orchestrator.py::run_retrieval`` (one wait for both
expanders and the aggregator) and ``agents/writer_planner/walkers.py``.

The wait is the helper frame's ``ensure_ready("segmenter", …)``: up to the
segmenter's ceiling (5 minutes), never cancelling it. Whatever is stored after
the wait is loaded — an item with no usable map simply has no entry, and the
delivery code falls back to the document's full text within the caps.

Never raises: any failure returns what was loaded so far (possibly nothing).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

from agents.utils.attachment_delivery import Section, attach_pins, sections_from_rows

logger = logging.getLogger(__name__)

SEGMENTER_HELPER = "segmenter"
#: completion_search.md §4.6.2 — what an attachment cites, resolved to official articles.
CITATION_PINS_HELPER = "citation_pins"


def _encode_sections(sections: list[Section]) -> list[Section]:
    """Encode ``text`` / ``what`` with the turn's codec (وضع السرية) — the deep-search
    recipients read the planner's encoded snapshots, so the sections must match.
    Identity when masking is off or no codec is active."""
    try:
        from backend.app.services.masking_service import active_codec, encode_active
    except Exception:  # noqa: BLE001 — masking unavailable = nothing to encode
        return sections
    if active_codec() is None:
        return sections
    # ``replace`` so every other field travels as it is — above all ``pins``: the
    # official text of a law is public, and encoding it would swap digits inside it.
    return [
        replace(
            s,
            what=encode_active(s.what),
            text=encode_active(s.text),
            # free text that can carry a name — encoded like ``what``
            owner=encode_active(s.owner) if s.owner else "",
            brought_by=encode_active(s.brought_by) if s.brought_by else "",
        )
        for s in sections
    ]


def _pins_on(user_id: str) -> bool:
    try:
        from agents.utils.citation_pin_flags import pin_delivery_enabled

        return pin_delivery_enabled(user_id)
    except Exception:  # noqa: BLE001 — off is today's behaviour
        return False


async def wait_and_load_sections(
    item_ids: Iterable[str],
    *,
    consumer: str,
    supabase: Any,
    user_id: str,
    on_wait: Callable[[], Any] | None = None,
    encode: bool = False,
) -> tuple[dict[str, list[Section]], dict[str, Any]]:
    """Wait for the segmenter on ``item_ids`` as ``consumer``, then load their sections.

    Returns ``(sections_by_id, telemetry)``. ``encode=True`` masks the loaded
    text with the active turn codec (deep search); the writer passes ``False``
    because its executor encodes the whole package itself.
    """
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    telemetry: dict[str, Any] = {"segments.requested": len(ids)}
    if not ids:
        return {}, telemetry

    pins_on = _pins_on(user_id)
    try:
        from agents.helpers import ensure_ready

        def wait(helper: str):
            return ensure_ready(
                helper,
                ids,
                consumer=consumer,  # type: ignore[arg-type]
                supabase=supabase,
                user_id=user_id,
                on_wait=on_wait,
            )

        # ONE combined wait: the two helpers start together and neither needs the other.
        waits = [wait(SEGMENTER_HELPER)] + ([wait(CITATION_PINS_HELPER)] if pins_on else [])
        results = await asyncio.gather(*waits, return_exceptions=True)
        readiness = results[0]
        if isinstance(readiness, BaseException):
            raise readiness
        # Flat strings, not a dict — this lands on Logfire span attributes.
        telemetry["segments.readiness"] = sorted(
            f"{str(iid)[:8]}:{getattr(r, 'status', r)}" for iid, r in (readiness or {}).items()
        )
        if pins_on and not isinstance(results[1], BaseException):
            telemetry["pins.readiness"] = sorted(
                f"{str(iid)[:8]}:{getattr(r, 'status', r)}" for iid, r in (results[1] or {}).items()
            )
    except Exception:  # noqa: BLE001 — a failed wait still lets us read what exists
        logger.warning("wait_and_load_sections: ensure_ready failed", exc_info=True)

    try:
        from agents.memory.attachment_segmenter.store import load_segment_maps

        maps = await asyncio.to_thread(load_segment_maps, supabase, ids, user_id=user_id)
    except Exception:  # noqa: BLE001
        logger.warning("wait_and_load_sections: load failed", exc_info=True)
        return {}, telemetry

    out: dict[str, list[Section]] = {}
    for item_id, stored in (maps or {}).items():
        sections = sections_from_rows(getattr(stored, "segments", None) or [])
        if sections:
            out[str(item_id)] = _encode_sections(sections) if encode else sections
    telemetry["segments.loaded"] = sorted(out)

    if pins_on:
        # After encoding, on purpose: the pinned text is attached as it is. A document
        # with no sections gets a carrier section so its articles follow its text.
        try:
            from agents.memory.citation_pins.store import load_pins

            pins = await asyncio.to_thread(load_pins, supabase, ids, user_id=user_id)
            for item_id, item_pins in (pins or {}).items():
                attached = attach_pins(out.get(item_id) or [], item_pins)
                if attached:
                    out[item_id] = attached
            telemetry["pins.items"] = len(pins or {})
            telemetry["pins.articles"] = sum(len({p.article_id for p in v}) for v in (pins or {}).values())
        except Exception:  # noqa: BLE001 — sections without pinned text is the fallback
            logger.warning("wait_and_load_sections: pin load failed", exc_info=True)
    return out, telemetry


__all__ = ["CITATION_PINS_HELPER", "SEGMENTER_HELPER", "wait_and_load_sections"]
