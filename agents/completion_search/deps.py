"""Run state of one Completion Search request: handles, rounds, what is already settled.

The three-round limit and the "a satisfied search is not repeated" rule live HERE,
in code — the prompt alone is not trusted for them (plan §4.2, owner decision C5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.completion_search.lookups import LawLookup, Library
from agents.completion_search.models import MAX_ROUNDS, MAX_SEARCHES, Pin
from agents.tool_repository.citation_scan import fold

CLOSED_AR = (
    "اكتمل البحث {sid} بنتيجة مؤكدة. لا تُعد المحاولة له؛ وللبحث عن شيء آخر استخدم رقم بحث جديدًا."
)
EXHAUSTED_AR = (
    "استُنفدت المحاولات الثلاث للبحث {sid}. اعتمد أفضل ما وُجد له، أو أعلن أنه غير موجود."
)
TOO_MANY_AR = "بلغ الطلب الحد الأقصى من عمليات البحث ({n}). لا تبدأ بحثًا جديدًا."
FINISHED_AR = "انتهى البحث {sid} بلا نتيجة، ولا فائدة من إعادة المحاولة. أعلن حالته كما هي."


@dataclass
class Handle:
    """Something a tool found. The model refers to it by ``id`` and never sees more than a line."""

    id: str                     # "T1" a text · "L1" a law
    search_id: str
    kind: str                   # "law" | "article" | "circular" | "service" | "case" | "principle"
    title: str
    pin: Pin | None = None      # the opened text — text handles only
    law: LawLookup | None = None


def _squash(text: str) -> str:
    return " ".join(fold(text).replace("«", " ").replace("»", " ").replace('"', " ").split())


@dataclass
class CompletionDeps:
    library: Library
    request: str
    caller: str = "deep_planner"
    #: What the CALLER itself was given — the user's messages and documents. When set,
    #: "as asked" is judged against it and not against the request: a planner that
    #: sends «نظام المرافعات الشرعية» where the user wrote «نظام المرافعات» has made a
    #: guess, and a guess is never high confidence.
    source_text: str | None = None
    # identity for the tracking span (agents/utils/tracking.py reads these names)
    conversation_id: Any = None
    case_id: Any = None
    turn_number: int | None = None
    user_id: Any = None

    handles: dict[str, Handle] = field(default_factory=dict)
    rounds: dict[str, int] = field(default_factory=dict)       # search id → attempts used
    closed: set[str] = field(default_factory=set)              # searches settled at high confidence
    hints: dict[str, list[str]] = field(default_factory=dict)  # search id → titles last listed
    hint_level: dict[str, str] = field(default_factory=dict)   # search id → confidence of such a list
    missing: set[str] = field(default_factory=set)             # searches whose law is not in the library
    finished: set[str] = field(default_factory=set)            # searches code has settled as "nothing to find"
    _text_n: int = 0
    _law_n: int = 0
    _request_squashed: str | None = None

    @staticmethod
    def sid(raw: str) -> str:
        return (raw or "").strip().upper()[:8] or "S?"

    def begin(self, search_id: str) -> str | None:
        """Start one attempt at a search. Returns the refusal line, or ``None`` to go on."""
        sid = self.sid(search_id)
        if sid in self.closed:
            return CLOSED_AR.format(sid=sid)
        if sid in self.finished:
            return FINISHED_AR.format(sid=sid)
        if sid not in self.rounds and len(self.rounds) >= MAX_SEARCHES:
            return TOO_MANY_AR.format(n=MAX_SEARCHES)
        if self.rounds.get(sid, 0) >= MAX_ROUNDS:
            return EXHAUSTED_AR.format(sid=sid)
        self.rounds[sid] = self.rounds.get(sid, 0) + 1
        return None

    def finish(self, search_id: str) -> None:
        """Nothing more can be found for this search — a further attempt would repeat the same lookup."""
        self.finished.add(self.sid(search_id))

    def last_round(self, search_id: str) -> bool:
        return self.rounds.get(self.sid(search_id), 0) >= MAX_ROUNDS

    def as_asked(self, words: str) -> bool:
        """True when ``words`` are written in the request itself — not the model's rewording."""
        if self._request_squashed is None:
            self._request_squashed = _squash(self.source_text if self.source_text else self.request)
        needle = _squash(words)
        return bool(needle) and needle in self._request_squashed

    def add_law(self, search_id: str, title: str, lookup: LawLookup) -> str:
        self._law_n += 1
        handle = Handle(f"L{self._law_n}", self.sid(search_id), "law", title, law=lookup)
        self.handles[handle.id] = handle
        return handle.id

    def add_pin(self, search_id: str, pin: Pin) -> str:
        sid = self.sid(search_id)
        for handle in self.handles.values():   # the same text found twice is one handle
            if handle.pin is not None and handle.pin.key == pin.key and handle.search_id == sid:
                return handle.id
        self._text_n += 1
        handle = Handle(f"T{self._text_n}", sid, pin.source_type, pin.title, pin=pin)
        self.handles[handle.id] = handle
        if pin.confidence == "high":
            self.closed.add(sid)
        return handle.id

    def get(self, handle_id: str) -> Handle | None:
        return self.handles.get((handle_id or "").strip().upper())


def written_in(words: str, text: str) -> bool:
    """True when ``words`` occur in ``text`` — tashkeel, letter shapes and quotes ignored."""
    needle = _squash(words)
    return bool(needle) and needle in _squash(text)


__all__ = ["CLOSED_AR", "CompletionDeps", "EXHAUSTED_AR", "FINISHED_AR", "Handle", "TOO_MANY_AR", "written_in"]
