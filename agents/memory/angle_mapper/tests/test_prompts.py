"""Prompt — the version, v2 parity with the tested experiment, the v3 inserts, the
v4 (one map per message) inserts, the v5 edits (strength a signal, weakness,
the angle's ocr_suspect, one question, the twelve rules), the per-type lines of a
message's documents and the user message's lines (the unread files, one block per
document)."""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

import pytest

from agents.memory.angle_mapper import prompts as P
from agents.memory.attachment_segmenter.models import StoredSegment

from . import fixtures as F

REPO = Path(__file__).resolve().parents[4]
EXPERIMENT = Path(".claude/plans/attachment_segmentation/experiments/run_angles.py")

TEN_TYPES = (
    "judgment", "contract", "hearing_minutes", "claim", "memo", "reference", "expert_report", "letter",
    "financial", "official_record",
)
USER_SAID = "## What the user said (their own messages up to the upload — use them to know their side and concern)"


# ── the experiment file (read, never imported: it reads env vars at import) ──


def _checkouts() -> list[Path]:
    """This checkout, and — in a git worktree — the main checkout (the experiment
    file lives untracked under ``.claude/plans`` there)."""
    out = [REPO]
    git = REPO / ".git"
    if git.is_file():
        text = git.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            if gitdir.parent.name == "worktrees":
                out.append(gitdir.parent.parent.parent)
    return out


def _experiment_path() -> Path | None:
    env = os.environ.get("ANGLE_EXPERIMENT_FILE")
    candidates = ([Path(env)] if env else []) + [root / EXPERIMENT for root in _checkouts()]
    return next((p for p in candidates if p.is_file()), None)


@pytest.fixture(scope="module")
def experiment() -> dict[str, Any]:
    path = _experiment_path()
    if path is None:
        pytest.skip("run_angles.py (the tested angles-v2 experiment) is not on this machine")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("HEAD", "TYPES", "PROMPT_VERSION"):
                out[node.targets[0].id] = ast.literal_eval(node.value)
    assert {"HEAD", "TYPES", "PROMPT_VERSION"} <= set(out), sorted(out)
    return out


def test_experiment_is_the_tested_v2(experiment):
    assert experiment["PROMPT_VERSION"] == "angles-v2"


def test_v2_head_is_byte_identical(experiment):
    assert P._HEAD_V2 == experiment["HEAD"]


def test_types_are_the_experiments_but_signatures(experiment):
    # 2026-11-27 (owner): OCR does not read signatures — "signatures" left the contract's
    # prime contradictions; every other type is the tested experiment's, byte for byte.
    exp = dict(experiment["TYPES"])
    what, angles = exp["contract"]
    exp["contract"] = (what, angles.replace("(dates, amounts, parties, signatures)", "(dates, amounts, parties)"))
    assert P.TYPES == exp


def test_signatures_are_no_contradiction_material():
    assert "signatures" not in P.TYPES["contract"][1]


# ── pinned without the experiment file (a clean clone / CI) ─────────────────


def test_version():
    assert P.PROMPT_VERSION == "angles-v5"


def test_the_ten_tested_types():
    assert P.DOC_TYPES == TEN_TYPES
    assert tuple(P.TYPES) == TEN_TYPES


@pytest.mark.parametrize("sentence", [
    "Your job: find the ANGLES (أوجه) worth a legal search",
    "The ownership is already decided: whose words each section are and which side relies on it. Use it; do not "
    "redo it.",
    "never infer the side from who wrote the uploaded documents or from the summary.",
    "Code labels each one as the client's, the opponent's or neutral from its owner, so do not sort them.",
    "Two angles that would send the search after the same legal question are ONE angle.",
    "Never state what the law says as settled — that is the `legal_question` the search will answer.",
    "Every core section is in an angle, in `context`, or in `no_angle` with a reason — check the list of core "
    "sections one by one before you answer.",
    "- `answer` angles are the opponent's real strengths, not straw men.",
    "- All text in Arabic.",
])
def test_key_v2_sentences_survive(sentence):
    assert sentence in P._HEAD_V2
    assert sentence in P.HEAD


# ── v3 ───────────────────────────────────────────────────────────────────────


def test_v3_is_v2_plus_exactly_the_three_inserts():
    assert len(P._V3_EDITS) == 3
    assert P.HEAD_V3 == P._apply(P._HEAD_V2, P._V3_EDITS)
    for old, new in P._V3_EDITS:
        assert P._HEAD_V2.count(old) == 1
        assert P.HEAD_V3.count(new) == 1
        assert new.startswith(old[:-1]) or new.startswith(old)
    undone = P.HEAD_V3
    for old, new in reversed(P._V3_EDITS):
        undone = undone.replace(new, old)
    assert undone == P._HEAD_V2


def test_v3_contract_client_kind_balance_and_for_party_are_in_v4():
    assert P._CLEAR_LINE + P._CONTRACT_CLIENT in P.HEAD
    assert "`side` = `contract_party` and `party` = that role copied exactly" in P.HEAD
    assert P._ANSWER_LINE + P._KIND_BALANCE in P.HEAD
    assert "Never add a weak or invented angle just to meet this" in P.HEAD
    assert "For a contract party (`contract_party`), `for_party` is its role copied exactly" in P.HEAD


# ── v4: one map per message ──────────────────────────────────────────────────


def test_v4_is_v3_plus_exactly_its_edits():
    assert len(P._V4_EDITS) == 7
    assert P.HEAD_V4 == P._apply(P.HEAD_V3, P._V4_EDITS)
    for old, new in P._V4_EDITS:
        assert P.HEAD_V3.count(old) == 1, old[:60]
        assert P.HEAD_V4.count(new) == 1, new[:60]
    undone = P.HEAD_V4
    for old, new in reversed(P._V4_EDITS):
        undone = undone.replace(new, old)
    assert undone == P.HEAD_V3


@pytest.mark.parametrize("sentence", [
    "reading the documents a user uploaded together, in ONE message",
    "(D1·S1, D1·S2…, D2·S1…; the numbers restart in every document)",
    "(paragraph numbers restart in every document too)",
    "`by_document`: owner keys are relative to each document's own proceeding",
    "one party can be `respondent` in a judgment and `claimant` in the objection it files against that judgment",
    "[] when they are the same everywhere (always [] with one document).",
    "the section refs the angle turns on, each written as listed (`D1·S7` — a ref names its document)",
    "- `context` — section refs every search on this file should keep in view",
    "- `no_angle` — each: `section` (one ref)",
    "every core section, of every document,",
    "- `unrelated_documents` — each: `document` (`D2`), `why` (Arabic, one sentence)",
    "## Several documents in one message",
    "the strongest angles often sit BETWEEN them",
    "Never invent a link the documents do not show.",
    "Decide the client's side ONCE for the whole message",
    "name it in `unrelated_documents`, never connect it to the other documents",
    "A contradiction between two documents is ONE gap whose `sections` name both.",
])
def test_key_v4_sentences(sentence):
    assert sentence in P.HEAD
    assert sentence not in P.HEAD_V3


@pytest.mark.parametrize("gone", [
    "reading one document a user uploaded",
    "(S7 → 7)",
    "- `context` — section numbers every search",
    "and the whole document as numbered paragraphs [P001], [P002]…\n",
])
def test_v4_drops_the_single_document_wording(gone):
    assert gone in P.HEAD_V3 or gone in P._HEAD_V2
    assert gone not in P.HEAD


def test_multi_doc_block_sits_before_the_rules():
    assert P.HEAD.index("## Several documents in one message") < P.HEAD.index("## Rules")
    assert P.HEAD.index("## Output") < P.HEAD.index("## Several documents in one message")
    assert P.HEAD.index("`by_document`") > P.HEAD.index(P._CONTRACT_CLIENT)


# ── v5: the review's fixes ───────────────────────────────────────────────────


def test_v5_is_v4_plus_exactly_its_edits():
    assert len(P._V5_EDITS) == 4
    assert P.HEAD == P._apply(P.HEAD_V4, P._V5_EDITS)
    for old, new in P._V5_EDITS:
        assert P.HEAD_V4.count(old) == 1, old[:60]
        assert P.HEAD.count(new) == 1, new[:60]
    undone = P.HEAD
    for old, new in reversed(P._V5_EDITS):
        undone = undone.replace(new, old)
    assert undone == P.HEAD_V4


def test_v5_strength_is_only_a_signal():
    assert ("  - `strength`: strong | medium | weak — the map's first signal from the file alone; the searches judge "
            "it.\n") in P.HEAD
    assert "how likely the angle changes the outcome" not in P.HEAD
    # no calibration rule, no "too many strong" check
    assert P.HEAD.count("`strong`") == 0 and "most client angles" not in P.HEAD


def test_v5_the_angles_ocr_suspect_comes_after_weakness():
    line = ("  - `ocr_suspect`: true when the angle rests on a figure, date, number or name the OCR could have misread "
            "(one reading, not confirmed elsewhere in the file); false when it rests on what the file shows clearly.\n")
    assert line in P.HEAD and line not in P.HEAD_V4
    assert P.HEAD.index("  - `weakness`:") < P.HEAD.index(line) < P.HEAD.index("- `context` —")
    # the gaps' own ocr_suspect is unchanged
    assert "`ocr_suspect` (true when the gap rests on a single figure or date the OCR could have misread)" in P.HEAD


#: The owner's twelve rules (angles-v5), each by a key phrase.
V5_RULES = [
    (1, "belongs to the owner of the section it comes from — never move it to another party, memo or document"),
    (1, "a party's description of something not in the file is that party's claim, not a fact"),
    (2, "exactly ONE question (one «؟»)"),
    (2, "Angles one search would answer are ONE angle."),
    (3, "the strongest fact in the file against the angle, looking first at the party's own documents and words"),
    (3, "for an `answer` angle, what in the file the party can use against the opponent's point"),
    (4, "Never write a rule as a conclusion inside `argument` / `weakness`; the rule is the search's question."),
    (5, "Compute before you compare (totals against their parts, day counts from the file's own dates)"),
    (5, "put the documents' dates in order before arguing"),
    (5, "A `contradiction` gap names two DIFFERENT values or statements of the SAME matter — never equal values, "
        "unrelated templates or OCR noise."),
    (6, "No angle on an issue a later document settled, or on a procedural point the file does not dispute"),
    (7, "rests on a threshold bar (deadline, jurisdiction, standing, failure of proof), give first the routes around "
        "that bar the file supports"),
    (8, "Include the opponent's main legal basis, as the file states it, among the `answer` angles."),
    (9, "Garbled or looping text and the system's own extraction notes are reading errors — never an angle; at most "
        "one gap for them, with `ocr_suspect: true`."),
    (9, "A blank left in the original is a real `missing` gap (`ocr_suspect: false`)."),
    (10, "A fact only the user states appears only as a condition («إن ثبت ما يذكره المستخدم…») or a gap, never as "
         "a fact"),
    (10, "words inside «» are copied exactly from the cited section"),
    (11, "check who granted what power over what, person by person"),
    (11, "religion → Ramadan working hours; a lease vs a sale on credit"),
    (12, "every angle has `for_side` `contract_party` and `for_party` = the role it protects, copied from the owner "
         "words — never `unknown` or «الطرفان»"),
    # 2026-11-27 (owner): an empty signature field in OCR text proves nothing
    (13, "An empty signature, stamp or initials field is not a blank: OCR does not read handwriting or stamps"),
    # 2026-11-28 (owner): both ways — an empty field shows neither signed nor unsigned
    (13, "shows neither that a document is signed nor that it is unsigned — never a gap, a weakness, a "
         "contradiction or a point in favour"),
    (13, "Only the file's own words can say so («غير موقّع», «وقّعه الطرفان»)."),
]


@pytest.mark.parametrize("rule, phrase", V5_RULES)
def test_v5_the_twelve_rules(rule, phrase):
    assert P.HEAD.count(phrase) == 1, rule
    assert phrase not in P.HEAD_V4, rule


def test_v5_every_rule_is_covered_and_short():
    assert {r for r, _ in V5_RULES} == set(range(1, 14))
    bullets = [b for b in P._V5_RULES.split("\n") if b]
    assert len(bullets) == 11 and all(b.startswith("- ") for b in bullets)  # rules 2 and 3 sit in their fields
    assert all(len(b) <= 320 for b in bullets)  # one or two lines each, no long examples
    rules = P.HEAD.index("## Rules")
    assert rules < P.HEAD.index(bullets[0]) and P.HEAD.index(bullets[-1]) < P.HEAD.index("- All text in Arabic.")
    assert P.HEAD.endswith("- All text in Arabic.\n")


def test_an_anchor_must_occur_once():
    with pytest.raises(RuntimeError, match="once"):
        P._replace_once("abc", "x", "y")
    with pytest.raises(RuntimeError, match="once"):
        P._replace_once("aa", "a", "b")
    assert P._replace_once("abc", "b", "B") == "aBc"


# ── the system instructions: one line per type present ───────────────────────


def _line(docs: str, doc_type: str | None) -> str:
    what, angles = P.type_line(doc_type)
    return f"- {docs} — {what}. Angles here: {angles}."


@pytest.mark.parametrize("doc_type", TEN_TYPES)
def test_instructions_for_one_document(doc_type):
    assert P.build_instructions(doc_type) == P.HEAD + "\n## The documents of this message\n" + _line("D1", doc_type) + "\n"
    assert P.build_instructions([doc_type]) == P.build_instructions(doc_type)


def test_instructions_group_the_documents_by_type_in_first_seen_order():
    text = P.build_instructions(["claim", "judgment", "claim", "letter"])
    assert text == P.HEAD + "\n".join([
        "", "## The documents of this message",
        _line("D1, D3", "claim"), _line("D2", "judgment"), _line("D4", "letter"),
    ]) + "\n"


@pytest.mark.parametrize("doc_types", [["chat"], [None], [""], ["other", None, "user_notes"], None, "statement"])
def test_untested_types_get_the_generic_line(doc_types):
    text = P.build_instructions(doc_types)
    n = 1 if doc_types is None or isinstance(doc_types, str) else len(doc_types)
    docs = ", ".join(f"D{i}" for i in range(1, n + 1))
    assert text.endswith("\n## The documents of this message\n" + _line(docs, None) + "\n")
    assert P.type_line("chat") == P.GENERIC_TYPE == P.type_line(None)
    assert "where it confirms or contradicts the other documents of the message" in P.GENERIC_TYPE[1]


def test_known_and_generic_types_mix():
    text = P.build_instructions(["judgment", "chat", None])
    assert text.endswith("\n" + _line("D1", "judgment") + "\n" + _line("D2, D3", None) + "\n")


# ── the user message, one block per document ─────────────────────────────────


def pdoc(doc: F.Doc, index: int, **kw: Any) -> P.PromptDocument:
    smap = F.seg_map_row(doc)
    args: dict[str, Any] = {
        "index": index, "title": doc.title, "doc_type": doc.doc_type, "content_md": F.content(doc),
        "summary": doc.summary, "documents_in_file": smap["documents_in_file"],
        "sections": F.segment_rows(doc), "key_paragraphs": smap["key_paragraphs"],
    }
    args.update(kw)
    return P.PromptDocument(**args)


def _msg(*docs: P.PromptDocument, user_words: str = "") -> str:
    return P.build_user_message(documents=list(docs), user_words=user_words)


TWO = (pdoc(F.JUDGMENT, 1), pdoc(F.OBJECTION, 2))


def test_message_header_counts_documents_and_characters():
    lines = _msg(*TWO).split("\n")
    total = len(F.content(F.JUDGMENT)) + len(F.content(F.OBJECTION))
    assert lines[:4] == ["## The message", f"- 2 documents · {total:,} characters", USER_SAID, P.NO_USER_WORDS]
    one = _msg(pdoc(F.JUDGMENT, 1)).split("\n")
    assert one[1] == f"- 1 document · {len(F.content(F.JUDGMENT)):,} characters"


def test_thousands_separator_in_the_sizes():
    big = F.content(F.JUDGMENT, big=True)
    msg = _msg(pdoc(F.JUDGMENT, 1, content_md=big, sections=[], key_paragraphs=[], documents_in_file=[]))
    assert f"- 1 document · {len(big):,} characters" in msg and "," in f"{len(big):,}"
    assert f"· judgment · 12 paragraphs · {len(big):,} characters" in msg


@pytest.mark.parametrize("words", ["", "   \n  ", None])
def test_no_user_words_placeholder(words):
    lines = _msg(*TWO, user_words=words).split("\n")
    assert lines[lines.index(USER_SAID) + 1] == P.NO_USER_WORDS == "(nothing — the file came without a message)"


def test_user_words_once_stripped():
    msg = _msg(*TWO, user_words="\n  ### With the file\nأنا المدعى عليه  \n")
    assert "\n### With the file\nأنا المدعى عليه\n" in msg
    assert msg.count("### With the file") == 1 and P.NO_USER_WORDS not in msg


def test_one_block_per_document_in_d_order():
    msg = _msg(*reversed(TWO))  # given out of order: still D1 then D2
    d1 = (f"## Document D1 — صك حكم ابتدائي · judgment · 12 paragraphs · {len(F.content(F.JUDGMENT)):,} "
          "characters")
    d2 = (f"## Document D2 — لائحة اعتراض على الحكم · claim · 7 paragraphs · {len(F.content(F.OBJECTION)):,} "
          "characters")
    assert msg.index(d1) < msg.index(d2)
    order = [USER_SAID, d1, "### D1 — its summary", "### D1 — documents inside this file", "### D1 — sections",
             "### D1 — key paragraphs", "### D1 — the document (numbered paragraphs)", d2, "### D2 — its summary",
             "### D2 — documents inside this file", "### D2 — sections", "### D2 — key paragraphs",
             "### D2 — the document (numbered paragraphs)"]
    positions = [msg.index(h) for h in order]
    assert positions == sorted(positions)


def test_no_unread_files_no_block():
    msg = _msg(*TWO)
    assert P.UNREAD_HEADING not in msg and P.UNREAD_NOTE not in msg
    assert P.build_user_message(documents=list(TWO), unread=[]) == msg


def test_unread_files_are_listed_by_title_after_the_users_words():
    msg = P.build_user_message(documents=list(TWO), user_words="أنا المدعى عليه",
                               unread=["عقد إيجار محل", "", "  كشف حساب  "])
    lines = msg.split("\n")
    i = lines.index(P.UNREAD_HEADING)
    assert lines[i - 2:i] == [USER_SAID, "أنا المدعى عليه"]
    assert lines[i + 1:i + 5] == [
        "These files were sent with the message but could not be read; never say their content is missing.",
        "- عقد إيجار محل", "- (untitled)", "- كشف حساب"]
    assert P.UNREAD_NOTE == lines[i + 1]
    assert lines[i + 5] == "" and lines[i + 6].startswith("## Document D1 — ")
    assert lines[1].startswith("- 2 documents · ")  # the count line stays where it was
    assert msg.count(P.UNREAD_HEADING) == 1


def test_paragraph_numbers_restart_in_every_document():
    msg = _msg(*TWO)
    lines = msg.split("\n")
    assert lines.count(f"[P001] {F.JUDGMENT.lines[0]}") == 1
    assert lines.count(f"[P001] {F.OBJECTION.lines[0]}") == 1
    assert f"[P012] {F.JUDGMENT.lines[-1]}" in lines and f"[P007] {F.OBJECTION.lines[-1]}" in lines
    assert lines[-1] == f"[P007] {F.OBJECTION.lines[-1]}"
    assert "[P013]" not in msg  # never one numbering across documents


def test_summary_is_labelled_analysis_per_document():
    msg = _msg(*TWO)
    heading = ("### D2 — its summary (written by another model — analysis, NOT the document's words; use it to "
               "understand the document, never as the source of a fact)")
    assert f"{heading}\n{F.OBJECTION.summary}\n### D2 — documents inside this file" in msg


@pytest.mark.parametrize("summary", ["", "  "])
def test_no_summary_no_heading(summary):
    msg = _msg(pdoc(F.JUDGMENT, 1, summary=summary), TWO[1])
    assert "### D1 — its summary" not in msg and "### D2 — its summary" in msg


def test_section_lines_carry_document_refs_owner_and_brought_by():
    lines = _msg(*TWO).split("\n")
    assert "### D1 — sections (ref | paragraphs | label | value | owner key: owner (قدّمه side) | what)" in lines
    assert "- D1·S2 | P003–P005 | claim | core | claimant: المدعي | دعوى القرض وطلباتها" in lines
    assert ("- D1·S4 | P009–P009 | expert_report | supporting | expert: الخبير المحاسبي (قدّمه: المدعى عليه) | "
            "مطابقة التحويلات") in lines
    assert "- D2·S4 | P007–P007 | annex | supporting | third_party: البنك (قدّمه: المعترض) | كشف الحساب" in lines


def test_documents_inside_and_key_paragraphs_per_document():
    lines = _msg(*TWO).split("\n")
    i = lines.index("### D1 — documents inside this file")
    assert lines[i + 1] == "- P001–P012: صك حكم ابتدائي"
    k = lines.index("### D2 — key paragraphs")
    assert lines[k + 1] == "- P002: السبب الأول"
    assert lines[k + 2] == "" and lines[k + 3] == "### D2 — the document (numbered paragraphs)"


def test_the_runners_section_dicts_and_stored_segments_give_the_same_bytes():
    rows = F.segment_rows(F.JUDGMENT)
    with_refs = [{**r, "ref": F.ref(1, r["seq"]), "doc": 1, "item_id": F.ITEM_A} for r in rows]
    stored = [StoredSegment(**r) for r in rows]
    expected = _msg(pdoc(F.JUDGMENT, 1))
    assert _msg(pdoc(F.JUDGMENT, 1, sections=with_refs)) == expected
    assert _msg(pdoc(F.JUDGMENT, 1, sections=stored, paragraphs=F.paragraphs(F.JUDGMENT))) == expected


def test_given_paragraphs_are_used_as_they_are():
    msg = _msg(pdoc(F.JUDGMENT, 1, paragraphs=F.paragraphs(F.JUDGMENT)[:3]))
    assert "· 3 paragraphs ·" in msg and "[P004]" not in msg


def test_untitled_and_untyped_documents():
    msg = _msg(pdoc(F.JUDGMENT, 1, title="", doc_type=""))
    assert "## Document D1 — (untitled) · unknown · " in msg


@pytest.mark.parametrize("seg, doc, line", [
    ({"seq": 7, "from_p": 1, "to_p": 120, "label": "x", "value": "core", "owner_class": None, "owner": None,
      "brought_by": None, "what": None}, 2,
     "- D2·S7 | P001–P120 | x | core | unknown: - | "),
    ({"seq": 8, "from_p": 3, "to_p": 1004, "label": "x", "value": "repeat", "owner_class": "court", "owner": "",
      "brought_by": "", "what": "w"}, 1,
     "- D1·S8 | P003–P1004 | x | repeat | court: - | w"),
    ({"seq": 9, "from_p": "4", "to_p": "5", "label": "x", "value": "core", "owner_class": "contract_party",
      "owner": "الطرف الأول", "brought_by": "النيابة العامة", "what": "w", "ref": "D3.S9"}, None,
     "- D3·S9 | P004–P005 | x | core | contract_party: الطرف الأول (قدّمه: النيابة العامة) | w"),
    ({"seq": 2, "from_p": 1, "to_p": 1, "label": "x", "value": "core", "what": "w"}, None,
     "- S2 | P001–P001 | x | core | unknown: - | w"),
])
def test_section_line_edges(seg, doc, line):
    assert P.section_line(seg, doc) == line


# ── follow-ups inside one run ────────────────────────────────────────────────


def test_bad_refs_message():
    refs = sorted([F.ref(1, s) for s in F.JUDGMENT.seqs] + [F.ref(2, s) for s in F.OBJECTION.seqs])
    text = P.bad_refs_message(["angles[2].sections: D1.S41, S7", "context: D3.S1"], refs)
    assert text.startswith(
        "These refs are not sections (or documents) of this message: angles[2].sections: D1.S41, S7; context: D3.S1."
    )
    assert "(D1·S1–D1·S6, D2·S1–D2·S4)" in text
    assert "(`D1·S7` = document 1, section 7), and only the documents D1–D2." in text
    assert text.endswith("Return the corrected full map.")
    assert "only the documents D1–D1." in P.bad_refs_message(["context: S1"], [])


def test_no_sections_message():
    text = P.no_sections_message([1, 3])
    assert text.startswith("These angles name no section of this message: angles[1], angles[3].")
    assert "at least one ref of the section lists (`D1·S7` = document 1, section 7), or remove the angle." in text
    assert text.endswith("Return the corrected full map.")


def test_multi_question_message():
    text = P.multi_question_message([2, 5])
    assert text.startswith("These angles' `legal_question` asks more than one question: angles[2], angles[5].")
    assert "ONE question per angle: split or drop the second question" in text
    assert "chained with «و» / «،» / «أم»" in text and text.endswith("Return the corrected full map.")


def test_gaps_have_no_retry_message():
    # equal-value contradictions and OCR gaps are fixed in code, never sent back
    assert not hasattr(P, "equal_contradiction_message")


def test_repair_message_names_refs_as_the_prompt_shows_them():
    text = P.repair_message(["D1.S5", "D2.S3"])
    assert text.startswith(
        "Coverage check: these core sections are in no angle's `sections`, not in `context` and not in "
        "`no_angle`: D1·S5, D2·S3."
    )
    assert ("Return the corrected FULL map (client, every angle, context, gaps, no_angle and unrelated_documents), "
            "not only the changes.") in text
