"""The attachment retention sweep purges the FILE and keeps the TEXT.

What these tests pin, and why each one is load-bearing:

* **The row survives.** ``message_attachments.document_id`` references
  ``workspace_items(item_id)`` ON DELETE CASCADE, so a DELETE here does not just
  drop an attachment — it cuts the message→upload link, taking the ``WI-{seq}``
  alias and the history tag with it. The sweep must never issue one. This is the
  regression: 22 of 46 OCR extractions ever made were destroyed this way.
* **``content_md`` is never written.** The OCR text is the only form of the
  document any agent ever reads; the PDF is never sent to a model. An UPDATE
  that touched it would defeat the whole point of keeping the row.
* **Two windows.** A file whose OCR never ran is the only copy of text that does
  not exist yet — purging it at 24h destroys that text forever, so it gets the
  longer abandoned-upload grace instead.
* **UPDATE before DELETE.** A deploy landing ahead of migration 125 hits the old
  ``workspace_content_shape`` CHECK, which rejects a NULL ``storage_path``. In
  this order that is a no-op sweep; reversed, it is unrecoverable file loss.
* **Scope.** Images and ``document_id`` pins are not the sweep's business — a
  pin's bytes belong to the case library, and deleting them there would take out
  a case document that nothing in this module owns.
* **Idempotent.** A purged row has ``storage_path IS NULL``, so it must fall out
  of the query rather than being re-purged (and re-stamped) every night.

No live DB and no settings/env dependency — the Supabase client and the storage
bucket are stubbed in-process.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services import attachment_cleanup as ac

BUCKET = "documents"


def _iso(hours_ago: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ).isoformat()


def row(
    item_id: str,
    *,
    hours_ago: float = 48,
    storage_path: str | None = "general/u/convos/c/contract.pdf",
    mime: str | None = "application/pdf",
    ocr_status: str | None = "done",
    content_md: str | None = "النص المستخرج من العقد",
    document_id: str | None = None,
) -> dict:
    meta: dict = {}
    if mime is not None:
        meta["mime_type"] = mime
    if ocr_status is not None:
        meta["ocr_status"] = ocr_status
    return {
        "item_id": item_id,
        "kind": "attachment",
        "storage_path": storage_path,
        "document_id": document_id,
        "content_md": content_md,
        "metadata": meta,
        "created_at": _iso(hours_ago),
        "deleted_at": None,
    }


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStorageBucket:
    def __init__(self, log: list[str], fail: bool = False):
        self._log = log
        self._fail = fail

    def remove(self, paths: list[str]):
        if self._fail:
            raise RuntimeError("storage down")
        self._log.extend(paths)
        return {"data": paths}


class FakeStorage:
    def __init__(self, log: list[str], fail: bool = False):
        self._log, self._fail = log, fail

    def from_(self, _bucket: str) -> FakeStorageBucket:
        return FakeStorageBucket(self._log, self._fail)


class FakeQuery:
    """In-memory stand-in for the PostgREST builder chain used by the sweep."""

    def __init__(self, client: "FakeSupabase", rows: list[dict]):
        self._client = client
        self._rows = rows
        self._filters: list = []
        self._op: str | None = None
        self._payload: dict | None = None

    # -- filters ----------------------------------------------------------
    def select(self, _cols: str):
        self._op = "select"
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):  # pragma: no cover - must never be reached
        self._client.deletes_attempted += 1
        raise AssertionError(
            "the retention sweep must NEVER delete a workspace_items row"
        )

    def eq(self, col: str, val):
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def lt(self, col: str, val):
        self._filters.append(lambda r: str(r.get(col) or "") < str(val))
        return self

    def is_(self, col: str, val):
        if self._negate_next:
            self._negate_next = False
            self._filters.append(
                lambda r: r.get(col) is not None if val == "null" else True
            )
        else:
            self._filters.append(
                lambda r: r.get(col) is None if val == "null" else True
            )
        return self

    _negate_next = False

    @property
    def not_(self):
        self._negate_next = True
        return self

    # -- terminal ---------------------------------------------------------
    def _matched(self) -> list[dict]:
        return [r for r in self._rows if all(f(r) for f in self._filters)]

    def execute(self):
        if self._client.raise_on_update and self._op == "update":
            raise RuntimeError("violates check constraint workspace_content_shape")
        matched = self._matched()
        if self._op == "update":
            self._client.updates.append((matched[0]["item_id"], dict(self._payload)))
            for r in matched:
                r.update(self._payload)
        return type("Result", (), {"data": [dict(r) for r in matched]})()


class FakeSupabase:
    def __init__(
        self,
        rows: list[dict],
        *,
        storage_fails: bool = False,
        raise_on_update: bool = False,
    ):
        self.rows = rows
        self.removed: list[str] = []
        self.updates: list[tuple[str, dict]] = []
        self.deletes_attempted = 0
        self.raise_on_update = raise_on_update
        self.storage = FakeStorage(self.removed, storage_fails)

    def table(self, name: str) -> FakeQuery:
        assert name == "workspace_items"
        return FakeQuery(self, self.rows)


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch):
    """The sweep only wants the bucket name — keep env/config out of the test."""
    monkeypatch.setattr(
        ac,
        "get_settings",
        lambda: type("S", (), {"STORAGE_BUCKET_DOCUMENTS": BUCKET})(),
    )


# ---------------------------------------------------------------------------
# The regression: text and row survive, only the file goes
# ---------------------------------------------------------------------------


def test_purges_file_but_keeps_row_and_extracted_text():
    r = row("a")
    sb = FakeSupabase([r])

    stats = ac.cleanup_old_pdf_attachments(sb)

    assert sb.deletes_attempted == 0, "a DELETE cascades message_attachments away"
    assert sb.removed == ["general/u/convos/c/contract.pdf"]
    assert stats == {
        "scanned": 1,
        "rows_purged": 1,
        "files_deleted": 1,
        "skipped_young": 0,
    }
    # The row is still there, still readable by the agents.
    assert r["content_md"] == "النص المستخرج من العقد"
    assert r["storage_path"] is None
    assert r["metadata"]["original_purged_at"]
    assert r["metadata"]["original_purge_reason"] == "retention_sweep"
    # …and the OCR marker it already carried is preserved, not clobbered.
    assert r["metadata"]["ocr_status"] == "done"


def test_update_never_touches_content_md():
    sb = FakeSupabase([row("a")])
    ac.cleanup_old_pdf_attachments(sb)

    _, payload = sb.updates[0]
    assert set(payload) == {"storage_path", "metadata"}


def test_purges_even_when_ocr_found_no_text():
    """``empty`` is a settled status: the file has been read and had nothing."""
    r = row("a", ocr_status="empty", content_md=None)
    sb = FakeSupabase([r])

    stats = ac.cleanup_old_pdf_attachments(sb)

    assert stats["rows_purged"] == 1
    assert sb.removed == ["general/u/convos/c/contract.pdf"]
    assert r["metadata"]["original_purged_at"]


# ---------------------------------------------------------------------------
# Two windows
# ---------------------------------------------------------------------------


def test_unextracted_upload_keeps_its_file_past_the_short_window():
    """No ``ocr_status`` ⇒ the text does not exist yet, and the file is the
    only way it ever will. 48h is past RETENTION_HOURS but well inside the
    abandoned-upload grace."""
    r = row("a", hours_ago=48, ocr_status=None, content_md=None)
    sb = FakeSupabase([r])

    stats = ac.cleanup_old_pdf_attachments(sb)

    assert stats == {
        "scanned": 1,
        "rows_purged": 0,
        "files_deleted": 0,
        "skipped_young": 1,
    }
    assert r["storage_path"] == "general/u/convos/c/contract.pdf"
    assert sb.removed == []


def test_unextracted_upload_is_purged_once_the_long_window_passes():
    r = row(
        "a",
        hours_ago=ac.ABANDONED_RETENTION_HOURS + 1,
        ocr_status=None,
        content_md=None,
    )
    sb = FakeSupabase([r])

    assert ac.cleanup_old_pdf_attachments(sb)["rows_purged"] == 1
    assert r["metadata"]["original_purged_at"]


def test_extracted_attachment_inside_the_window_is_left_alone():
    sb = FakeSupabase([row("a", hours_ago=1)])
    stats = ac.cleanup_old_pdf_attachments(sb)
    # Filtered out by the query's own cutoff, so it is never even scanned.
    assert stats["scanned"] == 0
    assert sb.removed == []


# ---------------------------------------------------------------------------
# Ordering and failure isolation
# ---------------------------------------------------------------------------


def test_failed_update_keeps_the_file(caplog):
    """The missing-migration case. The old CHECK rejects a NULL storage_path;
    because the UPDATE runs first, nothing is deleted and tomorrow retries."""
    r = row("a")
    sb = FakeSupabase([r], raise_on_update=True)

    stats = ac.cleanup_old_pdf_attachments(sb)

    assert stats["rows_purged"] == 0
    assert stats["files_deleted"] == 0
    assert sb.removed == [], "bytes must not be deleted if the purge is unrecorded"
    assert r["storage_path"] == "general/u/convos/c/contract.pdf"


def test_failed_storage_remove_logs_the_orphan_path(caplog):
    sb = FakeSupabase([row("a")], storage_fails=True)

    stats = ac.cleanup_old_pdf_attachments(sb)

    assert stats["rows_purged"] == 1
    assert stats["files_deleted"] == 0
    assert "general/u/convos/c/contract.pdf" in caplog.text


def test_one_bad_row_does_not_abort_the_sweep():
    rows = [row("a"), row("b", hours_ago=0.5), row("c")]
    sb = FakeSupabase(rows)

    stats = ac.cleanup_old_pdf_attachments(sb)

    assert stats["scanned"] == 2  # 'b' is inside the cutoff
    assert stats["rows_purged"] == 2


def test_unparseable_created_at_is_skipped_not_purged():
    """Age must never be inferred on a guess — an unreadable timestamp is a
    reason to leave the file alone, not to treat the row as infinitely old.
    (Leading zeros so the row still clears the query's own string cutoff and
    actually reaches the age check.)"""
    r = row("a")
    r["created_at"] = "0000-not-a-timestamp"
    sb = FakeSupabase([r])

    stats = ac.cleanup_old_pdf_attachments(sb)

    assert stats["rows_purged"] == 0
    assert stats["skipped_young"] == 1
    assert sb.removed == []


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_images_are_out_of_scope():
    r = row("a", mime="image/png", storage_path="general/u/convos/c/scan.png")
    sb = FakeSupabase([r])

    assert ac.cleanup_old_pdf_attachments(sb)["scanned"] == 0
    assert r["storage_path"] == "general/u/convos/c/scan.png"


def test_pdf_detected_by_path_when_mime_is_missing():
    r = row("a", mime=None)
    sb = FakeSupabase([r])
    assert ac.cleanup_old_pdf_attachments(sb)["rows_purged"] == 1


def test_document_id_pin_is_never_touched():
    """A pin shares the case library's object. Removing it would delete a case
    document this module does not own."""
    r = row("a", storage_path=None, document_id="doc-1")
    sb = FakeSupabase([r])

    assert ac.cleanup_old_pdf_attachments(sb)["scanned"] == 0
    assert sb.removed == []


def test_already_purged_row_is_not_reprocessed():
    r = row("a", storage_path=None)
    r["metadata"]["original_purged_at"] = _iso(72)
    sb = FakeSupabase([r])

    stats = ac.cleanup_old_pdf_attachments(sb)

    assert stats["scanned"] == 0
    assert sb.updates == []


def test_query_failure_returns_zeroed_stats_and_never_raises():
    class Broken(FakeSupabase):
        def table(self, name):  # noqa: ARG002
            raise RuntimeError("postgrest down")

    stats = Broken([]).__class__([]) and ac.cleanup_old_pdf_attachments(Broken([]))
    assert stats == {
        "scanned": 0,
        "rows_purged": 0,
        "files_deleted": 0,
        "skipped_young": 0,
    }
