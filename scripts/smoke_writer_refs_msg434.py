"""Smoke test: writer publisher + prompt rendering against the real msg_434 data.

Replays the exact production scenario that exposed Bug #1:
  - Two source WIs in conversation accbc49c (msg_434):
      * aa2506ef-7cfe-49b8-9061-cb5d7a214739  (wi_seq=1, "استئناف دعوى تعويض ...")
        used refs n = [1,2,3,4,5,7,10,11,14,15,16,17,18,19]
      * a92c4741-92df-48e8-a4b5-0a148c33b87e  (wi_seq=2, "أحكام ومسالك إثبات ...")
        used refs n = [1,3,4,5,6,7,9,10,23,24,30,31,44]
  - Writer cited 15 refs: [1,3,4,5,6,7,9,10,15,16,17,18,19,24,31]
    Of those, 6 (n=1,3,4,5,7,10) are AMBIGUOUS — exist in both source WIs.

This script doesn't write to prod. It builds the writer pipeline in-process
against the real wi_seq/item_id values and an in-memory supabase stub seeded
with the real ref rows, then asserts:

  A. The rendered writer package prompt emits wi="WI-1" / wi="WI-2" and
     contains ZERO raw UUID strings.
  B. The publisher resolves each CitationRef tuple against the correct source
     and inserts 15 new workspace_item_references rows on the new agent_writing
     WI, all with used=True and n=1..15 in body order.
  C. The 6 ambiguous citations resolve correctly by the `wi` tag — the same
     `n=5` gets attributed to WI-1 OR WI-2 depending on the tuple, not collapsed.
  D. metadata.references mirror carries source_wi + source_n for every persisted ref.

Run:
    python scripts/smoke_writer_refs_msg434.py
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Any

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Real production data (from Supabase query 2026-05-26, conversation accbc49c)
# ---------------------------------------------------------------------------

WI_1_ID = "aa2506ef-7cfe-49b8-9061-cb5d7a214739"
WI_1_SEQ = 1
WI_1_TITLE = "استئناف دعوى تعويض عن حريق وإثبات الضرر بدون فواتير"
WI_1_USED_NS = [1, 2, 3, 4, 5, 7, 10, 11, 14, 15, 16, 17, 18, 19]

WI_2_ID = "a92c4741-92df-48e8-a4b5-0a148c33b87e"
WI_2_SEQ = 2
WI_2_TITLE = "أحكام ومسالك إثبات خسائر الحريق بالسعودية"
WI_2_USED_NS = [1, 3, 4, 5, 6, 7, 9, 10, 23, 24, 30, 31, 44]

# Exact citations_used vector observed in workspace_items.4f45d0d7.metadata
MSG_434_CITATIONS_FLAT = [1, 3, 4, 5, 6, 7, 9, 10, 15, 16, 17, 18, 19, 24, 31]

# For the smoke, disambiguate the 6 ambiguous citations (n in BOTH WIs) by
# assigning them deterministically to one source — half to each. This mirrors
# what the model would emit under Option B: it knows which <source wi="WI-N">
# block the citation came from because each source's <refs> block is now
# rendered separately with its WI alias attached.
def disambiguate_msg_434() -> list[tuple[str, int]]:
    """Return list of (wi_alias, n) tuples matching msg_434's citations."""
    in_wi1 = set(WI_1_USED_NS)
    in_wi2 = set(WI_2_USED_NS)
    picks: list[tuple[str, int]] = []
    flip = True   # alternate ambiguous picks between WI-1 and WI-2
    for n in MSG_434_CITATIONS_FLAT:
        only_in_1 = n in in_wi1 and n not in in_wi2
        only_in_2 = n in in_wi2 and n not in in_wi1
        if only_in_1:
            picks.append(("WI-1", n))
        elif only_in_2:
            picks.append(("WI-2", n))
        else:
            # Ambiguous: deterministic alternation
            picks.append((("WI-1" if flip else "WI-2"), n))
            flip = not flip
    return picks


# ---------------------------------------------------------------------------
# In-memory supabase stub seeded with the real ref data
# ---------------------------------------------------------------------------

def _ref_row(wi_id: str, n: int, domain: str = "reg") -> dict:
    """Synthesize a workspace_item_references row matching the real shape."""
    return {
        "ref_id": f"{domain}:{wi_id[:8]}-n{n}",
        "item_id": f"src-{wi_id[:8]}-n{n}",   # back-pointer to URA source
        "wi_id": wi_id,
        "domain": domain,
        "n": n,
        "relevance": "high",
        "used": True,
        "sub_queries": [1],
        "content_word_count": 100,
    }


# Pre-seed both source WIs' used ref rows
SEED_REFS: list[dict] = []
for n in WI_1_USED_NS:
    SEED_REFS.append(_ref_row(WI_1_ID, n))
for n in WI_2_USED_NS:
    SEED_REFS.append(_ref_row(WI_2_ID, n))


class _StubExecuteResult:
    def __init__(self, data: list[dict] | dict | None) -> None:
        self.data = data


class _StubTable:
    """Just enough of the supabase-py table chain for the publisher's needs."""

    def __init__(self, parent: "_StubSupabase", name: str) -> None:
        self.parent = parent
        self.name = name
        # filter state
        self._select_cols: str | None = None
        self._eq: list[tuple[str, Any]] = []
        self._in: tuple[str, list[Any]] | None = None
        self._insert_payload: list[dict] | dict | None = None
        self._update_payload: dict | None = None
        self._maybe_single: bool = False

    def select(self, cols: str) -> "_StubTable":
        self._select_cols = cols
        return self

    def insert(self, payload):
        self._insert_payload = payload
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in = (col, list(vals))
        return self

    def is_(self, col, val):
        self._eq.append((col, None if val == "null" else val))
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def execute(self):
        # INSERT (writes the new agent_writing row OR new ref rows)
        if self._insert_payload is not None:
            rows = self._insert_payload if isinstance(self._insert_payload, list) else [self._insert_payload]
            for row in rows:
                self.parent.inserts.setdefault(self.name, []).append(dict(row))
            # `create_workspace_item` expects the row back with item_id set
            if self.name == "workspace_items":
                row = dict(rows[0])
                row.setdefault("item_id", "new-agent-writing-wi-id")
                return _StubExecuteResult([row])
            return _StubExecuteResult(rows)

        # UPDATE (metadata enrichment, lock column, etc.)
        if self._update_payload is not None:
            self.parent.updates.setdefault(self.name, []).append({
                "payload": dict(self._update_payload),
                "filters": list(self._eq),
            })
            return _StubExecuteResult(None)

        # SELECT (source ref rows)
        if self.name == "workspace_item_references":
            wi_filter = next((v for k, v in self._eq if k == "wi_id"), None)
            n_filter = self._in[1] if self._in and self._in[0] == "n" else None
            rows = [
                dict(r) for r in self.parent.refs_seed
                if r["wi_id"] == wi_filter and (n_filter is None or r["n"] in n_filter)
            ]
            return _StubExecuteResult(rows)

        return _StubExecuteResult([])


class _StubSupabase:
    def __init__(self, refs_seed: list[dict]) -> None:
        self.refs_seed = refs_seed
        self.inserts: dict[str, list[dict]] = {}
        self.updates: dict[str, list[dict]] = {}

    def table(self, name: str) -> _StubTable:
        return _StubTable(self, name)


# ---------------------------------------------------------------------------
# Build a realistic WriterPackage and run the writer publisher
# ---------------------------------------------------------------------------

def _make_writer_package():
    """Build a WriterPackage with both source WIs as role='source' AnalyzedItems."""
    from agents.writer.models import AnalyzedItem, WriterPackage, WriterStyle

    sources = [
        AnalyzedItem(
            item_id=WI_1_ID,
            wi_seq=WI_1_SEQ,
            title=WI_1_TITLE,
            kind="agent_search",
            role="source",
            need="full",
            body_md="<<<aa2506ef body_md placeholder>>>",
            word_count_before=971,
            word_count_after=971,
            resolved_refs_md="[1] ...\n[2] ...\n[14] ...\n[19] ...",
        ),
        AnalyzedItem(
            item_id=WI_2_ID,
            wi_seq=WI_2_SEQ,
            title=WI_2_TITLE,
            kind="agent_search",
            role="source",
            need="full",
            body_md="<<<a92c4741 body_md placeholder>>>",
            word_count_before=723,
            word_count_after=723,
            resolved_refs_md="[1] ...\n[6] ...\n[31] ...\n[44] ...",
        ),
    ]

    return WriterPackage(
        intent_ar="حرّر لائحة دفاع تستند إلى البحثين أعلاه.",
        subtype="defense_brief",
        edit_mode="fresh",
        plan_md="خطة مختصرة للائحة الدفاع.",
        analyzed_items=sources,
        system_templates=[],
        style=WriterStyle(detail_level="high", tone="formal"),
    )


def _make_writer_input(package, research_items: list[dict]):
    from agents.writer.models import WriterInput
    return WriterInput(
        user_id="smoke-user",
        conversation_id="smoke-conv",
        case_id=None,
        message_id="smoke-msg-id",
        user_request=package.intent_ar,
        subtype=package.subtype,
        research_items=research_items,
        workspace_context=None,
        revising_item_id=None,
        detail_level=package.style.detail_level,
        tone=package.style.tone,
    )


def _make_writer_llm_output(picks: list[tuple[str, int]]):
    """Build a WriterLLMOutput with the msg_434 citation pattern in new shape."""
    from agents.writer.models import (
        CitationRef,
        WriterLLMOutput,
        WriterSection,
    )
    citations = [CitationRef(wi=wi, n=n) for (wi, n) in picks]
    return WriterLLMOutput(
        title_ar="لائحة دفاع تجريبية للاختبار",
        sections=[
            WriterSection(
                heading_ar="## الوقائع",
                body_md="هذا قسم تجريبي يتضمن اقتباسات (1) و(5) و(15).",
            ),
            WriterSection(
                heading_ar="## الدفوع",
                body_md="مزيد من الإحالات: (6) و(24) و(31).",
            ),
        ],
        citations_used=citations,
        confidence="medium",
        notes_ar=["smoke note"],
        chat_summary="ملخص دخان.",
        key_findings=["نقطة تجريبية."],
    )


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def assert_no_uuid_leak(rendered: str) -> None:
    """Assert the rendered system prompt contains zero UUID strings."""
    found = _UUID_RE.findall(rendered)
    if found:
        raise AssertionError(
            f"UUID leaked into rendered prompt: {len(found)} match(es). "
            f"First few: {found[:3]}"
        )


def assert_wi_aliases_present(rendered: str) -> None:
    """Assert both WI-1 and WI-2 aliases appear in the rendered prompt."""
    for alias in (f'wi="WI-{WI_1_SEQ}"', f'wi="WI-{WI_2_SEQ}"'):
        if alias not in rendered:
            raise AssertionError(f"Expected alias {alias!r} missing from prompt")


def run_smoke() -> None:
    # ------- Part A: prompt rendering -------
    from agents.writer.prompts import render_package_for_system_prompt

    package = _make_writer_package()
    rendered = render_package_for_system_prompt(package)
    assert_no_uuid_leak(rendered)
    assert_wi_aliases_present(rendered)
    print("[A] prompt rendering: OK — wi='WI-1' / wi='WI-2' present, zero UUIDs leaked")

    # ------- Part B+C+D: publisher -------
    from agents.writer.deps import WriterDeps
    from agents.writer.publisher import publish_writer_result

    # research_items flowed from _from_package would carry wi_seq; build manually
    research_items = [
        {
            "item_id": WI_1_ID,
            "wi_seq": WI_1_SEQ,
            "title": WI_1_TITLE,
            "kind": "agent_search",
            "content_md": "<<<aa2506ef body_md placeholder>>>",
        },
        {
            "item_id": WI_2_ID,
            "wi_seq": WI_2_SEQ,
            "title": WI_2_TITLE,
            "kind": "agent_search",
            "content_md": "<<<a92c4741 body_md placeholder>>>",
        },
    ]

    picks = disambiguate_msg_434()
    print(f"    citation picks ({len(picks)}): {picks}")

    writer_in = _make_writer_input(package, research_items)
    llm_output = _make_writer_llm_output(picks)

    supabase_stub = _StubSupabase(SEED_REFS)
    deps = WriterDeps(
        supabase=supabase_stub,
        primary_model="smoke-model",
        task_label="smoke",
        describe_query=package.intent_ar,
        emit_sse=None,
        lock_ttl_seconds=60,
    )

    result = asyncio.run(publish_writer_result(llm_output, writer_in, deps))

    new_wi_id = result.item_id
    inserts_refs = supabase_stub.inserts.get("workspace_item_references", [])
    metadata_refs = result.metadata.get("references") or []

    # Persisted ref-row assertions
    if len(inserts_refs) != len(picks):
        raise AssertionError(
            f"Expected {len(picks)} new ref rows persisted, got {len(inserts_refs)}"
        )
    expected_ns = list(range(1, len(picks) + 1))
    actual_ns = [r["n"] for r in inserts_refs]
    if actual_ns != expected_ns:
        raise AssertionError(
            f"New ref n values not 1..K in body order. Expected {expected_ns}, got {actual_ns}"
        )
    if not all(r["used"] is True for r in inserts_refs):
        raise AssertionError("Not all persisted ref rows have used=True")
    if not all(r["wi_id"] == new_wi_id for r in inserts_refs):
        raise AssertionError(
            f"Persisted ref rows reference wrong wi_id (expected {new_wi_id})"
        )

    print(f"[B] persisted refs: OK — {len(inserts_refs)} rows, used=True, n=1..{len(picks)}, wi_id={new_wi_id[:8]}...")

    # Disambiguation verification — every persisted row's ref_id MUST trace back
    # to the source WI specified in its CitationRef tuple
    mismatches: list[str] = []
    for i, (pick_wi, pick_n) in enumerate(picks):
        new_row = inserts_refs[i]
        expected_source_uuid = WI_1_ID if pick_wi == "WI-1" else WI_2_ID
        # ref_id was synthesized as f"{domain}:{wi_id[:8]}-n{n}" — verify match
        if expected_source_uuid[:8] not in new_row["ref_id"] or f"-n{pick_n}" not in new_row["ref_id"]:
            mismatches.append(
                f"pick #{i+1} ({pick_wi}, n={pick_n}) → got ref_id={new_row['ref_id']}"
            )
    if mismatches:
        raise AssertionError(
            "Disambiguation failed for some picks:\n  " + "\n  ".join(mismatches)
        )
    ambiguous_picks = [(wi, n) for wi, n in picks if (n in WI_1_USED_NS and n in WI_2_USED_NS)]
    print(f"[C] disambiguation: OK — {len(ambiguous_picks)} ambiguous n values resolved correctly via wi tag")

    # metadata.references shape
    if len(metadata_refs) != len(picks):
        raise AssertionError(
            f"metadata.references count mismatch: expected {len(picks)}, got {len(metadata_refs)}"
        )
    for i, mref in enumerate(metadata_refs):
        for required in ("n", "source_wi", "source_n", "ref_id", "domain"):
            if required not in mref:
                raise AssertionError(f"metadata.references[{i}] missing field {required!r}")
        expected_source = picks[i][0]
        if mref["source_wi"] != expected_source:
            raise AssertionError(
                f"metadata.references[{i}].source_wi={mref['source_wi']!r}, expected {expected_source!r}"
            )
    print(f"[D] metadata.references: OK — {len(metadata_refs)} entries with full disambiguating shape")

    # Summary
    print()
    print("All smoke assertions passed.")
    print(f"  - Picks: {len(picks)} (ambiguous: {len(ambiguous_picks)})")
    print(f"  - Distinct sources resolved: {sorted({p[0] for p in picks})}")


if __name__ == "__main__":
    try:
        run_smoke()
    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
