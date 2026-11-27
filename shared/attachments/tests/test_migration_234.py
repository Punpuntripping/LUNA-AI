"""Migration 234 (attachment agent edits) — read as text, never applied here: three
tables, the keys that make fixes follow the file and the log survive a map
replace, the CHECKs that match the code, RLS, revokes, grants, one transaction,
self-tests, and the columns the code writes."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents.memory.angle_mapper import edits
from shared.attachments import segment_overrides as so
from shared.attachments import text_corrections as tc

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "shared" / "db" / "migrations"
PATH = MIGRATIONS / "234_attachment_agent_edits.sql"
SQL = PATH.read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL)
FLAT = " ".join(CODE.split())
TABLES = (tc.TABLE, so.TABLE, edits.EDITS_TABLE)


def _body(table: str) -> str:
    m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table} \((.*?)\n\);", CODE, re.S)
    assert m, table
    return m.group(1)


def _columns(table: str) -> set[str]:
    cols = set()
    for line in _body(table).split("\n"):
        m = re.match(r"^\s*([a-z_]+)\s+(uuid|text|jsonb|integer|timestamptz|boolean)\b", line)
        if m and m.group(1) != "constraint":
            cols.add(m.group(1))
    return cols


def test_the_only_234_and_the_table_names_match_the_code():
    assert sorted(p.name for p in MIGRATIONS.glob("234_*")) == [PATH.name]
    assert TABLES == ("attachment_text_corrections", "attachment_segment_overrides", "attachment_angle_map_edits")


def test_one_transaction_with_self_tests():
    assert len(re.findall(r"^\s*BEGIN\s*;", CODE, re.M)) == 1
    assert len(re.findall(r"^\s*COMMIT\s*;", CODE, re.M)) == 1
    assert CODE.strip().startswith("BEGIN;")
    assert "SET LOCAL lock_timeout" in FLAT
    assert FLAT.index("DO $$") < FLAT.index("COMMIT;")
    assert "RAISE NOTICE '234 OK" in FLAT
    assert not re.search(r"\bROLLBACK\b", CODE)


@pytest.mark.parametrize("table", TABLES)
def test_each_table_created_idempotently_with_rls_policy_revoke_grant(table):
    assert f"CREATE TABLE IF NOT EXISTS public.{table} (" in CODE
    assert re.search(rf"ALTER TABLE public\.{table}\s+ENABLE ROW LEVEL SECURITY;", CODE)
    assert f"DROP POLICY IF EXISTS {table}_select_own ON public.{table};" in FLAT
    assert f"CREATE POLICY {table}_select_own ON public.{table} FOR SELECT" in FLAT
    assert re.search(rf"REVOKE ALL ON TABLE public\.{table}\s+FROM anon, authenticated;", CODE)
    assert re.search(rf"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public\.{table}\s+TO service_role;", CODE)
    # no client write policy
    assert not re.search(rf"CREATE POLICY \w+ ON public\.{table} FOR (INSERT|UPDATE|DELETE|ALL)", FLAT)


def test_text_corrections_columns_and_keys():
    body = " ".join(_body(tc.TABLE).split())
    assert _columns(tc.TABLE) == {
        "id", "file_id", "user_id", "wrong_text", "right_text", "occurrences", "evidence",
        "conversation_id", "message_id", "created_at", "reverted_at", "derived",
    }
    assert "derived jsonb NOT NULL DEFAULT '[]'::jsonb" in body
    # idempotent for a database that ran an earlier draft, and self-checked
    assert ("ALTER TABLE public.attachment_text_corrections ADD COLUMN IF NOT EXISTS derived jsonb "
            "NOT NULL DEFAULT '[]'::jsonb;") in FLAT
    assert "column_name = 'derived' AND data_type = 'jsonb' AND is_nullable = 'NO'" in FLAT
    assert "file_id uuid NOT NULL REFERENCES public.user_files(file_id) ON DELETE CASCADE" in body
    assert "FOREIGN KEY (file_id, user_id) REFERENCES public.user_files (file_id, user_id) ON DELETE CASCADE" in body
    assert "REFERENCES public.conversations(conversation_id) ON DELETE SET NULL" in body
    assert "REFERENCES public.messages(message_id) ON DELETE SET NULL" in body
    assert "wrong_text <> right_text" in body and "strpos(wrong_text, E'\\n') = 0" in body
    assert "CREATE INDEX IF NOT EXISTS attachment_text_corrections_file_active_idx ON public.attachment_text_corrections (file_id) WHERE reverted_at IS NULL" in FLAT
    # RIGHT is reserved in PostgreSQL: never a bare column named right
    assert not re.search(r"^\s*right\s+text", _body(tc.TABLE), re.M)


def test_segment_overrides_columns_and_checks():
    body = " ".join(_body(so.TABLE).split())
    assert _columns(so.TABLE) == {
        "id", "file_id", "user_id", "char_start", "char_end", "value", "evidence",
        "conversation_id", "message_id", "created_at", "reverted_at",
    }
    assert "CHECK (char_start >= 0 AND char_end > char_start)" in body
    assert "CHECK (value = 'core')" in body and so.PROMOTED_VALUE == "core"
    assert "REFERENCES public.user_files(file_id) ON DELETE CASCADE" in body
    assert "CREATE INDEX IF NOT EXISTS attachment_segment_overrides_file_id_idx ON public.attachment_segment_overrides (file_id)" in FLAT


def test_edit_log_has_no_map_fk_and_its_kinds_match_the_code():
    body = " ".join(_body(edits.EDITS_TABLE).split())
    assert _columns(edits.EDITS_TABLE) == {
        "id", "map_id", "conversation_id", "user_id", "item_ids", "kind", "target", "before", "after",
        "evidence", "message_id", "created_at", "undone_at",
    }
    assert "map_id uuid NOT NULL," in body
    assert "attachment_angle_maps" not in body                      # survives a map replace
    kinds = re.search(r"kind_chk CHECK \(kind IN \((.*?)\)\)", body).group(1)
    assert tuple(re.findall(r"'([a-z_]+)'", kinds)) == edits.EDIT_KINDS
    assert "jsonb_typeof(item_ids) = 'array'" in body
    for idx in ("attachment_angle_map_edits_map_id_idx", "attachment_angle_map_edits_conversation_id_idx"):
        assert f"CREATE INDEX IF NOT EXISTS {idx}" in FLAT


def test_the_code_writes_only_columns_that_exist():
    from agents.memory.angle_mapper.tests.fake_supabase import FakeSupabase

    db = FakeSupabase()
    tc.save_text_correction(db, file_id="f", user_id="u", wrong="أ", right="ب", occurrences=1, evidence="e")
    so.save_segment_override(db, file_id="f", user_id="u", char_start=0, char_end=1, evidence="e")
    assert set(db.tables[tc.TABLE][0]) <= _columns(tc.TABLE)
    assert set(db.tables[so.TABLE][0]) <= _columns(so.TABLE)
    edits._write_log(db, map_id="m", item_ids=["i"], kind="snapshot", target="map", before={}, after=None,
                     evidence="e", user_id="u", conversation_id=None, message_id=None)
    assert set(db.tables[edits.EDITS_TABLE][0]) <= _columns(edits.EDITS_TABLE)
    # and what the loaders select
    assert set(re.findall(r"[a-z_]+", tc._COLUMNS)) <= _columns(tc.TABLE)
    assert set(re.findall(r"[a-z_]+", so._COLUMNS)) <= _columns(so.TABLE)
