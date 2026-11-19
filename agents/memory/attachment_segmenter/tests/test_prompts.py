"""Prompt v4.4 — byte-identity with the Phase 0 tested text, split mode, messages, schema."""
from __future__ import annotations

import hashlib
import json

import pytest

from agents.memory.attachment_segmenter import prompts as P
from agents.memory.attachment_segmenter.models import SegmentMap
from agents.memory.attachment_segmenter.paragraphs import paragraphize

#: sha256 of ``build_instructions(t)`` for seg-v5.2 (owner fields, 2026-11-18) —
#: re-pinned after the 5-document preview. A wording change must be deliberate.
TESTED_INSTRUCTIONS_SHA256 = {
    "claim": "72dda3dbe7c0e41755e32c849153110f686f5f06b3a1a8a9bf70354e858e6c52",
    "contract": "113ce835b5d2539191a7b0308b22ff366850c0be78cbd1b06496b0e24ce286ae",
    "expert_report": "30b70b4ec657b55d1ce7b0290b9412ce0bcb1ef16518a86bcb5f4ed0bf3f81ce",
    "financial": "1a41230cf4e7fb1fc9a719e7906243f45b8a3888c8e097b35879f18d49387c2b",
    "generic": "b8f499699f9104cee72b5cc74524025ded50f6246f4cfe1f1b9ccbec6d8d7398",
    "hearing_minutes": "0552209bfb03b8d97a8094f59bb0fa9dfa05d75ba01447371699c91a9c0a996d",
    "judgment": "5304cda3c2b95257f8d3a81629289c18d6462f8e184cba47707cf3876acfe3b4",
    "letter": "3c120115ac1bd94fdec4d888c67cd936ed192fd44afd4da8d64ac121c5ad6ea8",
    "memo": "f6ea59f4fd421f204986e4314d1b17e4d4f362a5a1847dd9b5c71db2206bbc98",
    "official_record": "0a8402a64c96b83ef08b389592a99906a32485385a37bd4f6f9ecac96bddb75e",
    "reference": "93bb8059d9c76494c065357605a70b406008372d8aa00aec097bb545dd875fc3",
    "": "c7c197dcc68ff70e3255235c89330a5360324271205d1de1b4767d607595a4c0",
    "chat": "b1b76910afd9678159ef02fa94740da15d2f1dc7fc3cc24032f836afb656c50a",
    "statement": "66e30ac800a78143c109a4ed7b03c097802c219daaca46721ebc0d8e3ec7ff44",
    "user_notes": "d5ab52b487c15a86a69fcca6999badb67fe7fa38d417376163856116ac49a344",
    "other": "6794e69be5034e6a2af4752e31aa4bfb4bead28c70d0d302e75b77c86ba4dc8a",
}
#: sha256 of ``SegmentMap.model_json_schema()`` (json, sort_keys) for seg-v5.2.
TESTED_SCHEMA_SHA256 = "86a3ff349cbdb0963abd9578651203ab37d975ba95e368ea9cd31b6da5ff17d8"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_prompt_version():
    assert P.PROMPT_VERSION == "seg-v5.2"


@pytest.mark.parametrize("doc_type", sorted(TESTED_INSTRUCTIONS_SHA256))
def test_whole_document_instructions_are_the_pinned_bytes(doc_type):
    """v5.0 pins (owner principle, 2026-11-12). A wording change must be deliberate:
    re-run scripts/segmentation_preview.py, bump PROMPT_VERSION, then re-pin."""
    assert _sha(P.build_instructions(doc_type)) == TESTED_INSTRUCTIONS_SHA256[doc_type]


def test_none_doc_type_is_unknown_generic():
    assert P.build_instructions(None) == P.build_instructions("")


def test_every_label_list_has_its_type_notes():
    assert set(P.LABELS) == set(P.TYPE_NOTES)
    assert set(P.LABELS) == {
        "judgment", "hearing_minutes", "expert_report", "contract", "memo", "claim",
        "reference", "letter", "financial", "official_record", "generic",
    }
    for t in P.LABELS:
        text = P.build_instructions(t)
        assert P.LABELS[t] in text, t
        assert P.TYPE_NOTES[t] in text, t
        assert f"## Labels for this document type ({t})" in text
        assert "{labels}" not in text and "{type_notes}" not in text and "{doc_type}" not in text


def test_label_relations():
    assert P.LABELS["claim"] == P.LABELS["memo"]
    assert P.LABELS["hearing_minutes"].startswith(P.LABELS["judgment"])
    assert "`attendance`" in P.LABELS["hearing_minutes"] and "`attendance`" not in P.LABELS["judgment"]
    for label in ("`claim_requests`", "`defense_pleas`", "`answers`"):
        assert label in P.LABELS["judgment"]
    assert P.TYPE_NOTES["judgment"] == P.TYPE_NOTES["hearing_minutes"]
    assert "the speaker rule" in P.TYPE_NOTES["judgment"]
    # contract files carry the letter and financial rules for embedded documents
    assert P.TYPE_NOTES["contract"].endswith(P.TYPE_NOTES["financial"])
    assert P.TYPE_NOTES["letter"] in P.TYPE_NOTES["contract"]
    # the criminal-case rule of v4.3 is NOT in v4.4
    assert "complainant" not in P.build_instructions("hearing_minutes").lower()


@pytest.mark.parametrize("doc_type", ["chat", "statement", "user_notes", "other", "", None])
def test_unlisted_types_get_the_generic_list(doc_type):
    text = P.build_instructions(doc_type)
    assert P.type_key(doc_type) == "generic"
    assert P.GENERIC_LABELS in text and P.TYPE_NOTES["generic"] in text
    shown = doc_type or "unknown"
    assert f"## Labels for this document type ({shown})" in text


def test_output_schema_is_the_tested_one():
    schema = SegmentMap.model_json_schema()
    assert _sha(json.dumps(schema, sort_keys=True)) == TESTED_SCHEMA_SHA256
    seg = schema["$defs"]["Segment"]
    assert list(seg["properties"]) == [
        "from_p", "to_p", "label", "owner_class", "owner", "brought_by", "what", "value",
    ]  # owner after label, what BEFORE value
    # the owner fields have code defaults, yet the model must always fill them
    assert seg["required"] == list(seg["properties"])
    assert schema["required"] == ["documents_in_file", "segments", "key_paragraphs"]
    assert list(schema["$defs"]["EmbeddedDoc"]["properties"]) == ["from_p", "to_p", "name"]


# ── split mode ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc_type", sorted(P.LABELS))
def test_part_instructions_do_not_contradict_the_part(doc_type):
    whole = P.build_instructions(doc_type)
    for part, first_end in ((1, None), (2, 7)):
        text = P.build_instructions(doc_type, part=part, a=101 if part == 2 else 1,
                                    b=200 if part == 2 else 100, first_end=first_end)
        assert "covering EVERY paragraph of YOUR PART exactly once" in text
        assert "the first segment starts at the first paragraph of YOUR PART" in text
        assert "the last `to_p` = the last paragraph of YOUR PART" in text
        assert "the first segment starts at 1" not in text
        assert "paragraph count in the first line" not in text
        assert f"## You are mapping ONE PART of a longer document ({part} of 2)" in text
        # everything else is the tested text
        assert P.LABELS[doc_type] in text and P.TYPE_NOTES[doc_type] in text
        assert len(text) > len(whole)
    two = P.build_instructions(doc_type, part=2, a=101, b=200, first_end=7)
    assert "Map ONLY paragraphs 101–200" in two
    assert "first page (paragraphs 1–7)" in two and "is `boilerplate`, not `metadata`" in two
    one = P.build_instructions(doc_type, part=1, a=1, b=100)
    assert "Your part starts at the beginning of the document" in one


def test_split_anchor_must_exist():
    with pytest.raises(ValueError):
        P._replace_once("no anchor here", "covering EVERY paragraph from 1", "x")
    for old, _ in P._PART_COVERAGE:
        assert P.PROMPT.count(old) == 1


# ── messages ─────────────────────────────────────────────────────────────────

CONTENT = "رأس الحكم\nوحيث إن المدعي طلب.\nحكمت الدائرة برفض الدعوى."
PARAS = paragraphize(CONTENT, doc_type="judgment")


def test_whole_message_is_the_tested_layout():
    msg = P.build_user_message(PARAS, doc_type="judgment", title="حكم\nابتدائي", summary="ملخص الحكم", chars=len(CONTENT))
    assert msg == (
        f"# doc_type: judgment | title: حكم ابتدائي | {len(CONTENT)} chars | 3 paragraphs\n"
        "\n"
        "## Summary of the document (context only — never map it)\n"
        "ملخص الحكم\n"
        "\n"
        "## Paragraphs\n"
        "[P001] رأس الحكم\n"
        "[P002] وحيث إن المدعي طلب.\n"
        "[P003] حكمت الدائرة برفض الدعوى.\n"
    )


def test_missing_summary_and_type():
    msg = P.build_user_message(PARAS, doc_type=None, title="x", summary="  ", chars=10)
    assert msg.startswith("# doc_type: unknown | title: x | 10 chars | 3 paragraphs\n")
    assert f"\n{P.NO_SUMMARY}\n" in msg


def test_part_messages():
    paras = paragraphize("\n".join(f"سطر {i}" for i in range(1, 41)))
    one = P.build_part_message(paras, part=1, a=1, b=20, first_end=3, overlap=5,
                               doc_type="memo", title="مذكرة", summary="ملخص", chars=999)
    assert one.splitlines()[0] == "# doc_type: memo | title: مذكرة | 999 chars | 40 paragraphs | part 1 of 2 — you map P001–P020"
    assert P.SUMMARY_HEADING in one and "[P020] " in one and "[P025] " in one and "[P026] " not in one
    two = P.build_part_message(paras, part=2, a=21, b=40, first_end=3, overlap=5,
                               doc_type="memo", title="مذكرة", summary="ملخص", chars=999)
    assert "## First page of the document (context only — already mapped)\n[P001] " in two
    assert "## Context before your part (read only — do not map)\n[P016] " in two
    assert "[P004] " not in two and two.endswith("[P040] سطر 40\n")


# ---------------------------------------------------------------------------
# v5.0 — one principle for the value, the user's words as context
# ---------------------------------------------------------------------------


def test_labels_carry_no_value_arrows():
    """The value comes from the principle in PROMPT, never from a label line."""
    for t, text in P.LABELS.items():
        for value in ("core", "supporting", "metadata", "boilerplate"):
            assert f"→ `{value}`" not in text, (t, value)


def test_the_principle_is_stated_once_in_every_prompt():
    for t in P.LABELS:
        text = P.build_instructions(t)
        assert text.count("is it worth investigating and searching on for this case?") == 1
        assert "**When in doubt, `core`.**" in text
        assert "Never rate a passage `supporting` on a guess" in text
        assert "## What the user said" in text


def test_user_words_block_only_when_given():
    from agents.memory.attachment_segmenter.paragraphs import paragraphize

    paras = paragraphize("سطر أول\nسطر ثان", doc_type="memo")
    with_words = P.build_user_message(
        paras, doc_type="memo", title="مذكرة", summary="ملخص", chars=17,
        user_words="### With the file\nانظر الجدول في الصفحة الثالثة",
    )
    assert P.USER_WORDS_HEADING in with_words
    assert with_words.index(P.SUMMARY_HEADING) < with_words.index(P.USER_WORDS_HEADING) < with_words.index(P.PARAGRAPHS_HEADING)
    without = P.build_user_message(paras, doc_type="memo", title="مذكرة", summary="ملخص", chars=17)
    assert P.USER_WORDS_HEADING not in without
    blank = P.build_user_message(paras, doc_type="memo", title="مذكرة", summary="ملخص", chars=17, user_words="  ")
    assert P.USER_WORDS_HEADING not in blank


def test_part_messages_carry_the_user_words_too():
    from agents.memory.attachment_segmenter.paragraphs import paragraphize

    paras = paragraphize("\n".join(f"فقرة {i}" for i in range(1, 41)), doc_type="memo")
    for part, a, b in ((1, 1, 20), (2, 21, 40)):
        msg = P.build_part_message(
            paras, part=part, a=a, b=b, first_end=3, overlap=5, doc_type="memo",
            title="مذكرة", summary="ملخص", chars=400, user_words="### With the file\nسؤال",
        )
        assert P.USER_WORDS_HEADING in msg and "سؤال" in msg


# ---------------------------------------------------------------------------
# v5.2 — who owns each section (owner, 2026-11-18)
# ---------------------------------------------------------------------------


def test_every_owner_key_is_explained_once_in_every_prompt():
    from agents.memory.attachment_segmenter.models import OWNER_CLASSES

    for t in P.LABELS:
        text = P.build_instructions(t)
        assert "## Who owns each section" in text
        for key in OWNER_CLASSES:
            assert text.count(f"- `{key}` — ") == 1, (t, key)
        assert "Quoted or reported words keep their original speaker" in text
        assert "The owner never decides where a section starts or ends" in text
