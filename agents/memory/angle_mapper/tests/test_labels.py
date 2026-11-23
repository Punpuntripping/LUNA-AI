"""Side labels (D14 + v3 contract parties) — الموكّل / الخصم / محايد from the
segmenter's owners, never from the model — and the v4 additions: refs → section
dicts across the documents of a message, the client's key per document."""
from __future__ import annotations

import pytest

from agents.memory.angle_mapper import labels as L
from agents.memory.attachment_segmenter.models import OWNER_CLASSES, StoredMap, StoredSegment

from . import fixtures as F


def sec(owner_class=None, owner=None, brought_by=None) -> dict:
    return {"owner_class": owner_class, "owner": owner, "brought_by": brought_by}


def test_label_words():
    assert L.LABEL_AR == {"own": "الموكّل", "opp": "الخصم", "neutral": "محايد"}


def test_party_keys_are_segmenter_owner_keys():
    assert L.PARTY_SIDES == L.LITIGATION_SIDES | {"contract_party"}
    assert L.PARTY_SIDES <= set(OWNER_CLASSES)
    assert "both_parties" not in L.PARTY_SIDES


# ── section_side ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("oc", sorted(["claimant", "respondent", "prosecution", "victim", "intervenor",
                                       "contract_party"]))
def test_a_party_owner_key_is_the_side(oc):
    # even when brought_by names another side: the owner wins
    assert L.section_side(sec(oc, "x", "المدعي")) == oc
    assert L.section_side(sec(oc.upper())) == oc


@pytest.mark.parametrize("brought_by, side", [
    ("النيابة العامة", "prosecution"),
    ("المدعي العام", "prosecution"),          # «العام» is checked before «المدعي»
    ("هيئة التحقيق والادعاء العام", "prosecution"),
    ("المدعى عليه", "respondent"),
    ("المدعى عليهما", "respondent"),
    ("المستأنف ضده", "respondent"),
    ("المعترض ضده", "respondent"),            # «ضد» before «المعترض»
    ("المدعي", "claimant"),
    ("المستأنف", "claimant"),
    ("المعترض", "claimant"),
    ("الخبير", None),
    ("", None),
    (None, None),
])
def test_brought_by_keywords(brought_by, side):
    assert L.brought_by_side(brought_by) == side
    expected = side or L.NEUTRAL
    for oc in ("court", "expert", "both_parties", "unknown", None):
        assert L.section_side(sec(oc, "x", brought_by)) == expected


def test_no_owner_no_brought_by_is_neutral():
    assert L.section_side(sec()) == "neutral"
    assert L.section_side({}) == "neutral"


def test_brought_by_the_client_contract_party():
    s = sec("third_party", "البنك الضامن", "المقاول")
    assert L.section_side(s, "المقاول") == "contract_party"
    assert L.section_side(s, "المالك") == "neutral"
    assert L.section_side(s) == "neutral"


def test_stored_segments_are_read_too():
    s = StoredSegment(seq=1, from_p=1, to_p=1, char_start=0, char_end=1, label="x", value="core",
                      owner_class="expert", owner="الخبير", brought_by="المدعى عليه")
    assert L.section_side(s) == "respondent"
    assert L.relative_label(s, "respondent") == "own"


# ── relative_label: litigation ───────────────────────────────────────────────

LITIGATION = [
    # me, section owner_class, label
    ("claimant", "claimant", "own"),
    ("claimant", "respondent", "opp"),
    ("claimant", "prosecution", "neutral"),
    ("claimant", "victim", "neutral"),
    ("claimant", "intervenor", "neutral"),
    ("claimant", "court", "neutral"),
    ("claimant", "contract_party", "neutral"),
    ("respondent", "respondent", "own"),
    ("respondent", "claimant", "opp"),
    ("respondent", "prosecution", "opp"),
    ("respondent", "victim", "opp"),
    ("respondent", "intervenor", "neutral"),
    ("respondent", "expert", "neutral"),
    ("respondent", "both_parties", "neutral"),
    ("prosecution", "prosecution", "own"),
    ("prosecution", "respondent", "opp"),
    ("prosecution", "victim", "neutral"),
    ("prosecution", "claimant", "neutral"),
    ("victim", "victim", "own"),
    ("victim", "respondent", "opp"),
    ("victim", "prosecution", "neutral"),
    ("intervenor", "intervenor", "own"),
    ("intervenor", "claimant", "neutral"),
    ("intervenor", "respondent", "neutral"),
]


@pytest.mark.parametrize("me, oc, label", LITIGATION)
def test_litigation_labels(me, oc, label):
    assert L.relative_label(sec(oc, "x"), me) == label
    assert L.relative_label(sec(oc, "x"), me.upper() + " ") == label  # normalised


@pytest.mark.parametrize("me, brought_by, label", [
    ("respondent", "المدعى عليه", "own"),    # an expert report the respondent relies on
    ("respondent", "النيابة العامة", "opp"),
    ("respondent", "المدعي", "opp"),
    ("claimant", "المدعى عليه", "opp"),
    ("claimant", "المدعي", "own"),
    ("prosecution", "النيابة العامة", "own"),
    ("victim", "المدعى عليه", "opp"),
    ("claimant", "الخبير", "neutral"),
])
def test_brought_by_decides_a_non_party_section(me, brought_by, label):
    assert L.relative_label(sec("expert", "الخبير المحاسبي", brought_by), me) == label


@pytest.mark.parametrize("me", ["unknown", "", None, "both_parties", "court", "mixed"])
def test_an_unknown_client_labels_nothing(me):
    for oc in ("claimant", "respondent", "prosecution", "victim", "contract_party", "court"):
        assert L.relative_label(sec(oc, "x", "المدعي"), me) == "neutral"


# ── relative_label: contract parties (v3) ────────────────────────────────────


@pytest.mark.parametrize("owner, label", [
    ("المقاول", "own"),
    ("المالك", "opp"),
    ("الطرف الأول (المقاول)", "own"),        # the client's words inside the owner's
    ("الطرف الثاني", "opp"),
    ("المُقاوِل", "own"),                     # diacritics
    ("المقـــاول", "own"),                   # tatweel
    ("مقاول", "own"),                        # without the article
    ("", "neutral"),                         # no owner words: which party it is cannot be told
    (None, "neutral"),
])
def test_contract_party_sections_by_owner_words(owner, label):
    assert L.relative_label(sec("contract_party", owner), "contract_party", "المقاول") == label


def test_contract_client_words_contain_the_owner_words():
    # containment works either way: «الطرف الأول» inside the client's «الطرف الأول (المالك)»
    assert L.relative_label(sec("contract_party", "الطرف الأول"), "contract_party", "الطرف الأول (المالك)") == "own"
    assert L.relative_label(sec("contract_party", "الطرف الثاني"), "contract_party", "الطرف الأول (المالك)") == "opp"


def test_alef_forms_are_one_letter():
    assert L.relative_label(sec("contract_party", "الطرف الاول"), "contract_party", "الطرف الأول") == "own"
    assert L.relative_label(sec("contract_party", "إدارة المشروع"), "contract_party", "ادارة المشروع") == "own"


@pytest.mark.parametrize("oc, brought_by, label", [
    ("both_parties", None, "neutral"),
    ("both_parties", "المقاول", "neutral"),   # a section both parties signed stays neutral
    ("court", None, "neutral"),
    ("third_party", "المقاول", "own"),        # a guarantee the client's party relies on
    ("third_party", "المالك", "neutral"),
    ("expert", None, "neutral"),
])
def test_contract_non_party_sections(oc, brought_by, label):
    assert L.relative_label(sec(oc, "x", brought_by), "contract_party", "المقاول") == label


@pytest.mark.parametrize("party", ["", None, "  ", "()"])
def test_contract_client_without_words_labels_nothing(party):
    for owner in ("المقاول", "المالك"):
        assert L.relative_label(sec("contract_party", owner), "contract_party", party) == "neutral"


def test_normalize_party():
    assert L.normalize_party("الطرف الأول (المالك)") == "طرفاولمالك"
    assert L.normalize_party("المُقاوِل") == "مقاول"
    assert L.normalize_party("المقـاول") == "مقاول"
    assert L.normalize_party("آل") == "ال"  # a short word keeps its letters
    assert L.normalize_party(None) == "" and L.normalize_party("") == ""


@pytest.mark.parametrize("a, b, match", [
    ("المقاول", "مقاول", True),
    ("الطرف الأول", "الطرف الأول (المالك)", True),
    ("الطرف الأول (المالك)", "الطرف الأول", True),
    ("المالك", "المقاول", False),
    ("", "المقاول", False),
    ("المقاول", "", False),
    (None, None, False),
    ("()", "()", False),
])
def test_party_words_match(a, b, match):
    assert L.party_words_match(a, b) is match


# ── angle_viewpoint ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("angle, client, expected", [
    ({"for_side": "respondent", "for_party": "المدعى عليه"}, {"side": "claimant", "party": "المدعي"},
     ("respondent", "المدعى عليه")),
    ({"for_side": "unknown", "for_party": "x"}, {"side": "claimant", "party": "المدعي"}, ("claimant", "المدعي")),
    ({"for_side": "", "for_party": ""}, {"side": "contract_party", "party": "المقاول"},
     ("contract_party", "المقاول")),
    ({"for_side": "contract_party", "for_party": "المالك"}, {"side": "unknown", "party": ""},
     ("contract_party", "المالك")),
    ({"for_side": "unknown", "for_party": "x"}, {"side": "unknown", "party": "y"}, (None, "")),
    ({"for_side": "both_parties", "for_party": "x"}, None, (None, "")),
    ({}, {}, (None, "")),
])
def test_angle_viewpoint(angle, client, expected):
    assert L.angle_viewpoint(angle, client) == expected


# ── v4: one map per message ──────────────────────────────────────────────────

CLIENT_TWO = F.CLIENT_TWO  # respondent in D1, claimant «المعترض» in D2


def _stored(doc: F.Doc) -> StoredMap:
    row = F.seg_map_row(doc)
    return StoredMap(**{k: row[k] for k in ("item_id", "user_id", "paragraph_rule_version", "prompt_version",
                                             "content_chars", "created_at")},
                     segments=[StoredSegment(**s) for s in F.segment_rows(doc)])


ITEMS = [{"doc_index": 1, "item_id": F.ITEM_A}, {"doc_index": 2, "item_id": F.ITEM_B}]


def test_section_index_keys_every_section_by_its_ref():
    index = L.section_index(ITEMS, {F.ITEM_A: _stored(F.JUDGMENT), F.ITEM_B: _stored(F.OBJECTION)})
    assert sorted(index) == sorted([F.ref(1, s) for s in F.JUDGMENT.seqs] + [F.ref(2, s) for s in F.OBJECTION.seqs])
    s = index["D2.S4"]
    assert (s["ref"], s["doc"], s["item_id"], s["seq"]) == ("D2.S4", 2, F.ITEM_B, 4)
    assert (s["owner_class"], s["owner"], s["brought_by"], s["value"]) == ("third_party", "البنك", "المعترض",
                                                                            "supporting")
    assert s["content_md"] == F.segment_rows(F.OBJECTION)[3]["content_md"]


def test_section_index_takes_plain_dicts_and_skips_what_it_cannot_place():
    maps = {F.ITEM_A: {"segments": F.segment_rows(F.JUDGMENT)}}  # D2 has no segment map now
    items = [*ITEMS, {"doc_index": "x", "item_id": F.ITEM_C}, {"doc_index": 3, "item_id": None}]
    index = L.section_index(items, maps)
    assert sorted(index) == sorted(F.ref(1, s) for s in F.JUDGMENT.seqs)
    assert L.section_index(ITEMS, {F.ITEM_A: {"segments": [{"seq": None}, {"seq": "2", "value": "core"}]}}) == {
        "D1.S2": {"seq": "2", "value": "core", "ref": "D1.S2", "doc": 1, "item_id": F.ITEM_A}}


def test_resolve_ref_in_any_spelling():
    index = L.section_index(ITEMS, {F.ITEM_A: _stored(F.JUDGMENT), F.ITEM_B: _stored(F.OBJECTION)})
    for spelling in ("D2.S2", "D2·S2", "d2-s02", "[D2S2]"):
        assert L.resolve_ref(index, spelling)["what"] == "أسباب الاعتراض"
    assert L.resolve_ref(index, "S2") is None and L.resolve_ref(index, "D3.S1") is None
    assert L.document_of("D2·S7") == "D2" and L.document_of("S7") is None


@pytest.mark.parametrize("client, document, expected", [
    (CLIENT_TWO, "D2", ("claimant", "المعترض")),
    (CLIENT_TWO, 2, ("claimant", "المعترض")),
    (CLIENT_TWO, "d2", ("claimant", "المعترض")),
    (CLIENT_TWO, "D1", None),
    ({"side": "respondent", "by_document": [{"document": "D2", "side": "unknown", "party": "x"}]}, "D2", None),
    ({"side": "respondent", "by_document": [{"document": "D2", "side": "court", "party": "x"}]}, "D2", None),
    ({"side": "respondent"}, "D2", None),
    (None, "D2", None),
])
def test_client_in_document(client, document, expected):
    assert L.client_in_document(client, document) == expected


SERVES_CLIENT = {"for_side": "respondent", "for_party": "المدعى عليه"}


@pytest.mark.parametrize("angle, ref, expected", [
    # an angle that serves the client takes the client's key in the section's document
    (SERVES_CLIENT, "D1.S3", ("respondent", "المدعى عليه")),
    (SERVES_CLIENT, "D2.S2", ("claimant", "المعترض")),
    (SERVES_CLIENT, "d2·s2", ("claimant", "المعترض")),
    (SERVES_CLIENT, None, ("respondent", "المدعى عليه")),
    (SERVES_CLIENT, "S2", ("respondent", "المدعى عليه")),   # a bare ref names no document
    ({"for_side": "unknown", "for_party": ""}, "D2.S2", ("claimant", "المعترض")),
    ({}, "D2.S3", ("claimant", "المعترض")),
    # an angle for ANOTHER side keeps its own side everywhere
    ({"for_side": "claimant", "for_party": "المدعي"}, "D1.S2", ("claimant", "المدعي")),
    ({"for_side": "claimant", "for_party": "المدعي"}, "D2.S2", ("claimant", "المدعي")),
])
def test_angle_viewpoint_per_document(angle, ref, expected):
    assert L.angle_viewpoint(angle, CLIENT_TWO, ref) == expected


def test_angle_viewpoint_without_a_client_side_ignores_by_document():
    client = {"side": "unknown", "party": "", "by_document": [{"document": "D2", "side": "claimant", "party": "x"}]}
    assert L.angle_viewpoint({"for_side": "unknown"}, client, "D2.S2") == (None, "")
    assert L.angle_viewpoint({"for_side": "respondent", "for_party": "y"}, client, "D2.S2") == ("respondent", "y")


@pytest.mark.parametrize("ref, label", [
    ("D1.S2", "opp"),      # the claimant's claim in the judgment
    ("D1.S3", "own"),      # the client's defence in the judgment
    ("D1.S4", "own"),      # the expert report the respondent relies on
    ("D1.S5", "neutral"),  # the court
    ("D2.S2", "own"),      # the client's grounds — claimant in ITS proceeding
    ("D2.S3", "own"),
    ("D2.S4", "own"),      # the bank statement the objector files
])
def test_labels_across_documents_follow_the_clients_key_in_each(ref, label):
    index = L.section_index(ITEMS, {F.ITEM_A: _stored(F.JUDGMENT), F.ITEM_B: _stored(F.OBJECTION)})
    me, party = L.angle_viewpoint(SERVES_CLIENT, CLIENT_TWO, ref)
    assert L.relative_label(L.resolve_ref(index, ref), me, party) == label


def test_without_by_document_the_objection_would_read_as_the_opponents():
    """Why ``by_document`` exists: with the judgment's key only, the client's own
    objection (owned by the claimant of ITS proceeding) labels as the opponent's."""
    index = L.section_index(ITEMS, {F.ITEM_A: _stored(F.JUDGMENT), F.ITEM_B: _stored(F.OBJECTION)})
    me, party = L.angle_viewpoint(SERVES_CLIENT, F.CLIENT, "D2.S2")
    assert (me, L.relative_label(L.resolve_ref(index, "D2.S2"), me, party)) == ("respondent", "opp")
