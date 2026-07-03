"""Per-user PII mapping I/O against the ``pii_mappings`` table.

Table (created in a parallel phase — migration ``087_pii_mappings.sql``)::

    id          uuid PK
    user_id     uuid
    kind        text            -- 'number' | 'email'
    real_value  text            -- normalized-ASCII form (the codec's key)
    fake_value  text
    created_at  timestamptz
    UNIQUE (user_id, real_value)
    UNIQUE (user_id, fake_value)

RLS is deny-all; this module uses the service-role sync client only (the house
pattern — see ``shared/db/client.py``: sync Supabase client is the established
choice, used even inside async handlers).

Concurrency (plan "Concurrency & storage"):
  * Load the full mapping in ONE select (both directions).
  * Insert a new fake with conflict handling:
      - UNIQUE(user_id, real_value) violation → a concurrent turn already created
        a fake for this real: re-select and USE THEIRS (never two fakes for one
        real). The codec's in-memory maps are reconciled to the DB value.
      - UNIQUE(user_id, fake_value) violation → our fake collided with an existing
        DIFFERENT real's fake: regenerate the fake (keep-prefix + length preserved
        via the codec generators) and retry.

Residual (documented): a fake regenerated at persist time differs from the fake
already emitted into the outgoing prompt for that turn. This only happens on a
genuine concurrent fake collision (rare — the codec already dedupes against the
loaded set) and is accepted per the plan's "Never two fakes for one real" rule.
"""
from __future__ import annotations

import logging
from typing import Any

from shared.privacy.codec import (
    NewMapping,
    PrivacyCodec,
    generate_email_fake,
    generate_number_fake,
)

logger = logging.getLogger(__name__)

_TABLE = "pii_mappings"
_MAX_FAKE_RETRIES = 8

# Postgres unique_violation.
_UNIQUE_VIOLATION = "23505"


class PiiMappingStore:
    """Load + upsert per-user real↔fake mappings (service-role sync client)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    # -- load --------------------------------------------------------------

    def load(self, user_id: str) -> tuple[dict[str, str], dict[str, str]]:
        """Return ``(real_to_fake, fake_to_real)`` for a user in one SELECT."""
        res = (
            self.client.table(_TABLE)
            .select("kind,real_value,fake_value")
            .eq("user_id", user_id)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        real_to_fake: dict[str, str] = {}
        fake_to_real: dict[str, str] = {}
        for row in rows:
            real = row.get("real_value")
            fake = row.get("fake_value")
            if real is None or fake is None:
                continue
            real_to_fake[real] = fake
            fake_to_real[fake] = real
        return real_to_fake, fake_to_real

    def load_codec(
        self, user_id: str, *, enabled: bool = True, rng=None
    ) -> PrivacyCodec:
        """Convenience: build a :class:`PrivacyCodec` from the stored mapping."""
        real_to_fake, fake_to_real = self.load(user_id)
        return PrivacyCodec(real_to_fake, fake_to_real, enabled=enabled, rng=rng)

    # -- persist -----------------------------------------------------------

    def persist_new(
        self,
        user_id: str,
        new_mappings: list[NewMapping],
        codec: PrivacyCodec | None = None,
    ) -> None:
        """Persist newly-created fakes with conflict handling.

        ``codec`` (optional) is reconciled in-memory when a concurrent turn won a
        real_value race, so the rest of this turn stays consistent with the DB.
        """
        for mapping in new_mappings:
            self._persist_one(user_id, mapping, codec)

    def _persist_one(
        self,
        user_id: str,
        mapping: NewMapping,
        codec: PrivacyCodec | None,
    ) -> None:
        real = mapping.real_value
        fake = mapping.fake_value
        kind = mapping.kind

        for _attempt in range(_MAX_FAKE_RETRIES):
            try:
                self.client.table(_TABLE).insert(
                    {
                        "user_id": user_id,
                        "kind": kind,
                        "real_value": real,
                        "fake_value": fake,
                    }
                ).execute()
                return  # inserted cleanly
            except Exception as exc:  # noqa: BLE001 — inspect + branch below
                which = _classify_unique_violation(exc)
                if which == "real":
                    # A concurrent turn already stored a fake for this real.
                    winner = self._select_fake_for_real(user_id, real)
                    if winner is not None and codec is not None:
                        _reconcile(codec, real, fake, winner)
                    return
                if which == "fake":
                    # Our fake collides with a different real's fake → regenerate.
                    avoid = self._known_fakes(user_id, codec)
                    fake = _regenerate_fake(kind, real, avoid, codec)
                    if codec is not None:
                        _reconcile_fake(codec, real, fake)
                    continue
                # Not a unique violation we handle — surface it.
                raise
        logger.warning(
            "pii_mappings: exhausted fake retries for user=%s kind=%s", user_id, kind
        )

    # -- helpers -----------------------------------------------------------

    def _select_fake_for_real(self, user_id: str, real: str) -> str | None:
        res = (
            self.client.table(_TABLE)
            .select("fake_value")
            .eq("user_id", user_id)
            .eq("real_value", real)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return rows[0].get("fake_value")
        return None

    def _known_fakes(self, user_id: str, codec: PrivacyCodec | None) -> set[str]:
        fakes: set[str] = set()
        if codec is not None:
            fakes |= set(codec.fake_to_real.keys())
        try:
            res = (
                self.client.table(_TABLE)
                .select("fake_value")
                .eq("user_id", user_id)
                .execute()
            )
            for row in getattr(res, "data", None) or []:
                fv = row.get("fake_value")
                if fv:
                    fakes.add(fv)
        except Exception as exc:  # noqa: BLE001 — best-effort avoidance set
            logger.debug("pii_mappings: fake-avoidance reselect failed: %s", exc)
        return fakes


def _classify_unique_violation(exc: Exception) -> str | None:
    """Return 'real', 'fake', or None for a (user_id, *) unique violation.

    Robust against both PostgREST error shapes:
      * ``details`` = ``Key (user_id, real_value)=(...) already exists.`` — the
        column name appears verbatim (``real_value`` / ``fake_value``).
      * ``message`` = ``... unique constraint "pii_mappings_user_real_uniq"`` —
        only the constraint name (contains ``real`` / ``fake`` but not the
        ``_value`` suffix). Migration 087 names them ``*_user_real_uniq`` /
        ``*_user_fake_uniq``; the real/fake tokens are disjoint across the two
        constraints (a fake-conflict error never mentions ``real`` and vice
        versa), so a token check disambiguates when ``details`` is absent.
    """
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or ""
    details = getattr(exc, "details", None) or ""
    blob = f"{message} {details} {exc}".lower()
    is_unique = code == _UNIQUE_VIOLATION or "duplicate key" in blob or "23505" in blob
    if not is_unique:
        return None
    # Prefer the explicit column name from PostgREST ``details``.
    if "real_value" in blob:
        return "real"
    if "fake_value" in blob:
        return "fake"
    # Fall back to the disjoint constraint-name token.
    has_real = "real" in blob
    has_fake = "fake" in blob
    if has_real and not has_fake:
        return "real"
    if has_fake and not has_real:
        return "fake"
    return None


def _regenerate_fake(
    kind: str, real: str, avoid: set[str], codec: PrivacyCodec | None
) -> str:
    import random

    rng = codec.rng if codec is not None else random.SystemRandom()
    if kind == "email":
        return generate_email_fake(real, rng, avoid)
    return generate_number_fake(real, rng, avoid)


def _reconcile(codec: PrivacyCodec, real: str, stale_fake: str, winner: str) -> None:
    """Adopt the DB's winning fake for ``real`` in the codec's in-memory maps."""
    if stale_fake in codec.fake_to_real:
        del codec.fake_to_real[stale_fake]
    codec.real_to_fake[real] = winner
    codec.fake_to_real[winner] = real
    codec._index_fake(winner)  # keep the tripwire index consistent


def _reconcile_fake(codec: PrivacyCodec, real: str, new_fake: str) -> None:
    old = codec.real_to_fake.get(real)
    if old is not None and old in codec.fake_to_real:
        del codec.fake_to_real[old]
    codec.real_to_fake[real] = new_fake
    codec.fake_to_real[new_fake] = real
    codec._index_fake(new_fake)
