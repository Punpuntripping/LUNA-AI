"""«النصوص المثبّتة» — one card per conversation (plan completion_search.md §4.5).

The fake table below behaves like the real one where it matters for this module:
one live ``pinned_texts`` row per conversation (migration 230), ``summary`` wiped
whenever ``content_md`` changes (the 030 trigger), ``updated_at`` stamped on every
write. Synthetic text only.
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from shared.workspace import pinned_texts as pt

USER, CONV = "user-1", "conv-1"


class _Query:
    def __init__(self, db: "CardDb") -> None:
        self.db, self.op, self.payload = db, "select", None
        self.filters: list[Callable[[dict], bool]] = []

    def select(self, *_a: Any) -> "_Query":
        return self

    def insert(self, row: dict) -> "_Query":
        self.op, self.payload = "insert", row
        return self

    def update(self, values: dict) -> "_Query":
        self.op, self.payload = "update", values
        return self

    def eq(self, key: str, value: Any) -> "_Query":
        self.filters.append(lambda r: str(r.get(key)) == str(value))
        return self

    def is_(self, key: str, _value: Any) -> "_Query":
        self.filters.append(lambda r: r.get(key) is None)
        return self

    def limit(self, _n: int) -> "_Query":
        return self

    def execute(self) -> SimpleNamespace:
        db = self.db
        db.ops.append(self.op)
        if self.op == "select":
            hook, db.after_select = db.after_select, None
            rows = [dict(r) for r in db.rows if all(f(r) for f in self.filters)]
            if hook:
                hook()                                  # a second writer acts right after our read
            return SimpleNamespace(data=rows[:1])
        if self.op == "insert":
            row = dict(self.payload)
            clash = [r for r in db.rows if r["conversation_id"] == row["conversation_id"]
                     and r["kind"] == row["kind"] == pt.KIND and r["deleted_at"] is None]
            if clash:
                raise RuntimeError("duplicate key value violates unique constraint (230)")
            row.update(item_id=f"item-{next(db.ids)}", updated_at=f"t{next(db.clock)}", deleted_at=None)
            db.rows.append(row)
            return SimpleNamespace(data=[dict(row)])
        hit = [r for r in db.rows if all(f(r) for f in self.filters)]
        for r in hit:
            if "content_md" in self.payload and self.payload["content_md"] != r.get("content_md"):
                r.update(summary=None, summary_source_length=None, summary_updated_at=None)   # the 030 trigger
            r.update(self.payload)
            r["updated_at"] = f"t{next(db.clock)}"       # stamped by the table, whatever the caller sent
        return SimpleNamespace(data=[dict(r) for r in hit])


class CardDb:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.ops: list[str] = []
        self.ids = itertools.count(1)
        self.clock = itertools.count(1)
        self.after_select: Callable[[], None] | None = None

    def table(self, name: str) -> _Query:
        assert name == "workspace_items"
        return _Query(self)

    @property
    def card(self) -> dict:
        (row,) = [r for r in self.rows if r["kind"] == pt.KIND and r["deleted_at"] is None]
        return row


def pin(key: str, title: str, text: str, source_type: str = "article", confidence: str = "high", **ids):
    return SimpleNamespace(key=key, source_type=source_type, title=title, text=text, source_title="نظام العمل",
                           confidence=confidence, regulation_id=ids.get("regulation_id", ""),
                           article_id=ids.get("article_id", ""), article_number=ids.get("article_number", ""),
                           circular_id="", service_id="", case_id=ids.get("case_id", ""), principle_no="")


A77 = pin("article:a77", "المادة 77 من نظام العمل", "نص المادة السابعة والسبعين.", article_id="a77")
A80 = pin("article:a80", "المادة 80 من نظام العمل", "نص المادة الثمانين.", article_id="a80")


def add(db: CardDb, *pins, by: str = "deep_planner"):
    return pt.add_agent_pins(db, user_id=USER, conversation_id=CONV, pins=pins, by=by)


# --------------------------------------------------------------------------- #
# One card, written by code
# --------------------------------------------------------------------------- #


def test_the_first_pin_creates_the_card():
    db = CardDb()
    write = add(db, A77)
    assert (write.created, write.changed, write.total) == (True, True, 1)
    card = db.card
    assert (card["kind"], card["created_by"], card["title"]) == ("pinned_texts", "agent", "النصوص المثبّتة")
    assert card["user_id"] == USER and card["conversation_id"] == CONV and card["is_visible"] is True
    assert card["content_md"] == (
        "> 📌 نصوص رسمية ثبّتها ريحان في هذه المحادثة — للقراءة فقط.\n\n---\n\n"
        "## المادة 77 من نظام العمل\n\nنص المادة السابعة والسبعين.\n\n> أُضيف أثناء البحث"
    )
    assert card["metadata"]["subtype"] == "pinned_texts"
    assert card["metadata"]["entries"][0]["ids"] == {"article_id": "a77"}


def test_the_card_is_born_with_its_summary_so_no_model_summarises_it():
    db = CardDb()
    add(db, A77)
    assert db.card["summary"] == "نصوص رسمية مثبّتة في هذه المحادثة (للقراءة فقط): المادة 77 من نظام العمل"
    assert db.card["summary_source_length"] == len(db.card["content_md"])


def test_a_later_pin_goes_onto_the_same_card():
    db = CardDb()
    first = add(db, A77)
    second = add(db, A80)
    assert second.item_id == first.item_id and (second.created, second.total) == (False, 2)
    assert len([r for r in db.rows if r["kind"] == pt.KIND]) == 1
    assert db.card["content_md"].index("المادة 77") < db.card["content_md"].index("المادة 80")


def test_the_summary_is_set_again_after_every_merge():
    # The table wipes the summary when the text changes; a card without one would be
    # handed to the LLM summariser.
    db = CardDb()
    add(db, A77)
    add(db, A80)
    assert db.card["summary"].endswith("المادة 77 من نظام العمل؛ المادة 80 من نظام العمل")
    assert db.card["summary_source_length"] == len(db.card["content_md"])


def test_the_same_text_twice_is_one_entry_and_no_write():
    db = CardDb()
    add(db, A77)
    before = list(db.ops)
    again = add(db, A77)
    assert (again.changed, again.total) == (False, 1)
    assert db.ops[len(before):] == ["select"]             # read, nothing to write
    assert db.card["content_md"].count("## المادة 77") == 1


def test_each_place_that_cited_a_text_gets_its_own_line():
    db = CardDb()
    add(db, A77)
    add(db, A77, by="writer_planner")
    entry = db.card["metadata"]["entries"][0]
    assert [c["by"] for c in entry["cited_in"]] == ["deep_planner", "writer_planner"]
    assert "> أُضيف أثناء البحث\n> أُضيف أثناء إعداد الصياغة" in db.card["content_md"]


def test_confidence_only_goes_up():
    db = CardDb()
    add(db, pin("article:a1", "المادة 1", "نص", confidence="medium"))
    add(db, pin("article:a1", "المادة 1", "نص", confidence="high"), by="writer_planner")
    assert db.card["metadata"]["entries"][0]["confidence"] == "high"
    add(db, pin("article:a1", "المادة 1", "نص", confidence="medium"), by="attachment")
    assert db.card["metadata"]["entries"][0]["confidence"] == "high"


def test_the_wording_follows_the_library():
    db = CardDb()
    add(db, A77)
    add(db, pin("article:a77", A77.title, "نص المادة بعد تعديل النظام.", article_id="a77"))
    assert "بعد تعديل النظام" in db.card["content_md"] and "السابعة والسبعين" not in db.card["content_md"]


def test_a_card_the_user_removed_is_replaced_by_a_new_one():
    db = CardDb()
    first = add(db, A77)
    db.card["deleted_at"] = "2026-11-23"
    second = add(db, A80)
    assert second.created and second.item_id != first.item_id and second.total == 1


def test_nothing_to_write():
    db = CardDb()
    assert pt.add_agent_pins(db, user_id=USER, conversation_id=CONV, pins=[], by="deep_planner") is None
    assert pt.upsert_card(db, user_id=USER, conversation_id="", entries=[pt.entry_from_pin(A77, by="x")]) is None
    assert db.ops == []


# --------------------------------------------------------------------------- #
# Two writers at once
# --------------------------------------------------------------------------- #


def test_a_writer_that_lost_the_race_reads_again_and_merges():
    db = CardDb()
    add(db, A77)

    def other_writer():
        db.after_select = None
        add(db, A80, by="writer_planner")                 # lands between our read and our write

    db.after_select = other_writer
    late = pin("article:a81", "المادة 81 من نظام العمل", "نص المادة 81.", article_id="a81")
    write = add(db, late)
    assert write.total == 3
    titles = [e["title"] for e in db.card["metadata"]["entries"]]
    assert titles == ["المادة 77 من نظام العمل", "المادة 80 من نظام العمل", "المادة 81 من نظام العمل"]
    assert db.card["summary"].count("المادة") == 3         # the summary is the newest merge's


def test_two_writers_that_both_find_no_card_end_with_one_card():
    db = CardDb()

    def other_writer():
        db.after_select = None
        add(db, A80, by="writer_planner")

    db.after_select = other_writer
    write = add(db, A77)
    assert not write.created and write.total == 2
    assert len([r for r in db.rows if r["kind"] == pt.KIND]) == 1


def test_endless_contention_raises_instead_of_looping():
    db = CardDb()
    add(db, A77)
    counter = itertools.count(100)

    def always():
        db.card["updated_at"] = f"x{next(counter)}"
        db.after_select = always

    db.after_select = always
    with pytest.raises(RuntimeError):
        add(db, A80)


# --------------------------------------------------------------------------- #
# What the card shows
# --------------------------------------------------------------------------- #


def test_articles_and_principles_show_their_full_text():
    long = "كلمة " * 2_000
    assert pt.body_for("article", long) == long.strip()
    assert pt.body_for("principle", long) == long.strip()


@pytest.mark.parametrize("source_type", ["case", "circular", "service"])
def test_rulings_circulars_and_services_show_a_lead_only(source_type):
    body = pt.body_for(source_type, "سطر من النص الكامل.\n" * 400)
    assert len(body) < 700 and body.endswith("(مقتطف — النص الكامل في المكتبة)")
    assert pt.body_for(source_type, "نص قصير.") == "نص قصير."


def test_a_ruling_on_the_card_carries_no_full_text():
    db = CardDb()
    ruling = pin("case:c1", "الحكم في القضية رقم 4455 — التجارية", "وقائع الحكم.\n" * 500, source_type="case",
                 case_id="c1")
    add(db, ruling)
    assert len(db.card["content_md"]) < 900
    assert len(db.card["metadata"]["entries"][0]["body_md"]) < 700


def test_the_summary_lists_titles_never_texts():
    summary = pt.card_summary([pt.entry_from_pin(A77, by="deep_planner"), pt.entry_from_pin(A80, by="deep_planner")])
    assert "المادة 77 من نظام العمل؛ المادة 80 من نظام العمل" in summary
    assert "نص المادة" not in summary
    many = [pt.make_entry(key=f"k{i}", source_type="article", title=f"المادة {i} من نظام طويل الاسم جداً", text="نص")
            for i in range(200)]
    assert len(pt.card_summary(many)) <= 1_501


# --------------------------------------------------------------------------- #
# Attachments
# --------------------------------------------------------------------------- #

CONTENT = "تمهيد. استناداً إلى المادة (77) من نظام العمل يطالب المدعي بالتعويض. ثم إن المادة 77 تكررت هنا. خاتمة."


def att_pin(start: int, sentence: tuple[int, int], article_id: str = "a77", number: str = "77"):
    return SimpleNamespace(char_start=start, sentence_start=sentence[0], sentence_end=sentence[1],
                           article_id=article_id, regulation_id="reg-labor", article_number=number,
                           regulation_title="نظام العمل", title=f"المادة {number} من نظام العمل",
                           text=f"نص المادة {number} الرسمي.")


def test_an_attachment_entry_quotes_the_citing_sentence():
    first = (CONTENT.index("استناداً"), CONTENT.index("ثم إن"))
    second = (CONTENT.index("ثم إن"), CONTENT.index("خاتمة"))
    entries = pt.attachment_entries(
        attachment_id="att-1", attachment_title="صحيفة الدعوى", content=CONTENT,
        pins=[att_pin(CONTENT.index("المادة 77 تكررت"), second), att_pin(CONTENT.index("المادة (77)"), first)],
    )
    (entry,) = entries                                       # the article once …
    assert entry["key"] == "article:a77" and entry["confidence"] == "high"
    labels = [c["label"] for c in entry["cited_in"]]         # … with a line per place, in document order
    assert labels[0].startswith("ورد في المرفق «صحيفة الدعوى»: «استناداً إلى المادة (77) من نظام العمل")
    assert "تكررت هنا" in labels[1]
    assert all(c["item_id"] == "att-1" and c["by"] == "attachment" for c in entry["cited_in"])


def test_the_same_attachment_twice_adds_nothing():
    db = CardDb()
    sentence = (CONTENT.index("استناداً"), CONTENT.index("ثم إن"))
    kwargs = dict(user_id=USER, conversation_id=CONV, attachment_id="att-1", attachment_title="صحيفة الدعوى",
                  content=CONTENT, pins=[att_pin(CONTENT.index("المادة (77)"), sentence)])
    assert pt.add_attachment_pins(db, **kwargs) == 1
    writes = len(db.ops)
    assert pt.add_attachment_pins(db, **kwargs) == 1
    assert db.ops[writes:] == ["select"]


def test_an_agent_and_an_attachment_share_one_entry_for_the_same_article():
    db = CardDb()
    add(db, A77)
    sentence = (CONTENT.index("استناداً"), CONTENT.index("ثم إن"))
    pt.add_attachment_pins(db, user_id=USER, conversation_id=CONV, attachment_id="att-1", attachment_title="",
                           content=CONTENT, pins=[att_pin(CONTENT.index("المادة (77)"), sentence)])
    (entry,) = db.card["metadata"]["entries"]
    assert [c["by"] for c in entry["cited_in"]] == ["deep_planner", "attachment"]
    assert entry["cited_in"][1]["label"].startswith("ورد في مرفق: «")


def test_the_event_has_the_shape_the_chat_relays():
    db = CardDb()
    write = add(db, A77)
    assert write.event() == {
        "type": "workspace_item_created", "item_id": write.item_id, "kind": "pinned_texts",
        "subtype": "pinned_texts", "title": "النصوص المثبّتة", "created_by": "agent",
    }
    assert add(db, A80).event() == {"type": "workspace_item_updated", "item_id": write.item_id}
    assert add(db, A80).event() == {"type": "referenced_existing_item", "item_id": write.item_id}
