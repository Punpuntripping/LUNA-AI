"""In-memory stand-in for the sync supabase-py client — just the query surface the
segmenter, its store and the OCR step use. Tables are lists of dict rows.

- ``fail``: table names (or ``(table, op)`` pairs) whose ``execute()`` raises;
- primary keys of the 215 tables are enforced on insert (a duplicate raises);
- deleting an ``attachment_segment_maps`` row cascades to its segments.
"""
from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
from typing import Any, Callable

PRIMARY_KEYS = {
    "attachment_pages": ("item_id", "page_no"),
    "attachment_segment_maps": ("item_id",),
    "attachment_segments": ("item_id", "seq"),
}
CASCADES = {"attachment_segment_maps": [("attachment_segments", "item_id")]}


class FakeSupabase:
    def __init__(self, tables: dict[str, list[dict]] | None = None, fail: set | None = None) -> None:
        self.tables: dict[str, list[dict]] = defaultdict(list)
        for k, v in (tables or {}).items():
            self.tables[k] = [dict(r) for r in v]
        self.fail: set = set(fail or ())
        self.log: list[tuple[str, str]] = []

    def table(self, name: str) -> "_Query":
        return _Query(self, name)


class _Query:
    def __init__(self, db: FakeSupabase, name: str) -> None:
        self.db, self.name = db, name
        self.op = "select"
        self.payload: Any = None
        self.on_conflict: str | None = None
        self.filters: list[Callable[[dict], bool]] = []
        self.orders: list[tuple[str, bool]] = []
        self.rng: tuple[int, int] | None = None
        self.lim: int | None = None

    # builders
    def select(self, *_a: Any, **_k: Any) -> "_Query":
        self.op = "select"
        return self

    def insert(self, rows: Any) -> "_Query":
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows: Any, on_conflict: str | None = None, **_k: Any) -> "_Query":
        self.op, self.payload, self.on_conflict = "upsert", rows, on_conflict
        return self

    def update(self, values: dict) -> "_Query":
        self.op, self.payload = "update", values
        return self

    def delete(self) -> "_Query":
        self.op = "delete"
        return self

    def eq(self, k: str, v: Any) -> "_Query":
        self.filters.append(lambda r: str(r.get(k)) == str(v))
        return self

    def neq(self, k: str, v: Any) -> "_Query":
        self.filters.append(lambda r: str(r.get(k)) != str(v))
        return self

    def lt(self, k: str, v: Any) -> "_Query":
        self.filters.append(lambda r: r.get(k) is not None and str(r.get(k)) < str(v))
        return self

    def in_(self, k: str, vals: Any) -> "_Query":
        s = {str(v) for v in vals}
        self.filters.append(lambda r: str(r.get(k)) in s)
        return self

    def is_(self, k: str, v: Any) -> "_Query":
        if str(v) == "null":
            self.filters.append(lambda r: r.get(k) is None)
        return self

    def order(self, k: str, desc: bool = False) -> "_Query":
        self.orders.append((k, desc))
        return self

    def range(self, a: int, b: int) -> "_Query":
        self.rng = (a, b)
        return self

    def limit(self, n: int) -> "_Query":
        self.lim = n
        return self

    # execution
    def _match(self) -> list[dict]:
        return [r for r in self.db.tables[self.name] if all(f(r) for f in self.filters)]

    def _key(self, row: dict, cols: tuple[str, ...]) -> tuple:
        return tuple(str(row.get(c)) for c in cols)

    def execute(self) -> SimpleNamespace:
        self.db.log.append((self.name, self.op))
        if self.name in self.db.fail or (self.name, self.op) in self.db.fail:
            raise RuntimeError(f"fake failure on {self.name}.{self.op}")
        table = self.db.tables[self.name]
        if self.op == "select":
            rows = [dict(r) for r in self._match()]
            for k, desc in reversed(self.orders):
                rows.sort(key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
            if self.rng is not None:
                rows = rows[self.rng[0]:self.rng[1] + 1]
            if self.lim is not None:
                rows = rows[: self.lim]
            return SimpleNamespace(data=rows)
        if self.op in ("insert", "upsert"):
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            pk = PRIMARY_KEYS.get(self.name)
            conflict = tuple(c.strip() for c in self.on_conflict.split(",")) if self.on_conflict else pk
            for row in rows:
                row = dict(row)
                if conflict:
                    existing = [r for r in table if self._key(r, conflict) == self._key(row, conflict)]
                    if existing and self.op == "insert":
                        raise RuntimeError(f"duplicate key in {self.name}: {self._key(row, conflict)}")
                    if existing:
                        existing[0].update(row)
                        continue
                table.append(row)
            return SimpleNamespace(data=rows)
        if self.op == "update":
            hit = self._match()
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in hit])
        if self.op == "delete":
            hit = self._match()
            hit_ids = {id(r) for r in hit}
            self.db.tables[self.name] = [r for r in table if id(r) not in hit_ids]
            for child, col in CASCADES.get(self.name, []):
                gone = {str(r.get(col)) for r in hit}
                self.db.tables[child] = [r for r in self.db.tables[child] if str(r.get(col)) not in gone]
            return SimpleNamespace(data=hit)
        raise AssertionError(self.op)
