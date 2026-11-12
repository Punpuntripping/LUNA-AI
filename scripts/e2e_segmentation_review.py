"""End-to-end review of attachment segmentation: OCR'd files → deep search → writer.

Runs the REAL message path (``backend.app.services.message_service.send_message_stream``
→ ``agents.orchestrator.handle_message``) in-process, as a TEST account, with the
segmentation flags on for this process only:

- ``HELPER_FRAME_ENABLED``, ``ATTACHMENT_SEGMENTER_ENABLED``,
  ``ATTACHMENT_SEGMENT_DELIVERY_ENABLED`` = true (env of this process only);
- the segment store and the helper-run store are in memory (migrations 215 / 216
  are not applied — nothing of them touches the database);
- no FastAPI app is started, so no scheduled job runs.

Writes, like any dev run, to the TEST account only: one conversation, its messages,
the attachments, the search / draft items, llm_calls rows.

Phases::

    python scripts/e2e_segmentation_review.py prepare --out DIR   # anonymized documents
    python scripts/e2e_segmentation_review.py run --out DIR       # the conversation
    python scripts/e2e_segmentation_review.py render --out DIR    # the review page

``prepare`` reads the source attachments and replaces every identifying detail
(names, numbers, places, dates) through one model-made mapping applied by code to
all files, so the same party keeps the same fictional name everywhere. Outputs hold
document text: keep them OUT of the repo.
"""
from __future__ import annotations

import os

# Flags for THIS process only — before anything reads the settings.
os.environ["HELPER_FRAME_ENABLED"] = "true"
os.environ["ATTACHMENT_SEGMENTER_ENABLED"] = "true"
os.environ["ATTACHMENT_SEGMENT_DELIVERY_ENABLED"] = "true"

import argparse  # noqa: E402
import asyncio  # noqa: E402
import html  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import unicodedata  # noqa: E402
from dataclasses import replace as dc_replace  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_USER_ID = "32c10d30-9d2a-4d50-966a-f397c1e3efae"  # enterprise@rayhanai.com (dev plan)
SOURCE_ITEMS = [  # Anwar's case (conversation b6fef8b4): judgment, hearing record, expert report
    "8f622010-f2a5-4734-afc3-82ba2a33adec",
    "3526c3a9-e4c4-443d-b85a-521d4d6acc00",
    "97959fac-eb6e-43a5-a5e5-2eef02fd624e",
]

Q1 = (
    "أرغب في الاعتراض على الحكم المرفق الذي ألزمني بالمتبقي من الأجرة، استنادًا إلى نقص "
    "المنفعة: تقرير الخبير المرفق أثبت تجمّع المياه في البدروم، وأسبابه كلها ترجع إلى "
    "إنشاءات المدعي. ابحث لي في نظام المعاملات المدنية والسوابق القضائية عن أساس الاعتراض "
    "وأثر نقص المنفعة على الأجرة."
)
Q1_REPLIES = [
    "لا، دعوى إثبات الضرر لم تُحسم بحكم نهائي بعد، لكن جميع أسباب الضرر في التقرير ترجع إلى المدعي. ابدأ البحث.",
    "ابدأ.",
]
Q2 = (
    "اكتب لي لائحة الاعتراض على الحكم الابتدائي بإلزام المستأجر بالمتبقي من الأجرة "
    "استنادًا إلى نقص المنفعة، مع طلب وقف التنفيذ."
)
Q2_REPLIES = ["موافق، ابدأ واكتب ولا تسألني مرة أخرى.", "موافق."]


def _client():
    from shared.db.client import get_supabase_client

    return get_supabase_client()


# --------------------------------------------------------------------------- #
# prepare — anonymize the source documents
# --------------------------------------------------------------------------- #

ANON_PROMPT = """\
You prepare Saudi legal documents (Arabic, OCR'd) for an internal test by replacing every
identifying detail with a FICTIONAL but plausible one. You do NOT rewrite the documents:
you return a list of exact strings to replace and what to put instead. Code applies it.

List every occurrence type of:
- person names (parties, lawyers, judges, experts, clerks, witnesses) — every spelling and
  every partial form that appears (full name, first name alone, «المدعي/فلان»);
- company / establishment / office names; commercial register, licence, national ID,
  iqama, passport, phone, IBAN / account, deed, plot, case, judgment, request and report
  numbers;
- addresses: district, street, building, plot and unit names (keep the city);
- Hijri and Gregorian dates — shift them all by the same offset (e.g. +3 months);
- money amounts — multiply by about 1.1 and round, keep the format (digits, commas,
  «ريال»).

Rules: `original` is copied EXACTLY from the text (same digits, same spelling, same
spacing); `replacement` keeps the same script (Arabic-Indic digits stay Arabic-Indic) and
a similar length; the same original always gets the same replacement; never list
ordinary legal words, article numbers of laws, or court / ministry names.
"""


async def _anon_mapping(docs: list[str]) -> list[dict]:
    from pydantic import BaseModel
    from pydantic_ai import Agent

    from agents.utils.agent_models import get_agent_model

    class Pair(BaseModel):
        original: str
        replacement: str
        kind: str

    class Mapping(BaseModel):
        pairs: list[Pair]

    agent = Agent(get_agent_model("artifact_summarizer"), output_type=Mapping, instructions=ANON_PROMPT)
    joined = "\n\n".join(f"===== DOCUMENT {i + 1} =====\n{d}" for i, d in enumerate(docs))
    # Only max_tokens at run time: pydantic-ai merges settings shallowly, so this
    # adds a key without dropping the model cell's thinking settings.
    res = await agent.run(joined, model_settings={"max_tokens": 32_000})
    return [p.model_dump() for p in res.output.pairs if p.original.strip() and p.original != p.replacement]


_DIGITS = "0123456789٠١٢٣٤٥٦٧٨٩"
_SCRAMBLE = str.maketrans("0123456789٠١٢٣٤٥٦٧٨٩", "7302958461٧٣٠٢٩٥٨٤٦١")
_LONG_NUMBER = re.compile(r"[0-9٠-٩]{6,}")


def _apply(text: str, pairs: list[dict]) -> tuple[str, int]:
    n = 0
    for p in sorted(pairs, key=lambda x: -len(x["original"])):
        if p["original"] in text:
            n += text.count(p["original"])
            text = text.replace(p["original"], p["replacement"])
    # Any long digit run the mapping missed (IDs, phones, deeds) is scrambled.
    text, k = _LONG_NUMBER.subn(lambda m: m.group(0).translate(_SCRAMBLE), text)
    return text, n + k


async def prepare(out: Path) -> None:
    sb = _client()
    resp = sb.table("workspace_items").select("item_id, title, content_md, metadata").in_("item_id", SOURCE_ITEMS).execute()
    rows = {str(r["item_id"]): r for r in resp.data}
    src = [rows[i] for i in SOURCE_ITEMS]
    texts = [r["content_md"] for r in src] + [r["title"] for r in src]
    pairs = await _anon_mapping([r["content_md"] for r in src])
    # One check pass: anything identifying left after the first mapping.
    first = [_apply(t, pairs)[0] for t in texts]
    more = await _anon_mapping(first[: len(src)])
    pairs += [p for p in more if all(p["original"] != q["original"] for q in pairs)]
    docs = []
    for r in src:
        content, n = _apply(r["content_md"], pairs)
        title, _ = _apply(r["title"], pairs)
        docs.append({
            "title": title,
            "content_md": content,
            "doc_type_hint": (r.get("metadata") or {}).get("doc_type"),
            "replacements": n,
            "chars": len(content),
        })
    kinds: dict[str, int] = {}
    for p in pairs:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    out.mkdir(parents=True, exist_ok=True)
    (out / "e2e_docs.json").write_text(json.dumps({"docs": docs, "mapping_kinds": kinds}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("pairs", len(pairs), kinds)
    for d in docs:
        print(d["doc_type_hint"], d["chars"], "replacements", d["replacements"])


# --------------------------------------------------------------------------- #
# run — the conversation
# --------------------------------------------------------------------------- #


class _MemRunStore:
    """In-memory ``helper_runs`` (migration 216 is not applied)."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    _rows: dict[tuple[str, str], Any] = {}

    def get(self, item_id, helper):
        return self._rows.get((item_id, helper))

    def claim(self, *, item_id, helper, user_id, prev, attempt, lease_until, version):
        from agents.helpers.status import RunRow

        cur = self._rows.get((item_id, helper))
        if (prev is None and cur is not None) or (prev is not None and cur != prev):
            return None
        row = RunRow(item_id=item_id, helper=helper, status="running", attempt=attempt,
                     lease_until=lease_until, version=version)
        self._rows[(item_id, helper)] = row
        return row

    def renew(self, row, lease_until):
        cur = self._rows.get((row.item_id, row.helper))
        if cur is None or cur.attempt != row.attempt:
            return False
        self._rows[(row.item_id, row.helper)] = dc_replace(cur, lease_until=lease_until)
        return True

    def finish(self, row, *, status, error):
        self._rows[(row.item_id, row.helper)] = dc_replace(row, status=status, error=error, lease_until=None)
        return True


_MAPS: dict[str, dict] = {}
CAPTURE: dict[str, Any] = {"deep_search": [], "writer_inputs": [], "waits": [], "segmenter": []}


def _install_patches() -> None:
    import agents.helpers.status as hstatus
    import agents.helpers.turn as hturn
    import agents.memory.attachment_segmenter as segpkg
    import agents.memory.attachment_segmenter.store as segstore
    from agents.memory.attachment_segmenter.models import StoredMap, StoredSegment

    hstatus.SupabaseHelperRunStore = _MemRunStore  # type: ignore[assignment]
    hturn.SupabaseHelperRunStore = _MemRunStore  # type: ignore[assignment]

    def map_state(_sb, item_id):
        m = _MAPS.get(str(item_id))
        return (m["row"]["content_chars"], bool(m["segments"])) if m else None

    def write_segment_map(_sb, map_row, segment_rows):
        _MAPS[str(map_row["item_id"])] = {"row": dict(map_row), "segments": [dict(r) for r in segment_rows]}
        CAPTURE["segmenter"].append({"item_id": str(map_row["item_id"]), "segments": len(segment_rows),
                                     "bundle_id": map_row.get("bundle_id"), "at": time.time()})

    def _stored(item_id):
        m = _MAPS.get(str(item_id))
        if not m or not m["segments"]:
            return None
        return StoredMap(**m["row"], segments=[StoredSegment(**s) for s in sorted(m["segments"], key=lambda s: s["seq"])])

    def load_segment_map(_sb, item_id):
        return _stored(item_id)

    def load_segment_maps(_sb, item_ids, *, user_id=None, content_chars=None):
        return {str(i): m for i in item_ids if (m := _stored(i)) is not None}

    segstore.map_state = map_state
    segstore.write_segment_map = write_segment_map
    segstore.load_segment_map = load_segment_map
    segstore.load_segment_maps = load_segment_maps
    segstore.load_pages = lambda _sb, _i: []
    segpkg.load_segment_map = load_segment_map
    segpkg.load_segment_maps = load_segment_maps

    # Capture what each reader receives.
    import agents.deep_search_v4.orchestrator as dso
    import agents.utils.attachment_sections as asec
    import agents.writer_planner.walkers as walkers

    orig_build = dso._build_case_document_lists

    def build(decision, deps, sections_by_id=None):
        lists, tel = orig_build(decision, deps, sections_by_id)
        CAPTURE["deep_search"].append({
            "case_full_text_wis": list(getattr(decision, "case_full_text_wis", None) or []),
            "reg_full_text_wis": list(getattr(decision, "reg_full_text_wis", None) or []),
            "segmented_path": sections_by_id is not None,
            "lists": {k: [{"item_id": b.source_item_id, "body": b.body} for b in v] for k, v in lists.items()},
            "telemetry": {k: v for k, v in tel.items() if isinstance(v, (int, float, str, list))},
        })
        return lists, tel

    dso._build_case_document_lists = build

    orig_wait = asec.wait_and_load_sections

    async def wait(item_ids, **kw):
        t0 = time.perf_counter()
        out, tel = await orig_wait(item_ids, **kw)
        CAPTURE["waits"].append({"consumer": kw.get("consumer"), "items": list(item_ids),
                                 "waited_s": round(time.perf_counter() - t0, 1), "telemetry": tel})
        return out, tel

    asec.wait_and_load_sections = wait

    orig_items = walkers.build_analyzed_items_direct

    async def items(selected_ids, role_assignments, deps):
        out = await orig_items(selected_ids, role_assignments, deps)
        CAPTURE["writer_inputs"].append([
            {"item_id": a.item_id, "kind": a.kind, "role": a.role, "title": a.title,
             "doc_type": getattr(a, "doc_type", ""), "body_md": a.body_md}
            for a in out
        ])
        return out

    walkers.build_analyzed_items_direct = items


class _Req:
    """What ``send_message_stream`` uses of the request."""

    app = SimpleNamespace(state=SimpleNamespace(redis=None))

    async def is_disconnected(self) -> bool:
        return False


def _parse_sse(chunk: str) -> list[tuple[str, dict]]:
    out = []
    for block in chunk.split("\n\n"):
        ev, data = "", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if ev:
            try:
                out.append((ev, json.loads(data or "{}")))
            except Exception:  # noqa: BLE001
                out.append((ev, {"raw": data}))
    return out


async def _turn(sb, conv: dict, content: str, attachment_ids: list[str] | None) -> dict:
    from backend.app.services.message_service import send_message_stream

    events: list[tuple[str, dict]] = []
    t0 = time.perf_counter()
    async for chunk in send_message_stream(
        sb, user_id=TEST_USER_ID, conversation_id=conv["conversation_id"], conv=conv,
        content=content, request=_Req(), attachment_ids=attachment_ids,
    ):
        events.extend(_parse_sse(chunk))
    tokens = "".join(d.get("text", "") for e, d in events if e == "token")
    kinds: dict[str, int] = {}
    for e, _ in events:
        kinds[e] = kinds.get(e, 0) + 1
    return {
        "user": content,
        "seconds": round(time.perf_counter() - t0, 1),
        "events": kinds,
        "agent_family": next((d.get("agent_family") for e, d in events if e in ("agent_run_started", "agent_selected")), None),
        "paused": any(e == "agent_question" for e, _ in events),
        "progress": [d.get("text") for e, d in events if e == "agent_progress" and d.get("text")],
        "reply": tokens,
        "errors": [d for e, d in events if e == "error"],
    }


async def _run_turn_with_replies(sb, conv, content, attachment_ids, replies) -> list[dict]:
    turns = [await _turn(sb, conv, content, attachment_ids)]
    for reply in replies:
        if not turns[-1]["paused"]:
            break
        turns.append(await _turn(sb, conv, reply, None))
    return turns


def _items_of(sb, conversation_id: str) -> list[dict]:
    resp = (
        sb.table("workspace_items")
        .select("item_id, wi_seq, kind, title, content_md, summary, metadata, created_at")
        .eq("conversation_id", conversation_id).is_("deleted_at", "null").order("wi_seq").execute()
    )
    return list(resp.data or [])


async def run(out: Path) -> None:
    _install_patches()
    from agents.memory.summarize import summarize_workspace_item

    sb = _client()
    docs = json.loads((out / "e2e_docs.json").read_text(encoding="utf-8"))["docs"]
    conv = sb.table("conversations").insert({"user_id": TEST_USER_ID, "title_ar": "اختبار التقسيم — اعتراض على حكم أجرة"}).execute().data[0]
    print("conversation", conv["conversation_id"])
    att_ids = []
    for i, d in enumerate(docs, 1):
        row = sb.table("workspace_items").insert({
            "user_id": TEST_USER_ID,
            "conversation_id": conv["conversation_id"],
            "kind": "attachment",
            "title": d["title"],
            "content_md": d["content_md"],
            "metadata": {
                "ocr_status": "done", "ocr_chars": len(d["content_md"]),
                "ocr_pages": max(1, len(d["content_md"]) // 2500), "mime_type": "application/pdf",
                "file_name": f"test_document_{i}.pdf", "test_fixture": "e2e_segmentation_review",
            },
        }).execute().data[0]
        att_ids.append(str(row["item_id"]))
    # «assume after the OCR»: the text is in; the summaries run as they would after OCR.
    for iid in att_ids:
        await summarize_workspace_item(sb, iid)

    t1 = await _run_turn_with_replies(sb, conv, Q1, att_ids, Q1_REPLIES)
    after_search = _items_of(sb, conv["conversation_id"])
    t2 = await _run_turn_with_replies(sb, conv, Q2, None, Q2_REPLIES)
    items = _items_of(sb, conv["conversation_id"])

    # Let background helpers (memory agent, late segmenter runs) settle.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=120)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conversation_id": conv["conversation_id"],
        "attachments": att_ids,
        "turn1": t1,
        "turn2": t2,
        "items": items,
        "search_items_after_turn1": [i for i in after_search if i["kind"] == "agent_search"],
        "maps": _MAPS,
        "capture": CAPTURE,
    }
    (out / "e2e_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("turn1", [(t["agent_family"], t["seconds"], t["paused"]) for t in t1])
    print("turn2", [(t["agent_family"], t["seconds"], t["paused"]) for t in t2])
    print("items", [(i["wi_seq"], i["kind"], len(i.get("content_md") or "")) for i in items])


# --------------------------------------------------------------------------- #
# render — the review page
# --------------------------------------------------------------------------- #

_QUOTE = re.compile(r"«([^«»]{12,})»|\"([^\"]{12,})\"")
_DIAC = re.compile(r"[ً-ْـ‏‎]")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _DIAC.sub("", text)
    text = re.sub(r"[إأآ]", "ا", text).replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"\s+", " ", text).strip()


def _quotes(text: str, sources: list[str]) -> list[dict]:
    hay = [_norm(s) for s in sources]
    out = []
    for m in _QUOTE.finditer(text or ""):
        q = (m.group(1) or m.group(2) or "").strip().strip(".،")
        nq = _norm(q)
        if len(nq) < 12:
            continue
        found = any(nq in h for h in hay)
        out.append({"quote": q, "verbatim": found})
    return out


def render(out: Path) -> None:
    r = json.loads((out / "e2e_result.json").read_text(encoding="utf-8"))
    att = [i for i in r["items"] if i["kind"] == "attachment"]
    sources = [i.get("content_md") or "" for i in att]
    search = [i for i in r["items"] if i["kind"] == "agent_search"]
    drafts = [i for i in r["items"] if i["kind"] in ("agent_writing", "agent_writer")]
    e = html.escape

    def pre(t: str) -> str:
        return f'<pre class="txt ar">{e(t or "")}</pre>'

    def qtable(text: str) -> str:
        qs = _quotes(text, sources)
        if not qs:
            return '<p class="muted">No quotes («…») of 12+ characters.</p>'
        rows = "".join(
            f'<tr><td class="ar">{e(q["quote"][:220])}</td><td>{"verbatim" if q["verbatim"] else "<b>not in the documents</b>"}</td></tr>'
            for q in qs
        )
        ok = sum(q["verbatim"] for q in qs)
        return f'<p><b>{ok} of {len(qs)}</b> quotes appear word for word in the attachments.</p><div class="scroll"><table><tr><th>Quote</th><th>Check</th></tr>{rows}</table></div>'

    docs_html = []
    for a in att:
        m = r["maps"].get(str(a["item_id"])) or {}
        segs = m.get("segments") or []
        vc: dict[str, int] = {}
        for s in segs:
            vc[s["value"]] = vc.get(s["value"], 0) + len(s.get("content_md") or "")
        segrows = "".join(
            f'<tr><td>{s["seq"]}</td><td class="mono">P{s["from_p"]}–P{s["to_p"]}</td><td class="mono">{e(s["label"])}</td>'
            f'<td><span class="chip v-{e(s["value"])}">{e(s["value"])}</span></td><td class="ar">{e(s.get("what"))}</td><td>{len(s.get("content_md") or ""):,}</td></tr>'
            for s in segs
        )
        docs_html.append(
            f'<section class="sheet"><span class="eyebrow">WI-{a["wi_seq"]} · {e((a.get("metadata") or {}).get("doc_type") or "unset")} · {len(a.get("content_md") or ""):,} chars</span>'
            f'<h3 class="ar">{e(a["title"])}</h3>'
            f'<p class="muted">{"Segmented: " + ", ".join(f"{k} {v:,}" for k, v in vc.items()) if segs else "Not segmented (under 5,000 characters, not in a bundle) — travels whole."}</p>'
            + (f'<details><summary>Sections ({len(segs)})</summary><div class="scroll"><table><tr><th>#</th><th>Paragraphs</th><th>Label</th><th>Value</th><th>Description</th><th>Chars</th></tr>{segrows}</table></div></details>' if segs else "")
            + f'<details><summary>Summary</summary>{pre(a.get("summary") or "")}</details></section>'
        )

    ds_html = []
    for k, cap in enumerate(r["capture"]["deep_search"]):
        tabs = []
        for rec in ("case", "reg", "aggregator"):
            bodies = "\n\n".join(b["body"] for b in cap["lists"].get(rec, []))
            tabs.append(f'<details><summary>{rec} — {len(bodies):,} chars</summary>{pre(bodies)}</details>')
        ds_html.append(
            f'<section class="sheet"><h3>Deep search run {k + 1} — what each reader received</h3>'
            f'<p class="mono">segmented path: {cap["segmented_path"]} · planner named for the rulings search: {e(", ".join(cap["case_full_text_wis"]) or "none")} · for the regulations search: {e(", ".join(cap["reg_full_text_wis"]) or "none")}</p>'
            f'<pre class="mono small">{e(json.dumps(cap["telemetry"], ensure_ascii=False, indent=1))}</pre>{"".join(tabs)}</section>'
        )

    waits = "".join(
        f'<li class="mono">{e(str(w["consumer"]))}: waited {w["waited_s"]} s · {e(json.dumps(w["telemetry"].get("segments.readiness"), ensure_ascii=False))}</li>'
        for w in r["capture"]["waits"]
    )
    w_html = []
    for k, items in enumerate(r["capture"]["writer_inputs"]):
        parts = "".join(
            f'<details><summary>{e(it["kind"])} · {e(it["role"])} · {e(it.get("doc_type") or "")} · {len(it["body_md"]):,} chars — <span class="ar">{e(it["title"][:80])}</span></summary>{pre(it["body_md"])}</details>'
            for it in items
        )
        w_html.append(f'<section class="sheet"><h3>Writer run {k + 1} — what the writer received</h3>{parts}</section>')

    def turns(ts: list[dict]) -> str:
        return "".join(
            f'<div class="turn"><p class="ar"><b>المستخدم:</b> {e(t["user"])}</p>'
            f'<p class="mono">{e(str(t["agent_family"]))} · {t["seconds"]} s · paused: {t["paused"]} · events {e(json.dumps(t["events"]))}</p>'
            f'<details><summary>Reply in chat ({len(t["reply"]):,} chars)</summary>{pre(t["reply"])}</details></div>'
            for t in ts
        )

    search_html = "".join(
        f'<section class="sheet"><h3>Search answer — WI-{s["wi_seq"]} ({len(s.get("content_md") or ""):,} chars)</h3>{qtable(s.get("content_md") or "")}'
        f'<details><summary>Full answer</summary>{pre(s.get("content_md"))}</details></section>'
        for s in search
    )
    draft_html = "".join(
        f'<section class="sheet"><h3>Draft — WI-{d["wi_seq"]} ({len(d.get("content_md") or ""):,} chars)</h3>{qtable(d.get("content_md") or "")}'
        f'<details><summary>Full draft</summary>{pre(d.get("content_md"))}</details></section>'
        for d in drafts
    )
    css = """
:root{--bg:#f6f7f4;--surface:#fff;--fg:#18211c;--muted:#5d6a62;--line:#dfe3dc;--accent:#2f6b4f;
--v-metadata:#4a6fa5;--v-core:#2f7d4f;--v-supporting:#b7862b;--v-boilerplate:#9aa29c;--v-repeat:#b4545f;
--font-ui:"Inter",system-ui,sans-serif;--font-ar:"Noto Naskh Arabic","Segoe UI",Tahoma,serif;--font-mono:"JetBrains Mono",ui-monospace,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111613;--surface:#181f1b;--fg:#e4ebe6;--muted:#98a59d;--line:#2a332e;--accent:#7cc49d;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#111613;--surface:#181f1b;--fg:#e4ebe6;--muted:#98a59d;--line:#2a332e;--accent:#7cc49d;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--font-ui);font-size:14px;line-height:1.5}
.wrap{max-width:1100px;margin:0 auto;padding-block:24px 60px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:20px}
h1,h2,h3{margin:0;text-wrap:balance}.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:11px;color:var(--muted);font-weight:600}
.muted{color:var(--muted)}.mono{font-family:var(--font-mono);font-size:12px}.small{font-size:11px;white-space:pre-wrap}
.ar{font-family:var(--font-ar);direction:rtl;text-align:right;line-height:1.9}
.sheet{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:18px;display:grid;gap:10px;min-width:0}
.turn{border-inline-start:3px solid var(--accent);padding-inline-start:12px}
pre.txt{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;max-height:520px;overflow:auto;margin:0}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.chip{border-radius:999px;padding:0 8px;font-size:12px;font-weight:600;border:1px solid var(--line);color:var(--c)}
.v-metadata{--c:var(--v-metadata)}.v-core{--c:var(--v-core)}.v-supporting{--c:var(--v-supporting)}.v-boilerplate{--c:var(--v-boilerplate)}.v-repeat{--c:var(--v-repeat)}
details>summary{cursor:pointer;color:var(--accent);font-weight:600}
"""
    page = f"""<title>Segmentation E2E Review</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Noto+Naskh+Arabic:wght@400;600&family=JetBrains+Mono&display=swap">
<style>{css}</style>
<div class="wrap">
<header><span class="eyebrow">attachments → segmenter → deep search → writer · test account · anonymized documents</span>
<h1>Segmentation E2E Review</h1>
<p class="mono">conversation {e(r["conversation_id"])} · {e(r["generated_at"])}</p></header>
<section class="sheet"><h2>The documents</h2></section>{''.join(docs_html)}
<section class="sheet"><h2>Turn 1 — the search</h2>{turns(r["turn1"])}<p class="muted">Segmenter waits:</p><ul>{waits}</ul></section>
{''.join(ds_html)}{search_html}
<section class="sheet"><h2>Turn 2 — the writer</h2>{turns(r["turn2"])}</section>
{''.join(w_html)}{draft_html}
</div>"""
    (out / "e2e_review.html").write_text(page, encoding="utf-8")
    print("written", out / "e2e_review.html")


def _outside_repo(path: str) -> Path:
    """The output holds customer documents: refuse any folder inside the repo."""
    out = Path(path).resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit(f"--out must be OUTSIDE the repo ({ROOT}); use the scratchpad")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["prepare", "run", "render"])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = _outside_repo(args.out)
    if args.phase == "prepare":
        asyncio.run(prepare(out))
    elif args.phase == "run":
        asyncio.run(run(out))
    else:
        render(out)


if __name__ == "__main__":
    main()
