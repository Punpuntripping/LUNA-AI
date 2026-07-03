# -*- coding: utf-8 -*-
"""PiiMappingStore tests with a mocked Supabase sync client (no live DB).

The fake client mimics the ``client.table(...).select/insert/eq/limit/execute``
chain and enforces the two UNIQUE constraints so the conflict-retry paths are
exercised for real.
"""
import random

import pytest

from shared.privacy.codec import NewMapping, PrivacyCodec
from shared.privacy.store import PiiMappingStore, _classify_unique_violation


class _FakeAPIError(Exception):
    """Mimics postgrest.exceptions.APIError (has .code / .message / .details)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = ""


class _Query:
    def __init__(self, table):
        self._table = table
        self._filters = {}
        self._select = None
        self._insert = None
        self._limit = None

    def select(self, cols):
        self._select = cols
        return self

    def insert(self, row):
        self._insert = row
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self._insert is not None:
            self._table._insert(self._insert)
            return _Result([self._insert])
        rows = self._table._select(self._filters)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self):
        self.rows = []
        self.insert_calls = 0

    def _insert(self, row):
        self.insert_calls += 1
        user = row["user_id"]
        for existing in self.rows:
            if existing["user_id"] != user:
                continue
            if existing["real_value"] == row["real_value"]:
                # Realistic PostgREST shape: constraint name only in `message`
                # (migration 087 names it *_user_real_uniq — no "real_value"),
                # so the classifier must fall back to the disjoint token.
                raise _FakeAPIError(
                    "23505",
                    'duplicate key value violates unique constraint '
                    '"pii_mappings_user_real_uniq"',
                )
            if existing["fake_value"] == row["fake_value"]:
                raise _FakeAPIError(
                    "23505",
                    'duplicate key value violates unique constraint '
                    '"pii_mappings_user_fake_uniq"',
                )
        self.rows.append(dict(row))

    def _select(self, filters):
        out = []
        for r in self.rows:
            if all(r.get(k) == v for k, v in filters.items()):
                out.append(dict(r))
        return out


class _FakeClient:
    def __init__(self):
        self._t = _FakeTable()

    def table(self, name):
        assert name == "pii_mappings"
        return _Query(self._t)


# ---------------------------------------------------------------------------


def test_load_returns_both_directions():
    client = _FakeClient()
    client._t.rows = [
        {"user_id": "u1", "kind": "number", "real_value": "1023456789",
         "fake_value": "1023835840"},
        {"user_id": "u1", "kind": "email", "real_value": "a@b.com",
         "fake_value": "z@example.com"},
        {"user_id": "u2", "kind": "number", "real_value": "999",
         "fake_value": "888"},  # other user, must not leak
    ]
    store = PiiMappingStore(client)
    r2f, f2r = store.load("u1")
    assert r2f == {"1023456789": "1023835840", "a@b.com": "z@example.com"}
    assert f2r == {"1023835840": "1023456789", "z@example.com": "a@b.com"}
    assert "999" not in r2f


def test_load_codec_builds_working_codec():
    client = _FakeClient()
    client._t.rows = [
        {"user_id": "u1", "kind": "number", "real_value": "1023456789",
         "fake_value": "1023835840"},
    ]
    store = PiiMappingStore(client)
    codec = store.load_codec("u1")
    assert codec.decode("رقم 1023835840").text == "رقم 1023456789"


def test_persist_new_inserts_cleanly():
    client = _FakeClient()
    store = PiiMappingStore(client)
    store.persist_new("u1", [
        NewMapping("number", "1023456789", "1023835840"),
        NewMapping("email", "a@b.com", "z@example.com"),
    ])
    assert len(client._t.rows) == 2
    assert client._t.insert_calls == 2


def test_real_value_conflict_reuses_winner():
    """A concurrent turn already stored a fake for this real → adopt theirs."""
    client = _FakeClient()
    # concurrent winner already in the table
    client._t.rows = [
        {"user_id": "u1", "kind": "number", "real_value": "1023456789",
         "fake_value": "1023111111"},
    ]
    codec = PrivacyCodec()
    codec.real_to_fake = {"1023456789": "1023835840"}  # our stale fake
    codec.fake_to_real = {"1023835840": "1023456789"}
    store = PiiMappingStore(client)
    store.persist_new("u1", [NewMapping("number", "1023456789", "1023835840")], codec)
    # no duplicate row inserted
    reals = [r for r in client._t.rows if r["real_value"] == "1023456789"]
    assert len(reals) == 1
    # codec reconciled to the DB winner
    assert codec.real_to_fake["1023456789"] == "1023111111"
    assert codec.fake_to_real["1023111111"] == "1023456789"
    assert "1023835840" not in codec.fake_to_real


def test_fake_value_conflict_regenerates_and_retries():
    """Our fake collides with a DIFFERENT real's fake → regenerate + retry."""
    client = _FakeClient()
    # a different real already owns the fake we want
    client._t.rows = [
        {"user_id": "u1", "kind": "number", "real_value": "5550001111",
         "fake_value": "1023835840"},
    ]
    codec = PrivacyCodec(rng=random.Random(1))
    codec.real_to_fake = {"1023456789": "1023835840"}
    codec.fake_to_real = {"1023835840": "5550001111"}  # taken by the other real
    store = PiiMappingStore(client)
    store.persist_new("u1", [NewMapping("number", "1023456789", "1023835840")], codec)
    # our real is now stored with a DIFFERENT (regenerated) fake
    ours = [r for r in client._t.rows if r["real_value"] == "1023456789"]
    assert len(ours) == 1
    new_fake = ours[0]["fake_value"]
    assert new_fake != "1023835840"
    assert len(new_fake) == 10 and new_fake[:4] == "1023"  # shape preserved
    # codec reconciled to the new fake
    assert codec.real_to_fake["1023456789"] == new_fake


def test_classify_unique_violation_both_shapes():
    # constraint-name-only (message), migration 087 names
    real_msg = _FakeAPIError(
        "23505", 'unique constraint "pii_mappings_user_real_uniq"')
    fake_msg = _FakeAPIError(
        "23505", 'unique constraint "pii_mappings_user_fake_uniq"')
    assert _classify_unique_violation(real_msg) == "real"
    assert _classify_unique_violation(fake_msg) == "fake"

    # PostgREST `details` Key clause carries the column name verbatim
    real_det = _FakeAPIError("23505", "duplicate key value violates unique constraint")
    real_det.details = "Key (user_id, real_value)=(u1, 123) already exists."
    fake_det = _FakeAPIError("23505", "duplicate key value violates unique constraint")
    fake_det.details = "Key (user_id, fake_value)=(u1, 999) already exists."
    assert _classify_unique_violation(real_det) == "real"
    assert _classify_unique_violation(fake_det) == "fake"

    # non-unique error → None
    assert _classify_unique_violation(_FakeAPIError("42P01", "relation missing")) is None


def test_non_unique_error_propagates():
    client = _FakeClient()

    def boom(_row):
        raise _FakeAPIError("42P01", 'relation "pii_mappings" does not exist')

    client._t._insert = boom  # type: ignore[assignment]
    store = PiiMappingStore(client)
    with pytest.raises(_FakeAPIError):
        store.persist_new("u1", [NewMapping("number", "1", "2")])
