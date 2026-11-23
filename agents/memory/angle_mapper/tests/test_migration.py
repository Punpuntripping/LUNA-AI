"""Migration 228 (one map per message group) — read as text, never applied here:
three tables, their keys, foreign keys and cascades, UNIQUEs, CHECKs that match
the models, RLS, revokes, one transaction, self-tests, and the columns the store
writes."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents.memory.angle_mapper import store
from agents.memory.angle_mapper.models import KINDS, STRENGTHS

REPO = Path(__file__).resolve().parents[4]
MIGRATIONS = REPO / "shared" / "db" / "migrations"
PATH = MIGRATIONS / "228_attachment_angle_maps.sql"
SQL = PATH.read_text(encoding="utf-8")
#: The statements, comments removed (no string in the file holds ``--``).
CODE = re.sub(r"--[^\n]*", "", SQL)
FLAT = " ".join(CODE.split())
TABLES = ("attachment_angle_maps", "attachment_angle_map_items", "attachment_angles")


def _body(table: str) -> str:
    m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table} \((.*?)\n\);", CODE, re.S)
    assert m, table
    return m.group(1)


def _flat_body(table: str) -> str:
    return " ".join(_body(table).split())


def _columns(table: str) -> set[str]:
    cols = set()
    for line in _body(table).split("\n"):
        line = line.strip()
        if not line or line.startswith("CONSTRAINT") or line.startswith(")") or line.startswith("AND "):
            continue
        if re.match(r"^[a-z_]+\s+(uuid|text|jsonb|integer|numeric|timestamptz|boolean)\b", line):
            cols.add(line.split()[0])
    return cols


def test_the_only_228():
    assert sorted(p.name for p in MIGRATIONS.glob("228_*")) == [PATH.name]
    # master's 227 is the files attach (227_user_files_attach.sql); ours is not there any more
    assert not (MIGRATIONS / "227_attachment_angle_maps.sql").exists()


def test_one_transaction():
    assert len(re.findall(r"^\s*BEGIN\s*;", CODE, re.M)) == 1
    assert len(re.findall(r"^\s*COMMIT\s*;", CODE, re.M)) == 1
    assert CODE.strip().startswith("BEGIN;")
    assert CODE.strip().endswith("COMMIT;")
    assert not re.search(r"\bROLLBACK\b", CODE)
    assert "SET LOCAL lock_timeout" in FLAT


def test_three_tables_created_idempotently_and_named_as_the_store_names_them():
    for t in TABLES:
        assert CODE.count(f"CREATE TABLE IF NOT EXISTS public.{t} (") == 1
    assert (store.MAPS_TABLE, store.ITEMS_TABLE, store.ANGLES_TABLE) == TABLES
    assert len(re.findall(r"CREATE TABLE", CODE)) == 3


def test_columns_are_what_the_store_writes():
    assert _columns("attachment_angle_maps") == {
        "map_id", "message_id", "conversation_id", "user_id", "created_at", *store.MAP_COLUMNS}
    assert _columns("attachment_angle_map_items") == {"map_id", *store.ITEM_COLUMNS}
    assert _columns("attachment_angles") == {"map_id", *store.ANGLE_COLUMNS}


@pytest.mark.parametrize("fragment", [
    "map_id uuid PRIMARY KEY DEFAULT gen_random_uuid()",
    "message_id uuid REFERENCES public.messages(message_id) ON DELETE SET NULL",
    "conversation_id uuid REFERENCES public.conversations(conversation_id) ON DELETE CASCADE",
    "user_id uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE",
    "prompt_version text NOT NULL",
    "client jsonb NOT NULL DEFAULT '{}'::jsonb",
    "context jsonb NOT NULL DEFAULT '[]'::jsonb",
    "unrelated_documents jsonb NOT NULL DEFAULT '[]'::jsonb",
    "input_tokens integer NOT NULL DEFAULT 0",
    "cost_usd numeric NOT NULL DEFAULT 0",
    "created_at timestamptz NOT NULL DEFAULT now()",
    "CONSTRAINT attachment_angle_maps_message_id_key UNIQUE (message_id)",
    "jsonb_typeof(client) = 'object'",
    "jsonb_typeof(context) = 'array'",
    "jsonb_typeof(gaps) = 'array'",
    "jsonb_typeof(no_angle) = 'array'",
    "jsonb_typeof(unrelated_documents) = 'array'",
])
def test_map_keys_and_checks(fragment):
    assert fragment in _flat_body("attachment_angle_maps")


@pytest.mark.parametrize("fragment", [
    "map_id uuid NOT NULL REFERENCES public.attachment_angle_maps(map_id) ON DELETE CASCADE",
    "item_id uuid NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE",
    "doc_index integer NOT NULL",
    "content_chars integer NOT NULL",
    "segment_prompt_version text NOT NULL",
    "paragraph_rule_version text NOT NULL",
    "segmented_at timestamptz",
    "CONSTRAINT attachment_angle_map_items_pkey PRIMARY KEY (map_id, doc_index)",
    "CONSTRAINT attachment_angle_map_items_item_id_key UNIQUE (item_id)",
    "CONSTRAINT attachment_angle_map_items_doc_index_chk CHECK (doc_index >= 1)",
    "CONSTRAINT attachment_angle_map_items_content_chars_chk CHECK (content_chars >= 0)",
])
def test_member_keys_and_checks(fragment):
    assert fragment in _flat_body("attachment_angle_map_items")


@pytest.mark.parametrize("fragment", [
    "map_id uuid NOT NULL REFERENCES public.attachment_angle_maps(map_id) ON DELETE CASCADE",
    "sections jsonb NOT NULL DEFAULT '[]'::jsonb",
    "kind text NOT NULL",
    "strength text NOT NULL",
    "ocr_suspect boolean NOT NULL DEFAULT false",  # angles-v5
    "CONSTRAINT attachment_angles_pkey PRIMARY KEY (map_id, seq)",
    "CONSTRAINT attachment_angles_seq_chk CHECK (seq >= 1)",
    "CONSTRAINT attachment_angles_sections_chk CHECK (jsonb_typeof(sections) = 'array')",
])
def test_angle_keys_and_checks(fragment):
    assert fragment in _flat_body("attachment_angles")


def test_the_angles_ocr_suspect_column_is_what_the_store_writes():
    assert "ocr_suspect" in store.ANGLE_COLUMNS and "ocr_suspect" in _columns("attachment_angles")
    assert "ocr_suspect" not in _columns("attachment_angle_maps")  # the gaps' flag stays inside the gaps jsonb


def test_no_fk_to_the_segment_tables():
    """A re-segmented file makes the map stale through its member row's versions."""
    assert "attachment_segment" not in CODE


def test_foreign_keys_and_their_delete_rules():
    refs = re.findall(r"REFERENCES public\.(\w+)\((\w+)\) ON DELETE (CASCADE|SET NULL)", FLAT)
    assert sorted(refs) == sorted([
        ("messages", "message_id", "SET NULL"),
        ("conversations", "conversation_id", "CASCADE"),
        ("users", "user_id", "CASCADE"),
        ("attachment_angle_maps", "map_id", "CASCADE"),
        ("workspace_items", "item_id", "CASCADE"),
        ("attachment_angle_maps", "map_id", "CASCADE"),
    ])
    assert len(re.findall(r"\bREFERENCES\b", CODE)) == 6  # none without a delete rule


def _in_list(name: str) -> tuple[str, ...]:
    m = re.search(rf"CONSTRAINT attachment_angles_{name}_chk CHECK \({name} IN \(([^)]*)\)\)", FLAT)
    assert m, name
    return tuple(v.strip().strip("'") for v in m.group(1).split(","))


def test_kind_and_strength_checks_match_the_models():
    assert _in_list("kind") == KINDS == ("strengthen", "attack", "answer")
    assert _in_list("strength") == STRENGTHS == ("strong", "medium", "weak")


@pytest.mark.parametrize("table", TABLES)
def test_rls_revokes_and_service_role(table):
    assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;" in FLAT
    assert f"REVOKE ALL ON TABLE public.{table} FROM anon, authenticated;" in FLAT
    assert f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table} TO service_role;" in FLAT


def test_no_client_grant_and_select_only_owner_policies():
    grantees = re.findall(r"\bGRANT\b[^;]*?\bTO\s+([^;]+);", FLAT)
    assert len(grantees) == 3 and all(g.strip() == "service_role" for g in grantees)
    policies = re.findall(r"CREATE POLICY (\w+) ON public\.(\w+) FOR (\w+)", FLAT)
    assert sorted(policies) == sorted((f"{t}_select_own", t, "SELECT") for t in TABLES)
    for name, table, _ in policies:
        assert f"DROP POLICY IF EXISTS {name} ON public.{table};" in FLAT
    # members and angles carry no user_id: ownership through their map
    for t in ("attachment_angle_map_items", "attachment_angles"):
        assert f"WHERE m.map_id = {t}.map_id AND m.user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = " \
               "(SELECT auth.uid()))" in FLAT


def test_indexes_for_the_cascades():
    assert ("CREATE INDEX IF NOT EXISTS attachment_angle_maps_user_id_idx ON public.attachment_angle_maps "
            "(user_id);") in FLAT
    assert ("CREATE INDEX IF NOT EXISTS attachment_angle_maps_conversation_id_idx ON public.attachment_angle_maps "
            "(conversation_id) WHERE conversation_id IS NOT NULL;") in FLAT


def test_self_checks_run_inside_the_transaction():
    do = FLAT.index("DO $$")
    assert FLAT.index("BEGIN;") < do < FLAT.index("COMMIT;")
    for needle in ("relrowsecurity", "role_table_grants", "confdeltype = 'c'", "confdeltype = 'n'",
                   "IF v_casc <> 5", "attachment_angle_maps_message_id_key", "attachment_angle_map_items_item_id_key",
                   "attachment_angles_kind_chk", "attachment_angles_strength_chk",
                   "attachment_angle_maps_user_id_idx", "IF v_rls <> 3", "IF v_svc <> 3"):
        assert needle in FLAT, needle
