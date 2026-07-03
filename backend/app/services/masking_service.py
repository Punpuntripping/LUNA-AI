"""Per-turn PrivacyCodec construction, pipeline-wide access, and self-audit.

Phase 3 backend wiring of the identifier-masking codec (وضع السرية). This module
is the SINGLE place a turn's :class:`~shared.privacy.PrivacyCodec` is built and
the SINGLE handle downstream pipeline code (history encode, workspace_items
publishers decode) uses to reach it. See ``.claude/plans/identifier_masking.md``
("The invariant", "Architecture — where it hooks", "Concurrency & storage").

Threading model (Phase 3b reuses this verbatim):

  * ``message_service`` builds the codec once per turn via :func:`build_turn_codec`
    and passes it as ``handle_message(codec=...)``. It ALSO keeps the reference
    for its own SSE stream-decode + persist-decode (which must NOT depend on the
    ContextVar — the timeout path runs after handle_message's context is gone).
  * ``handle_message`` publishes the codec on a ContextVar (:func:`set_active_codec`)
    for the duration of the turn. ``_load_recent_messages`` (history encode) and
    the workspace_items publishers read it via :func:`active_codec`. When
    ``handle_message`` is called WITHOUT a codec (the blog API front door,
    ``deepsearch_api/generate.py``), it builds one internally the same way.
  * DECODE is ALWAYS active; only ENCODE honours ``codec.enabled``.
    :func:`build_turn_codec` loads the mapping table EVEN WHEN DISABLED so a
    paused run captured while masking was ON and resumed after OFF still decodes.

Persist discipline: :func:`persist_new_mappings` writes only the fakes created
since the previous call (a per-codec watermark) so the two intake persist points
(question encode → before the router LLM; history encode → before the planner
LLM) never re-insert the same mapping. ``persist_new`` with an empty list is a
cheap no-op.

Everything here is resilient: any failure degrades to a no-op passthrough so the
masking layer can never take down a chat turn.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Optional

from shared.config import get_settings
from shared.observability import get_logfire
from shared.privacy import (
    DecodeResult,
    PiiMappingStore,
    PrivacyCodec,
    TripwireEvent,
)

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# The turn's active codec, published by handle_message for the whole pipeline.
# Default None → every helper degrades to a passthrough when unset (e.g. an
# agent surface reached outside a masked turn, or a build failure).
_ACTIVE_CODEC: contextvars.ContextVar[Optional[PrivacyCodec]] = contextvars.ContextVar(
    "luna_active_privacy_codec", default=None
)

# Wiring-owned bookkeeping attribute stamped on the codec instance so
# persist_new_mappings only flushes the delta since the last flush.
_WATERMARK_ATTR = "_luna_persisted_upto"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_turn_codec(supabase, user_id: str) -> PrivacyCodec:
    """Build this turn's codec.

    ``enabled = PRIVACY_MASKING_ENABLED (env kill-switch) AND the user's
    privacy_masking preference``. The mapping table is loaded UNCONDITIONALLY
    (decode is always-on), so a disabled codec can still restore fakes captured
    while masking was previously ON.

    Resilient: a broken preference read yields ``enabled=False``; a broken
    mapping load yields an empty codec. Either way encode becomes a byte-identical
    passthrough and the turn proceeds.

    ``user_id`` is the resolved ``users.user_id`` (NOT the Supabase auth_id) —
    the same value the pipeline and ``get_detail_level`` use.
    """
    # Local import avoids a module-load cycle (preferences_service → case_service).
    from backend.app.services.preferences_service import get_privacy_masking

    try:
        enabled = bool(get_settings().PRIVACY_MASKING_ENABLED) and get_privacy_masking(
            supabase, user_id
        )
    except Exception:
        logger.warning(
            "build_turn_codec: flag/preference read failed; masking disabled",
            exc_info=True,
        )
        enabled = False

    try:
        return PiiMappingStore(supabase).load_codec(user_id, enabled=enabled)
    except Exception:
        logger.warning(
            "build_turn_codec: mapping load failed; empty codec (enabled=%s)",
            enabled,
            exc_info=True,
        )
        return PrivacyCodec(enabled=enabled)


# ---------------------------------------------------------------------------
# ContextVar plumbing
# ---------------------------------------------------------------------------


def set_active_codec(codec: Optional[PrivacyCodec]) -> contextvars.Token:
    """Publish ``codec`` as the turn's active codec. Returns the reset token."""
    return _ACTIVE_CODEC.set(codec)


def reset_active_codec(token: contextvars.Token) -> None:
    """Reset the active-codec ContextVar. Swallows a cross-context reset error."""
    try:
        _ACTIVE_CODEC.reset(token)
    except Exception:
        logger.debug("reset_active_codec: token reset failed (cross-context)", exc_info=True)


def active_codec() -> Optional[PrivacyCodec]:
    """The turn's active codec, or None outside a masked turn."""
    return _ACTIVE_CODEC.get()


# ---------------------------------------------------------------------------
# Persist (delta-only)
# ---------------------------------------------------------------------------


def persist_new_mappings(supabase, user_id: str, codec: Optional[PrivacyCodec]) -> int:
    """Persist fakes created since the last call, IMMEDIATELY (before the LLM).

    A pause/resume in a fresh process reloads the codec from the DB, so a fake
    emitted into an outgoing prompt but never persisted would be undecodable.
    Only the delta since the previous flush is written (per-codec watermark), so
    calling this at both intake persist points is cheap and idempotent. Returns
    the number of new mappings flushed on this call.
    """
    if codec is None:
        return 0
    all_new = codec.new_mappings  # cumulative, append-only snapshot
    watermark = int(getattr(codec, _WATERMARK_ATTR, 0) or 0)
    delta = all_new[watermark:]
    if not delta:
        return 0
    try:
        PiiMappingStore(supabase).persist_new(user_id, delta, codec)
    except Exception:
        logger.warning("persist_new_mappings: persist failed", exc_info=True)
    finally:
        # Advance past what we attempted regardless of per-mapping outcome — the
        # values are already in the outgoing prompt; store.persist_new already
        # handles conflicts internally, and the two-point flush has no third try.
        setattr(codec, _WATERMARK_ATTR, len(codec.new_mappings))
    return len(delta)


# ---------------------------------------------------------------------------
# Encode + self-audit (Layer 4)
# ---------------------------------------------------------------------------


def encode_active(text: str) -> str:
    """Encode ``text`` via the turn's ACTIVE codec (وضع السرية encode seam).

    Passthrough-safe on every degenerate input: ``None`` / empty text, no active
    turn codec (outside a masked turn — tests, non-LLM callers), a disabled
    codec (``enabled=False`` → :meth:`PrivacyCodec.encode` is itself a byte-
    identical passthrough), or an encode hiccup. Mirrors the inline pattern used
    by ``messages_to_history`` / ``_encode_template_titles`` / ``_encode_unfold_output``
    so the eager router/planner/writer_planner context surfaces (summaries,
    titles, case memory, attached content_md) can be masked at prompt assembly
    with a single call.

    Minted fakes accrue to the active codec; the CALLER persists them via
    :func:`persist_new_mappings` BEFORE the consuming LLM run (a fresh-process
    pause/resume reloads the codec from the DB). This function never persists —
    it is pure w.r.t. the DB, exactly like the render-side encoders it mirrors.
    """
    if not text:
        return text
    codec = active_codec()
    if codec is None:
        return text
    try:
        return codec.encode(text)
    except Exception:  # noqa: BLE001
        logger.debug("encode_active failed — returning raw text", exc_info=True)
        return text


def audit_encoded(encoded: str, codec: Optional[PrivacyCodec]) -> None:
    """Re-run the detector on encoded text; any non-fake hit → leak_candidate.

    Value-free (count + kinds only). This turns "did we mask everything?" from a
    test-time question into a production metric (plan Layer 4).
    """
    if codec is None or not encoded:
        return
    try:
        hits = codec.audit(encoded)
        if hits:
            _logfire.warning(
                "masking.leak_candidate",
                count=len(hits),
                kinds=sorted({h.kind for h in hits}),
            )
    except Exception:
        logger.debug("audit_encoded failed", exc_info=True)


def emit_encoded_count(count: int) -> None:
    """Emit ``masking.encoded_count`` (mappings applied/created) when non-zero."""
    if not count:
        return
    try:
        _logfire.info("masking.encoded_count", count=int(count))
    except Exception:
        logger.debug("emit_encoded_count failed", exc_info=True)


def emit_decode_telemetry(restored_count: int, tripwires) -> None:
    """Emit decode counters: ``masking.decode_restored_count`` +
    ``masking.tripwire_hit``. Both value-free (counts + kinds)."""
    try:
        if restored_count:
            _logfire.info("masking.decode_restored_count", count=int(restored_count))
        tw = list(tripwires or [])
        if tw:
            _logfire.warning(
                "masking.tripwire_hit",
                count=len(tw),
                kinds=sorted({t.kind for t in tw}),
            )
    except Exception:
        logger.debug("emit_decode_telemetry failed", exc_info=True)


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------


def decode_text(codec: Optional[PrivacyCodec], text: str, *, emit: bool = True) -> str:
    """Decode ``text`` with an explicit codec. No-op on a None codec / empty text.

    ``emit`` controls decode telemetry — set False on the message_service persist
    decode (the SSE stream already emitted counters for the same text) to avoid
    double counting.
    """
    if codec is None or not text:
        return text
    result: DecodeResult = codec.decode(text)
    if emit:
        emit_decode_telemetry(result.restored_count, result.tripwires)
    return result.text


def decode_for_persist(text: str) -> str:
    """Decode via the ACTIVE turn codec — used by the workspace_items publishers,
    which run inside handle_message where the ContextVar is reliably set. Enforces
    the store-real invariant: DB rows never contain fakes.
    """
    return decode_text(active_codec(), text, emit=True)


__all__ = [
    "build_turn_codec",
    "set_active_codec",
    "reset_active_codec",
    "active_codec",
    "persist_new_mappings",
    "encode_active",
    "audit_encoded",
    "emit_encoded_count",
    "emit_decode_telemetry",
    "decode_text",
    "decode_for_persist",
    "TripwireEvent",
]
