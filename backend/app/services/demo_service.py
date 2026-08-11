"""The shared demo conversation — «محادثة تجريبية».

One conversation, owned by one account, that EVERY account is allowed to read.
It is the fixture the product tour (`.claude/plans/demo_conversation_product_tour.md`)
runs on: a real chat, a real workspace item, real references.

Why this module can be four constants and two predicates
--------------------------------------------------------
The backend runs on ``SUPABASE_SERVICE_KEY`` (``shared/db/client.py``) and
therefore bypasses RLS entirely; ownership is enforced in Python, by the
``.eq("user_id", …)`` filters scattered through the service layer. Sharing one
conversation with every account is consequently a pure service-layer allowance:
no RLS migration, no schema change, no new column.

The allowance is **read-only, and deliberately narrow**:

* It is applied at named READ call sites only — never inside a helper that a
  POST/PATCH/DELETE handler uses as its ownership gate. Every write path keeps
  its ``.eq("user_id", …)`` filter and keeps refusing non-owners for free.
* It is keyed on these hardcoded ids and NOTHING else. There is no
  ``?demo=true`` query param, no ``X-Demo`` header, no request-body flag, and
  there must never be one: the free-source-reveal branch in
  ``api/workspace.py::get_reference_source`` sits above ``resolve_access``, so a
  caller-supplied predicate there would re-open the exact metering bypass
  migration 104 was written to close.

Constants, not env vars
-----------------------
An env var would have to be set identically on both Railway services. When it
drifts, the tour silently becomes a 404 for everybody — the same failure shape
already lived through with ``ISR_BAKE_SECRET``. A literal in the repo cannot
drift.
"""
from __future__ import annotations

# The conversation every account may read. Owned by xl0rch@gmail.com, who keeps
# seeing it as an ordinary (editable) conversation of their own.
DEMO_CONVERSATION_ID = "f4804262-da8c-45eb-87c2-911025377d13"

# The one workspace item inside it that the tour opens: wi_seq=1, kind
# ``agent_search``, subtype ``legal_synthesis``. Its reference reveals are free.
DEMO_ITEM_ID = "ac478719-4897-48ee-a844-30bbb482da27"


def _matches(value: str | None, target: str) -> bool:
    """Case-insensitive UUID comparison against a hardcoded id.

    A UUID can legitimately arrive from the URL path in upper case; Postgres
    would match it, so the predicate must too, or the allowance would depend on
    how the client happened to spell the same id. This compares against a
    module constant only — never against anything the caller chose.
    """
    if not value:
        return False
    return value.strip().lower() == target


def is_demo_conversation(conversation_id: str | None) -> bool:
    """True when this is THE shared demo conversation."""
    return _matches(conversation_id, DEMO_CONVERSATION_ID)


def is_demo_item(item_id: str | None) -> bool:
    """True when this is THE shared demo workspace item."""
    return _matches(item_id, DEMO_ITEM_ID)


__all__ = [
    "DEMO_CONVERSATION_ID",
    "DEMO_ITEM_ID",
    "is_demo_conversation",
    "is_demo_item",
]
