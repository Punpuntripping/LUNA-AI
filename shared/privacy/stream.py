"""Streaming decode buffer for SSE text deltas.

Plan section "Streaming decode buffering": a fake can be split across SSE chunks
(``…هويته 10328`` | ``49275 و…``). The relay feeds text deltas here; the buffer
holds back a chunk tail that ends inside a *potential* run (trailing ASCII /
Arabic-Indic digits, or digits + dash/space; hold-back capped at 32 chars) and
flushes on the first non-run char or on :meth:`finalize`. Decode always runs on
the joined pending text, so fakes split across chunks restore correctly.

This module handles ONLY the text API. Heartbeats / status / non-text SSE events
are routed around the decoder by the wiring layer (not this class's concern).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shared.privacy.codec import PrivacyCodec, TripwireEvent, _is_digit_char

# Chars that can be part of (or join) a run and therefore must be held back when
# they trail a chunk: digits (any supported script) plus dash and space.
_HOLD_EXTRA = frozenset("- ")

DEFAULT_HOLDBACK_CAP = 32


def _is_run_char(ch: str) -> bool:
    return _is_digit_char(ch) or ch in _HOLD_EXTRA


@dataclass
class StreamDecoder:
    """Stateful decode buffer fed SSE text deltas.

    Usage::

        sd = StreamDecoder(codec)
        for delta in sse_text_deltas:
            out = sd.feed(delta)      # emit decoded, safe-to-flush text
            ...
        tail = sd.finalize()          # flush whatever was held back

    ``feed`` and ``finalize`` return decoded text ready to relay. Tripwires and a
    restored-value counter accumulate on the instance for the wiring layer to log.
    """

    codec: PrivacyCodec
    holdback_cap: int = DEFAULT_HOLDBACK_CAP
    _pending: str = field(default="", init=False, repr=False)
    tripwires: list[TripwireEvent] = field(default_factory=list, init=False)
    restored_count: int = field(default=0, init=False)

    # -- API ---------------------------------------------------------------

    def feed(self, delta: str) -> str:
        """Consume a text delta; return decoded text that is safe to emit now."""
        if not delta:
            return ""
        buf = self._pending + delta
        hold_start = self._hold_start(buf)
        to_decode = buf[:hold_start]
        self._pending = buf[hold_start:]
        return self._decode(to_decode)

    def finalize(self) -> str:
        """Stream end: decode + flush everything held back."""
        buf = self._pending
        self._pending = ""
        return self._decode(buf)

    @property
    def pending(self) -> str:
        """Currently held-back (still raw, undecoded) tail. For tests/inspection."""
        return self._pending

    # -- internals ---------------------------------------------------------

    def _decode(self, text: str) -> str:
        if not text:
            return ""
        result = self.codec.decode(text)
        if result.tripwires:
            self.tripwires.extend(result.tripwires)
        self.restored_count += result.restored_count
        return result.text

    def _hold_start(self, buf: str) -> int:
        """Index at which the held-back tail begins.

        The tail is the maximal suffix of run-chars, but only when it contains at
        least one digit (a bare trailing run of dashes/spaces cannot be a partial
        number). Capped at ``holdback_cap`` so a pathological long run cannot pin
        the buffer — a >cap run can only be a non-fake (real fakes are ≤22 digits
        + a few separators), and flushing its prefix is a harmless no-op decode.
        """
        n = len(buf)
        i = n
        while i > 0 and _is_run_char(buf[i - 1]):
            i -= 1
        suffix = buf[i:]
        if not any(_is_digit_char(c) for c in suffix):
            return n  # nothing worth holding
        hold_start = i
        if n - hold_start > self.holdback_cap:
            hold_start = n - self.holdback_cap
        return hold_start
