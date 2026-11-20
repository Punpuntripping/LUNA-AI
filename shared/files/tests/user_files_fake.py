"""In-memory PostgREST stand-in with ``user_files`` and the 222 triggers.

No live database. Supports the query shapes the Phase-2 readers and writers use:
``select`` with plain columns, ``alias:col``, ``col->>key`` / ``col->key`` paths
and embeds (``conversations!artifacts_conversation_id_fkey(...)`` from an item,
``workspace_items!workspace_items_file_owner_fkey(...)`` from a file, nested),
``eq / neq / gt / lt / in_ / is_ / not_.is_``, ``order``, ``range``, ``limit``,
``maybe_single``, ``update``, and the RPCs ``user_file_patch`` /
``user_files_hide``.

``user_files`` generated columns (``content_chars``, ``word_count``,
``filename``, ``mime_type``, ``file_size_bytes``, ``doc_type``, ``ocr_status``,
``upload_status``) are computed on read. With ``triggers=True`` the fake runs
the 222 sync both ways: an item update reaches its file (mirror, only while
``file_id == item_id``), a file patch reaches every item of the file; new text
clears the summary (030 / 221).

``schema_ready=False`` answers like a database before migration 222: any read
of ``user_files`` or of ``workspace_items.file_id`` fails.

Every select, filter value, update statement and RPC is recorded so a test can
prove what was (and was not) asked. All text here is invented.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

ITEMS = "workspace_items"
FILES = "user_files"
CONVERSATIONS = "conversations"

#: (columns the file→item sync copies, migration 222 trigger 3)
_SYNC_TO_ITEMS = (
    "title", "content_md", "summary", "summary_source_length", "summary_updated_at",
    "metadata", "storage_path", "document_id",
)
#: (columns the item→file mirror copies, migration 222 trigger 2)
_MIRROR_TO_FILE = _SYNC_TO_ITEMS + ("deleted_at",)
_SUMMARY_TRIO = ("summary", "summary_source_length", "summary_updated_at")
#: The keys ``user_file_patch`` accepts in ``p_fields`` (migration 221: others raise 22023).
PATCH_FIELDS = frozenset({
    "title", "content_md", "summary", "summary_source_length", "summary_updated_at",
    "storage_path", "deleted_at",
})


class Result:
    def __init__(self, data: Any, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count


def split_columns(cols: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in cols:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _path(row: dict, expr: str) -> Any:
    """``col``, ``col->>key`` (text) or ``col->key`` (json) of ``row``."""
    if "->>" in expr:
        base, key = expr.split("->>", 1)
        container = row.get(base)
        if not isinstance(container, dict) or container.get(key) is None:
            return None
        value = container[key]
        return value if isinstance(value, str) else str(value)
    if "->" in expr:
        base, key = expr.split("->", 1)
        container = row.get(base)
        if not isinstance(container, dict):
            return None
        return copy.deepcopy(container.get(key))
    return row.get(expr)


def derived_file(f: dict) -> dict:
    """A ``user_files`` row with its generated columns computed."""
    meta = f.get("metadata") if isinstance(f.get("metadata"), dict) else {}
    content = f.get("content_md") or ""
    raw_size = meta.get("file_size_bytes")
    size = int(str(raw_size)) if raw_size is not None and re.fullmatch(r"[0-9]{1,18}", str(raw_size)) else None

    def text(key: str) -> Optional[str]:
        value = meta.get(key)
        return None if value is None else (value if isinstance(value, str) else str(value))

    return {
        **f,
        "content_chars": len(content),
        "word_count": len(content.split()),
        "filename": text("filename"),
        "mime_type": text("mime_type"),
        "file_size_bytes": size,
        "doc_type": text("doc_type"),
        "ocr_status": text("ocr_status"),
        "upload_status": text("upload_status"),
    }


class _Not:
    def __init__(self, q: "Query") -> None:
        self.q = q

    def is_(self, col: str, val: Any) -> "Query":
        self.q.filters.append(("not_is", col, None))
        return self.q


class Query:
    def __init__(self, sb: "FakeSupabase", name: str) -> None:
        self.sb = sb
        self.name = name
        self.cols: Optional[str] = None
        self.filters: list[tuple[str, str, Any]] = []
        self.orders: list[tuple[str, bool]] = []
        self.lo = 0
        self.hi: Optional[int] = None
        self.payload: Optional[dict] = None
        self.single = False

    # building -------------------------------------------------------------
    def select(self, cols: str = "*", count: Optional[str] = None) -> "Query":
        self.cols = cols
        self.sb.selects.append((self.name, cols))
        return self

    def update(self, payload: dict) -> "Query":
        self.payload = copy.deepcopy(payload)
        return self

    def eq(self, col: str, val: Any) -> "Query":
        self.filters.append(("eq", col, val))
        self.sb.filter_values.append(str(val))
        return self

    def neq(self, col: str, val: Any) -> "Query":
        self.filters.append(("neq", col, val))
        self.sb.filter_values.append(str(val))
        return self

    def gt(self, col: str, val: Any) -> "Query":
        self.filters.append(("gt", col, val))
        self.sb.filter_values.append(str(val))
        return self

    def lt(self, col: str, val: Any) -> "Query":
        self.filters.append(("lt", col, val))
        self.sb.filter_values.append(str(val))
        return self

    def in_(self, col: str, vals: Any) -> "Query":
        vals = list(vals)
        self.filters.append(("in", col, vals))
        self.sb.filter_values.extend(str(v) for v in vals)
        return self

    def is_(self, col: str, val: Any) -> "Query":
        assert val in ("null", None), val
        self.filters.append(("is", col, None))
        return self

    @property
    def not_(self) -> _Not:
        return _Not(self)

    def order(self, col: str, desc: bool = False) -> "Query":
        self.orders.append((col, desc))
        return self

    def range(self, lo: int, hi: int) -> "Query":
        self.lo, self.hi = lo, hi
        return self

    def limit(self, n: int) -> "Query":
        self.hi = self.lo + n - 1
        return self

    def maybe_single(self) -> "Query":
        self.single = True
        return self

    # running --------------------------------------------------------------
    def _view(self, row: dict) -> dict:
        return derived_file(row) if self.name == FILES else row

    def _match(self, row: dict) -> bool:
        view = self._view(row)
        for op, col, val in self.filters:
            actual = _path(view, col)
            if op == "eq" and (actual is None or str(actual) != str(val)):
                return False
            if op == "neq" and (actual is None or str(actual) == str(val)):   # SQL NULL semantics
                return False
            if op == "is" and actual is not None:
                return False
            if op == "not_is" and actual is None:
                return False
            if op == "in" and (actual is None or str(actual) not in {str(v) for v in val}):
                return False
            if op in ("gt", "lt"):
                if actual is None:
                    return False
                try:
                    a, b = float(actual), float(val)
                except (TypeError, ValueError):
                    a, b = str(actual), str(val)
                if (op == "gt" and not a > b) or (op == "lt" and not a < b):
                    return False
        return True

    def _check_schema(self) -> None:
        if self.sb.schema_ready:
            return
        if self.name == FILES:
            raise RuntimeError("PGRST205: relation user_files not found")
        if self.cols and "file_id" in split_columns(self.cols):
            raise RuntimeError("42703: column workspace_items.file_id does not exist")

    def execute(self) -> Any:
        if self.name in self.sb.fail_tables:
            raise RuntimeError(f"{self.name} unavailable")
        self._check_schema()
        rows = [r for r in self.sb.tables.setdefault(self.name, []) if self._match(r)]
        if self.payload is not None:
            return self._run_update(rows)
        for col, desc in reversed(self.orders):
            rows.sort(key=lambda r: str(self._view(r).get(col) or ""), reverse=desc)
        total = len(rows)
        hi = len(rows) - 1 if self.hi is None else self.hi
        rows = rows[self.lo: hi + 1]
        data = [self.sb.project(self.name, self._view(r), self.cols) for r in rows]
        if self.single:
            return Result(data[0] if data else None)
        return Result(data, total)

    def _run_update(self, rows: list[dict]) -> Result:
        assert self.payload is not None
        self.sb.statements.append(("update", self.name, copy.deepcopy(self.payload), list(self.filters)))
        out = []
        for r in rows:
            rid = r.get("item_id") or r.get("file_id")
            if rid in self.sb.fail_update_ids:
                raise RuntimeError("update failed")
            before = copy.deepcopy(r)
            r.update(copy.deepcopy(self.payload))
            if self.name == ITEMS and "content_md" in self.payload and before.get("content_md") != r.get("content_md"):
                for k in _SUMMARY_TRIO:   # 030
                    r[k] = None
            self.sb.updates.append((self.name, rid, copy.deepcopy(self.payload)))
            if self.name == ITEMS:
                self.sb._mirror_item_to_file(r)
            out.append(copy.deepcopy(r))
        return Result(out)


class _Rpc:
    def __init__(self, sb: "FakeSupabase", name: str, params: dict) -> None:
        self.sb, self.name, self.params = sb, name, params

    def execute(self) -> Result:
        self.sb.rpcs.append((self.name, copy.deepcopy(self.params)))
        if self.name in self.sb.fail_rpcs:
            raise RuntimeError(f"rpc {self.name} failed")
        if not self.sb.schema_ready:
            raise RuntimeError(f"PGRST202: function {self.name} not found")
        handler = getattr(self.sb, f"_rpc_{self.name}")
        return Result(handler(**self.params))


class FakeSupabase:
    def __init__(
        self,
        *,
        schema_ready: bool = True,
        triggers: bool = True,
        backfill: bool = False,
        **tables: list[dict],
    ) -> None:
        self.tables = {k: [copy.deepcopy(r) for r in v] for k, v in tables.items()}
        for name in (ITEMS, FILES, CONVERSATIONS, "attachment_segment_maps", "attachment_segments"):
            self.tables.setdefault(name, [])
        self.schema_ready = schema_ready
        self.triggers = triggers
        self.selects: list[tuple[str, str]] = []
        self.filter_values: list[str] = []      # every filter value — they travel in URLs
        self.updates: list[tuple[str, Any, dict]] = []
        self.statements: list[tuple] = []       # ("update", table, payload, filters)
        self.rpcs: list[tuple[str, dict]] = []
        self.fail_tables: set[str] = set()
        self.fail_update_ids: set[str] = set()
        self.fail_rpcs: set[str] = set()
        if backfill:
            self.backfill()

    # client surface ---------------------------------------------------------
    def table(self, name: str) -> Query:
        return Query(self, name)

    def rpc(self, name: str, params: dict) -> _Rpc:
        return _Rpc(self, name, params)

    # helpers ------------------------------------------------------------------
    def row(self, item_id: str) -> dict:
        return next(r for r in self.tables[ITEMS] if r["item_id"] == item_id)

    def file(self, file_id: str) -> dict:
        return next(f for f in self.tables[FILES] if f["file_id"] == file_id)

    def backfill(self) -> None:
        """Migration 222 Tx B + link: every attachment gets a file under its own id."""
        have = {f["file_id"] for f in self.tables[FILES]}
        for wi in self.tables[ITEMS]:
            if wi.get("kind") != "attachment":
                continue
            if wi.get("file_id") is None:
                wi["file_id"] = wi["item_id"]
            if wi["file_id"] in have:
                continue
            meta = wi.get("metadata") if isinstance(wi.get("metadata"), dict) else {}
            self.tables[FILES].append({
                "file_id": wi["file_id"],
                "user_id": wi.get("user_id"),
                "title": wi.get("title"),
                "content_md": wi.get("content_md") or "",
                "summary": wi.get("summary"),
                "summary_source_length": wi.get("summary_source_length"),
                "summary_updated_at": wi.get("summary_updated_at"),
                "metadata": copy.deepcopy(meta),
                "storage_path": wi.get("storage_path"),
                "document_id": wi.get("document_id"),
                "origin": "chat",
                "library_hidden_at": meta.get("library_hidden_at"),
                "created_at": wi.get("created_at"),
                "updated_at": wi.get("updated_at"),
                "deleted_at": wi.get("deleted_at"),
            })
            have.add(wi["file_id"])

    def project(self, table: str, row: dict, cols: Optional[str]) -> dict:
        if cols is None or cols.strip() == "*":
            return copy.deepcopy(row)
        out: dict = {}
        for col in split_columns(cols):
            m = re.fullmatch(r"(\w+)(?:!(\w+))?\((.*)\)", col, re.S)
            if m:
                target, hint, inner = m.groups()
                out[target] = self._embed(table, row, target, hint, inner)
                continue
            alias = None
            m = re.fullmatch(r"(\w+):(.+)", col)
            if m:
                alias, col = m.group(1), m.group(2).strip()
            if col == "*":
                out.update(copy.deepcopy(row))
                continue
            name = alias or re.split(r"->>|->", col)[-1]
            out[name] = copy.deepcopy(_path(row, col))
        return out

    def _embed(self, table: str, row: dict, target: str, hint: Optional[str], inner: str) -> Any:
        if table == ITEMS and target == CONVERSATIONS:
            assert hint in (None, "artifacts_conversation_id_fkey"), hint
            conv = next(
                (c for c in self.tables[CONVERSATIONS] if c.get("conversation_id") == row.get("conversation_id")),
                None,
            )
            return None if conv is None else self.project(CONVERSATIONS, conv, inner)
        if table == FILES and target == ITEMS:
            assert hint == "workspace_items_file_owner_fkey", hint
            return [
                self.project(ITEMS, wi, inner)
                for wi in self.tables[ITEMS]
                if wi.get("file_id") == row.get("file_id") and wi.get("user_id") == row.get("user_id")
            ]
        raise AssertionError(f"unsupported embed {table} -> {target}!{hint}")

    # the 222 triggers ---------------------------------------------------------
    def _mirror_item_to_file(self, wi: dict) -> None:
        if not self.triggers or wi.get("kind") != "attachment":
            return
        if not wi.get("file_id") or wi.get("file_id") != wi.get("item_id"):
            return
        f = next((f for f in self.tables[FILES] if f["file_id"] == wi["file_id"]), None)
        if f is None:
            return
        for k in _MIRROR_TO_FILE:
            value = wi.get(k)
            if k == "content_md":
                value = value or ""
            if k == "metadata":
                value = copy.deepcopy(value) if isinstance(value, dict) else {}
            f[k] = value
        stamp = (wi.get("metadata") or {}).get("library_hidden_at") if isinstance(wi.get("metadata"), dict) else None
        if f.get("library_hidden_at") is None and stamp:
            f["library_hidden_at"] = stamp

    def _sync_file_to_items(self, f: dict) -> None:
        if not self.triggers:
            return
        for wi in self.tables[ITEMS]:
            if wi.get("file_id") != f["file_id"] or wi.get("kind") != "attachment":
                continue
            changed = {k: copy.deepcopy(f.get(k)) for k in _SYNC_TO_ITEMS if wi.get(k) != f.get(k)}
            if changed:
                wi.update(changed)
                wi["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.updates.append((ITEMS, wi["item_id"], {"_sync": sorted(changed)}))

    # the RPCs -----------------------------------------------------------------
    def _rpc_user_file_patch(
        self, p_file_id: str, p_fields: dict, p_metadata_patch: dict, p_user_id: Optional[str] = None,
    ) -> Optional[str]:
        if p_file_id in self.fail_update_ids:
            raise RuntimeError("patch failed")
        if not isinstance(p_fields, dict) or not isinstance(p_metadata_patch, dict):
            raise RuntimeError("22023: p_fields and p_metadata_patch must be JSON objects")
        bad = sorted(set(p_fields) - PATCH_FIELDS)
        if bad:
            raise RuntimeError(f"22023: user_file_patch: unknown field(s): {', '.join(bad)}")
        f = next(
            (f for f in self.tables[FILES]
             if f["file_id"] == p_file_id and (p_user_id is None or f.get("user_id") == p_user_id)),
            None,
        )
        if f is None:
            return None
        before = f.get("content_md")
        for k, v in (p_fields or {}).items():
            if k == "content_md":
                v = "" if v is None else v
            elif k == "title" and v is None:
                v = f.get("title")              # a JSON null title keeps the title (221)
            f[k] = v
        f["metadata"] = {**(f.get("metadata") or {}), **copy.deepcopy(p_metadata_patch or {})}
        if f.get("content_md") != before:
            for k in _SUMMARY_TRIO:   # 221 invalidate
                f[k] = None
        f["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._sync_file_to_items(f)
        return f["file_id"]

    def _rpc_user_files_hide(self, p_user_id: str, p_file_ids: list[str]) -> int:
        if any(i in self.fail_update_ids for i in p_file_ids):
            raise RuntimeError("hide failed")
        now = datetime.now(timezone.utc).isoformat()
        n = 0
        for f in self.tables[FILES]:
            if (f.get("user_id") == p_user_id and f["file_id"] in p_file_ids
                    and f.get("library_hidden_at") is None and f.get("deleted_at") is None):
                f["library_hidden_at"] = now
                f["metadata"] = {**(f.get("metadata") or {}), "library_hidden_at": now}
                n += 1
                self._sync_file_to_items(f)
        return n


def resp(data: Any) -> SimpleNamespace:
    return SimpleNamespace(data=data)


__all__ = ["FakeSupabase", "Query", "Result", "derived_file", "split_columns"]
