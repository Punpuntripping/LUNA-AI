"""Fixes from the independent review (2026-11-12): bundle summary wait, label decode."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agents.memory.attachment_segmenter import runner as R
from agents.memory.attachment_segmenter.models import SegmentMap


def _row(*, summary="", doc_type="", age_s=10, chars=2_000):
    created = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    return {"item_id": "x", "content_md": "ن" * chars, "summary": summary,
            "metadata": {"doc_type": doc_type} if doc_type else {}, "created_at": created}


def test_a_fresh_member_without_summary_is_awaited():
    assert R._awaiting_summary(_row())


def test_summary_or_doc_type_ends_the_wait():
    assert not R._awaiting_summary(_row(summary="ملخص"))
    assert not R._awaiting_summary(_row(doc_type="judgment"))


def test_old_short_or_deleted_members_are_not_awaited():
    assert not R._awaiting_summary(_row(age_s=R.BUNDLE_SUMMARY_FRESH_S + 60))
    assert not R._awaiting_summary(_row(chars=100))  # the summarizer skips it
    assert not R._awaiting_summary({**_row(), "deleted_at": "2026-11-12"})
    assert not R._awaiting_summary({**_row(), "created_at": None})


def test_the_bundle_waits_until_every_member_has_its_summary(monkeypatch):
    monkeypatch.setattr(R, "BUNDLE_SUMMARY_POLL_S", 0.01)
    reads = {"n": 0}

    def load(_sb, ids):
        reads["n"] += 1
        done = reads["n"] >= 3  # the second member's summary lands on the 3rd read
        return {"a": _row(doc_type="judgment"), "b": _row(doc_type="judgment" if done else "")}

    monkeypatch.setattr(R, "_load_items", load)
    rows = asyncio.run(R._wait_for_member_summaries(None, ["a", "b"], load(None, ["a", "b"])))
    assert rows["b"]["metadata"]["doc_type"] == "judgment" and reads["n"] == 3


def test_the_bundle_stops_waiting_at_the_deadline(monkeypatch):
    monkeypatch.setattr(R, "BUNDLE_SUMMARY_POLL_S", 0.01)
    monkeypatch.setattr(R, "BUNDLE_SUMMARY_WAIT_S", 0.05)
    monkeypatch.setattr(R, "_load_items", lambda _sb, ids: {"a": _row()})
    rows = asyncio.run(R._wait_for_member_summaries(None, ["a"], {"a": _row()}))
    assert R._awaiting_summary(rows["a"])  # still missing — the bundle goes on anyway


class _Codec:
    def decode(self, text):
        return text.replace("FAKE-1", "محمد")


def test_labels_are_stored_real(monkeypatch):
    monkeypatch.setattr(R, "_dec", lambda codec, text: codec.decode(text) if codec else text)
    smap = SegmentMap.model_validate({
        "documents_in_file": [{"from_p": 1, "to_p": 1, "name": "FAKE-1"}],
        "segments": [{"from_p": 1, "to_p": 1, "label": "other:FAKE-1", "what": "FAKE-1", "value": "core"}],
        "key_paragraphs": [{"p": 1, "why": "FAKE-1"}],
    })
    out = R._decode_map(_Codec(), smap)
    assert out.segments[0].label == "other:محمد" and out.segments[0].what == "محمد"
