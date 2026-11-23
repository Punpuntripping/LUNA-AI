"""In-memory stand-in for the sync supabase-py client — the query surface the angle
mapper, its store, its group resolution, the segmenter's loaders it reuses, the
helper-run store and the masking store use. Tables are lists of dict rows.

- ``fail``: table names (or ``(table, op)`` pairs) whose ``execute()`` raises;
- primary keys of the 215 / 228 tables are enforced on insert (a duplicate raises);
- UNIQUE columns of 228 (``attachment_angle_maps.message_id`` — NULLs distinct,
  ``attachment_angle_map_items.item_id``) are enforced on insert AND update;
- foreign keys of 228 (items / angles → their map) are enforced on insert;
- deletes cascade as migration 215 / 228 say: a segment map → its segments; an
  angle map → its items and angles; a workspace item → its angle-map member row;
- ``log``: every executed ``(table, op)`` in order; ``selects``: every executed
  select as ``(table, columns)`` (``"*"`` when none named).
"""
from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
from typing import Any, Callable

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "attachment_pages": ("item_id", "page_no"),
    "attachment_segment_maps": ("item_id",),
    "attachment_segments": ("item_id", "seq"),
    "attachment_angle_maps": ("map_id",),
    "attachment_angle_map_items": ("map_id", "doc_index"),
    "attachment_angles": ("map_id", "seq"),
}
#: UNIQUE constraints besides the primary key (a NULL never conflicts).
UNIQUES: dict[str, list[tuple[str, ...]]] = {
    "attachment_angle_maps": [("message_id",)],
    "attachment_angle_map_items": [("item_id",)],
}
#: child table → [(column, parent table, parent column)], checked on insert.
FOREIGN_KEYS: dict[str, list[tuple[str, str, str]]] = {
    "attachment_angle_map_items": [("map_id", "attachment_angle_maps", "map_id")],
    "attachment_angles": [("map_id", "attachment_angle_maps", "map_id")],
}
#: parent table → [(child table, child column, parent column)] — ON DELETE CASCADE.
CASCADES: dict[str, list[tuple[str, str, str]]] = {
    "attachment_segment_maps": [("attachment_segments", "item_id", "item_id")],
    "attachment_angle_maps": [
        ("attachment_angle_map_items", "map_id", "map_id"),
        ("attachment_angles", "map_id", "map_id"),
    ],
    "workspace_items": [("attachment_angle_map_items", "item_id", "item_id")],
}


class FakeSupabase:
    def __init__(self, tables: dict[str, list[dict]] | None = None, fail: set | None = None) -> None:
        self.tables: dict[str, list[dict]] = defaultdict(list)
        for k, v in (tables or {}).items():
            self.tables[k] = [dict(r) for r in v]
        self.fail: set = set(fail or ())
        self.log: list[tuple[str, str]] = []
        self.selects: list[tuple[str, str]] = []

    def table(self, name: str) -> "_Query":
        return _Query(self, name)

    def ops(self, *tables: str) -> list[tuple[str, str]]:
        """The executed ``(table, op)`` pairs on ``tables`` (all when none given)."""
        return [x for x in self.log if not tables or x[0] in tables]

    def writes(self) -> list[tuple[str, str]]:
        """Every executed non-select ``(table, op)``."""
        return [x for x in self.log if x[1] != "select"]


class _Query:
    def __init__(self, db: FakeSupabase, name: str) -> None:
        self.db, self.name = db, name
        self.op = "select"
        self.columns = "*"
        self.payload: Any = None
        self.on_conflict: str | None = None
        self.filters: list[Callable[[dict], bool]] = []
        self.orders: list[tuple[str, bool]] = []
        self.rng: tuple[int, int] | None = None
        self.lim: int | None = None

    # builders
    def select(self, *a: Any, **_k: Any) -> "_Query":
        self.op = "select"
        self.columns = str(a[0]) if a else "*"
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

    def gte(self, k: str, v: Any) -> "_Query":
        self.filters.append(lambda r: r.get(k) is not None and str(r.get(k)) >= str(v))
        return self

    def lte(self, k: str, v: Any) -> "_Query":
        self.filters.append(lambda r: r.get(k) is not None and str(r.get(k)) <= str(v))
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

    @staticmethod
    def _key(row: dict, cols: tuple[str, ...]) -> tuple:
        return tuple(str(row.get(c)) for c in cols)

    def _check_unique(self, row: dict, others: list[dict]) -> None:
        for cols in UNIQUES.get(self.name, []):
            if any(row.get(c) is None for c in cols):
                continue
            if any(self._key(o, cols) == self._key(row, cols) for o in others):
                raise RuntimeError(f"duplicate key in {self.name} UNIQUE {cols}: {self._key(row, cols)}")

    def _check_foreign_keys(self, row: dict) -> None:
        for col, parent, pcol in FOREIGN_KEYS.get(self.name, []):
            if row.get(col) is None:
                continue
            if not any(str(p.get(pcol)) == str(row.get(col)) for p in self.db.tables[parent]):
                raise RuntimeError(f"foreign key violation {self.name}.{col} → {parent}")

    def _cascade(self, table: str, hit: list[dict]) -> None:
        for child, col, parent_col in CASCADES.get(table, []):
            gone = {str(r.get(parent_col)) for r in hit}
            removed = [r for r in self.db.tables[child] if str(r.get(col)) in gone]
            if removed:
                self.db.tables[child] = [r for r in self.db.tables[child] if str(r.get(col)) not in gone]
                self._cascade(child, removed)

    def execute(self) -> SimpleNamespace:
        self.db.log.append((self.name, self.op))
        if self.op == "select":
            self.db.selects.append((self.name, self.columns))
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
            staged = [dict(r) for r in table]
            for row in rows:  # all or nothing, like one INSERT statement
                row = dict(row)
                if conflict:
                    existing = [r for r in staged if self._key(r, conflict) == self._key(row, conflict)]
                    if existing and self.op == "insert":
                        raise RuntimeError(f"duplicate key in {self.name}: {self._key(row, conflict)}")
                    if existing:
                        existing[0].update(row)
                        continue
                self._check_unique(row, staged)
                self._check_foreign_keys(row)
                staged.append(row)
            self.db.tables[self.name] = staged
            return SimpleNamespace(data=[dict(r) for r in rows])
        if self.op == "update":
            hit = self._match()
            hit_ids = {id(r) for r in hit}
            others = [r for r in table if id(r) not in hit_ids]
            for r in hit:
                self._check_unique({**r, **self.payload}, others)
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in hit])
        if self.op == "delete":
            hit = self._match()
            hit_ids = {id(r) for r in hit}
            self.db.tables[self.name] = [r for r in table if id(r) not in hit_ids]
            self._cascade(self.name, hit)
            return SimpleNamespace(data=hit)
        raise AssertionError(self.op)
