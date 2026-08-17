"""Product analytics — taxonomy, UA bucketing, row building, insert, retention.

The service half of `.claude/plans/product_analytics.md` Phase 0. The route
(`backend/app/api/analytics.py`) does transport concerns only — bot drop, rate
limit, opportunistic auth, always-204 — and everything that decides *what gets
written* lives here.

FOUR THINGS THIS MODULE EXISTS TO GUARANTEE (plan §2 — "a visit is tracked, a
person is not"):

1. **The raw User-Agent never leaves this function.** :func:`classify_client`
   takes the header, returns three buckets (device / browser / OS), and the
   string is discarded. A UA is a near-unique fingerprint; the buckets are what
   was asked for anyway. No parser dependency — plan §7 T7: the backend builds
   from `requirements.lock` and has a live dependency-reproducibility concern,
   so `Sec-CH-UA-Mobile` (a boolean Chrome sends by default) plus ~40 lines of
   regex covers mobile/tablet/desktop and the six browsers that matter.
2. **No query string is ever stored.** :func:`sanitize_path` cuts at `?` and
   `#` — `?q=` on the navigation search surfaces is user-typed legal text in a
   product for lawyers (plan §7 T4). The client is told to send `path` only;
   this is the server-side enforcement of that rule, because a rule enforced
   only in the client is a rule that lasts until the next refactor.
3. **Referrers are reduced to a host.** The source page's own query string can
   carry someone else's search terms (:func:`normalize_host`).
4. **An unknown `event_name` is dropped, not stored.** Twenty-two names exist
   (§3 + §3b); anything else is a client typo or a probe, and letting it through
   would create buckets no query knows about.

The IP is handled entirely in the route (rate limiting only) and is never passed
into this module — there is no parameter here that could accept one.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


# ============================================
# EVENT TAXONOMY (plan §3 + §3b)
# ============================================

# §3 — the public funnel. Nine events, every one mapped to a question in §0.
PUBLIC_EVENT_NAMES = frozenset(
    {
        "session_start",     # first event of a tab: entry_path, referrer_host, utm_*
        "page_view",         # every route change, including client-side nav
        "page_exit",         # tab hidden / navigating away: dwell_ms, max_scroll_pct
        "gate_view",         # a gated surface became VISIBLE (not merely rendered)
        "gate_cta_click",    # signup/login clicked FROM a gate
        "gate_dismiss",      # gate popup closed without clicking
        "signup_started",    # /login?mode=register rendered
        "signup_completed",  # account created
        "quota_blocked",     # authed user refused by a limit
    }
)

# §3b — chat depth. "Did they wait for the answer?" Every one of these carries a
# real user_id, which is why the session-only decision in §2 does not bite here.
CHAT_EVENT_NAMES = frozenset(
    {
        "chat_send",            # message submitted (user submit ONLY — §7 T14)
        "run_first_token",      # first `token` SSE
        "run_done",             # `done` SSE
        "run_failed",           # `error` SSE
        "run_paused",           # `agent_question` SSE — awaiting the user's reply
        "tab_hidden",           # visibilitychange → hidden, carries run_state
        "tab_visible",          # visibilitychange → visible
        "page_leave",           # pagehide with persisted === false (§7 T12)
        "answer_seen",          # assistant bubble ≥50% visible ≥1s AFTER done
        "wi_created",           # workspace_item_created SSE
        "wi_opened",            # WorkspaceCard onClick
        "wi_dwell",             # viewer closed
        "conversation_opened",  # a conversation is loaded
    }
)

# The allowlist the endpoint validates against. Adding a name here is the ONLY
# way a new event becomes storable — deliberately, so a typo in the client is a
# dropped event rather than a silent new bucket that no §6 query counts.
EVENT_NAMES = PUBLIC_EVENT_NAMES | CHAT_EVENT_NAMES

# The gate surfaces §3 enumerates. NOT enforced (gate_kind rides in `props`,
# which is open by design — see sanitize_props); kept here so the funnel query
# and the frontend have one written-down list to agree on.
GATE_KINDS = frozenset(
    {
        "anon_popup",        # AnonCtaPopup
        "full_content",      # FullContentGate
        "gate_banner",       # GateBanner
        "hub_wall",          # HubCtaWall
        "blog_cta",          # BlogConversionCta
        "search_modal",      # SearchCtaModal
        "judgment_summary",  # JudgmentSummary
    }
)

# Batch + field bounds. A beacon is unauthenticated and fire-and-forget, so
# every bound is enforced server-side.
MAX_BATCH_EVENTS = 20      # plan §5.5 — one sendBeacon flush, not one event
MAX_SESSION_KEY_CHARS = 64
MAX_PATH_CHARS = 300
MAX_HOST_CHARS = 120
MAX_UTM_CHARS = 120
MAX_PROP_KEYS = 24
MAX_PROP_KEY_CHARS = 40
MAX_PROP_VALUE_CHARS = 300

USER_TYPE_AUTHED = "authed"
USER_TYPE_ANON = "anon"


# ============================================
# UA BUCKETING — the raw string dies in here
# ============================================


@dataclass(frozen=True)
class ClientBuckets:
    """What survives of a User-Agent: three low-cardinality buckets.

    All three are Optional: a caller with no UA and no client hints (curl, a
    stripped proxy) gets NULLs rather than an invented `desktop`, so
    "unclassifiable" is visible in the data instead of quietly inflating the
    desktop share — the exact number question 1 exists to answer.
    """

    device_type: Optional[str]  # mobile | tablet | desktop
    browser: Optional[str]      # chrome | safari | firefox | edge | samsung | other
    os: Optional[str]           # ios | android | windows | macos | linux | other


# Tablets FIRST: an Android tablet's UA contains "android" but not "mobile"
# (Google's own rule), and an iPad is "ipad", not "iphone". Getting this order
# wrong files every tablet under mobile.
_TABLET_RE = re.compile(r"ipad|tablet|playbook|silk|kindle|android(?!.*mobile)")
_MOBILE_RE = re.compile(
    r"mobi|iphone|ipod|android|blackberry|iemobile|opera mini|windows phone"
)

# Browser order is load-bearing: Edge, Samsung Internet and Chrome all ship
# "chrome" in their UA, and Chrome ships "safari". Most specific wins.
_BROWSER_PATTERNS = (
    ("edge", re.compile(r"edg[ea]?/|edgios|edge/")),
    ("samsung", re.compile(r"samsungbrowser")),
    ("firefox", re.compile(r"firefox|fxios")),
    ("chrome", re.compile(r"chrome|crios|chromium|crmo")),
    ("safari", re.compile(r"safari")),
)

# iOS before macOS ("iPad; CPU OS 13_2 like Mac OS X"), Android before Linux
# (every Android UA says "Linux"). Both inversions are silent if unnoticed.
_OS_PATTERNS = (
    ("ios", re.compile(r"iphone|ipad|ipod|ios;")),
    ("android", re.compile(r"android")),
    ("windows", re.compile(r"windows")),
    ("macos", re.compile(r"mac os x|macintosh")),
    ("linux", re.compile(r"linux|x11|ubuntu|fedora|cros")),
)

# Sec-CH-UA-Platform is a LOW-ENTROPY hint Chromium sends by default (quoted).
_PLATFORM_HINTS = {
    "android": "android",
    "ios": "ios",
    "macos": "macos",
    "windows": "windows",
    "linux": "linux",
}

# Sec-CH-UA brand list, also sent by default. Substring match is enough: the
# header looks like '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'.
_BRAND_HINTS = (
    ("edge", "microsoft edge"),
    ("samsung", "samsung internet"),
    ("chrome", "google chrome"),
    ("chrome", "chromium"),
)

_TRUTHY_CH_MOBILE = {"?1", "1", "true"}
_FALSY_CH_MOBILE = {"?0", "0", "false"}


def _device_from_ua(ua: str) -> Optional[str]:
    if not ua:
        return None
    if _TABLET_RE.search(ua):
        return "tablet"
    if _MOBILE_RE.search(ua):
        return "mobile"
    return "desktop"


def _browser_from_ua(ua: str) -> Optional[str]:
    if not ua:
        return None
    for name, pattern in _BROWSER_PATTERNS:
        if pattern.search(ua):
            return name
    return "other"


def _os_from_ua(ua: str) -> Optional[str]:
    if not ua:
        return None
    for name, pattern in _OS_PATTERNS:
        if pattern.search(ua):
            return name
    return "other"


def classify_client(
    *,
    user_agent: Optional[str] = None,
    ch_ua_mobile: Optional[str] = None,
    ch_ua_platform: Optional[str] = None,
    ch_ua: Optional[str] = None,
) -> ClientBuckets:
    """Bucket one caller. The UA string goes in; only three buckets come out.

    Client hints win where they exist because they are structured and Chromium
    sends them by default; the UA regex is the fallback that covers Safari and
    Firefox, which send no hints at all.

    * ``device_type`` — ``Sec-CH-UA-Mobile`` decides the mobile/desktop axis
      (that is the whole reason plan §5.5 names it: one reliable boolean). The
      UA still supplies the *tablet* refinement, because a tablet reports
      ``?0`` and would otherwise be indistinguishable from a laptop.
    * ``browser`` — ``Sec-CH-UA`` brands, else UA regex.
    * ``os`` — ``Sec-CH-UA-Platform``, else UA regex.

    Never raises: this runs on an anonymous beacon that must always answer 204.
    """
    ua = (user_agent or "").lower()
    hint_mobile = (ch_ua_mobile or "").strip().lower()
    platform = (ch_ua_platform or "").strip().strip('"').lower()
    brands = (ch_ua or "").lower()

    ua_device = _device_from_ua(ua)

    if hint_mobile in _TRUTHY_CH_MOBILE:
        device = "tablet" if ua_device == "tablet" else "mobile"
    elif hint_mobile in _FALSY_CH_MOBILE:
        device = "tablet" if ua_device == "tablet" else "desktop"
    else:
        device = ua_device

    browser: Optional[str] = None
    for name, needle in _BRAND_HINTS:
        if needle in brands:
            browser = name
            break
    if browser is None:
        browser = _browser_from_ua(ua)

    os_name = _PLATFORM_HINTS.get(platform) or _os_from_ua(ua)
    if os_name is None and platform:
        # A platform hint we don't have a bucket for (e.g. "Chrome OS") is
        # still evidence the caller is a real browser.
        os_name = "other"

    return ClientBuckets(device_type=device, browser=browser, os=os_name)


# ============================================
# FIELD SANITIZERS
# ============================================


def sanitize_path(raw: Any) -> Optional[str]:
    """A site-relative path with the query string and fragment removed.

    Plan §7 T4 is enforced HERE, not just in the client: `?q=` on the search
    surfaces is user-typed legal text — potentially a case description — and it
    must never reach a row. A full URL is accepted and reduced to its path so a
    client sending `location.href` cannot smuggle one in through the origin.
    """
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    if "://" in value:
        value = urlparse(value).path or "/"
    value = value.split("?", 1)[0].split("#", 1)[0]
    if not value:
        return None
    if not value.startswith("/"):
        value = "/" + value
    return value[:MAX_PATH_CHARS]


def normalize_host(raw: Any) -> Optional[str]:
    """Host only — never a full referrer URL (plan §2).

    Accepts either a bare host or a full URL. Userinfo and port are dropped;
    the result is lowercased so `Google.com` and `google.com` are one row in the
    referrer breakdown rather than two.
    """
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    if "://" in value:
        value = urlparse(value).netloc
    value = value.split("/", 1)[0].split("?", 1)[0].strip().lower()
    if "@" in value:
        value = value.rsplit("@", 1)[-1]
    if value.startswith("["):           # IPv6 literal — leave intact
        return value[:MAX_HOST_CHARS] or None
    value = value.split(":", 1)[0]
    return value[:MAX_HOST_CHARS] or None


def _sanitize_short_text(raw: Any, limit: int) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value[:limit] if value else None


def sanitize_props(raw: Any) -> dict:
    """Scalars only, bounded in count and length.

    Prop KEYS are deliberately NOT allowlisted: the taxonomy gate is on
    `event_name`, and Phase 3 adds props to existing events (`stage`,
    `run_state`, `ms_hidden`, …). Making every new prop a backend deploy would
    guarantee the two halves drift.

    What IS enforced: no nested objects or arrays (a beacon is unauthenticated —
    an open jsonb blob is an open write surface), no non-finite floats (json
    would serialise `NaN`, PostgREST would reject the request, and the whole
    batch would be lost to one bad number), and hard caps on key count and
    string length.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, value in raw.items():
        if len(out) >= MAX_PROP_KEYS:
            break
        if not isinstance(key, str):
            continue
        key = key.strip()
        if not key or len(key) > MAX_PROP_KEY_CHARS:
            continue
        if value is None or isinstance(value, bool):
            out[key] = value
        elif isinstance(value, int):
            out[key] = value
        elif isinstance(value, float):
            if math.isfinite(value):
                out[key] = value
        elif isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                out[key] = trimmed[:MAX_PROP_VALUE_CHARS]
        # dict / list / anything else: dropped.
    return out


# ============================================
# ROW BUILDING
# ============================================


def build_event_rows(
    raw_events: Iterable[Any],
    *,
    client: ClientBuckets,
    user_id: Optional[str],
    user_type: str,
) -> list[dict]:
    """Turn a validated batch into `analytics_events` rows, IN ORDER.

    Order matters: `event_id` is a bigserial and the §6b chat-depth queries walk
    one run's events by it, so the rows must be inserted in the sequence the
    client fired them. `occurred_at` defaults to now() server-side and a whole
    flush can share a millisecond — the serial is the tiebreaker.

    Individual bad events are DROPPED, never fatal: one malformed entry must not
    cost the other nineteen (the batch-level guards live in the route).

    `referrer_host` / `utm_*` are lifted out of `props` on `session_start` only,
    matching the schema comment "Entry attribution. Populated on session_start
    only" — a page_view carrying them would double-count the session's source.
    """
    rows: list[dict] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue

        name = raw.get("event_name")
        if not isinstance(name, str) or name.strip() not in EVENT_NAMES:
            # Unknown name = client typo or a probe. Dropped silently; the batch
            # still returns 204 (plan §5.5).
            continue
        name = name.strip()

        session_key = _sanitize_short_text(
            raw.get("session_key"), MAX_SESSION_KEY_CHARS
        )
        if not session_key:
            # `session_key` is NOT NULL and is the join key for every derived
            # metric (bounce, exit page, gate abandonment). An unkeyed event is
            # noise, so the client is built to go silent rather than send one.
            continue

        props = sanitize_props(raw.get("props"))

        # ⚠ EVERY ROW CARRIES EVERY KEY, including the attribution columns it
        # will leave NULL. PostgREST's bulk insert historically refused an array
        # whose objects have differing key sets (PGRST102, "All object keys must
        # match"); modern versions default the missing ones instead, but a batch
        # is exactly the mixed shape that would trip it — `session_start` sets
        # utm_*/referrer_host and `page_view` does not. The failure mode is the
        # worst kind here: insert_events swallows the error, so the symptom
        # would be an empty table rather than an alarm. Uniform keys cost
        # nothing and remove the question.
        row: dict = {
            "session_key": session_key,
            "user_id": user_id,
            "user_type": user_type,
            "event_name": name,
            "path": sanitize_path(raw.get("path")),
            "device_type": client.device_type,
            "browser": client.browser,
            "os": client.os,
            "referrer_host": None,
            "utm_source": None,
            "utm_medium": None,
            "utm_campaign": None,
            "props": props,
        }

        if name == "session_start":
            row["referrer_host"] = normalize_host(props.get("referrer_host"))
            row["utm_source"] = _sanitize_short_text(
                props.get("utm_source"), MAX_UTM_CHARS
            )
            row["utm_medium"] = _sanitize_short_text(
                props.get("utm_medium"), MAX_UTM_CHARS
            )
            row["utm_campaign"] = _sanitize_short_text(
                props.get("utm_campaign"), MAX_UTM_CHARS
            )
            # Keep the normalized host in props too, so a client that sent a
            # full URL cannot leave the raw string behind in the jsonb.
            if "referrer_host" in props:
                props["referrer_host"] = row["referrer_host"]

        rows.append(row)

    return rows


# ============================================
# PERSISTENCE (sync — call through run_db)
# ============================================


def lookup_user_id(supabase: SupabaseClient, auth_id: str) -> Optional[str]:
    """``users.auth_id`` → ``users.user_id``. ``None`` on anything unexpected.

    Deliberately NOT ``case_service.get_user_id``, which raises 401 for a
    missing profile and 403 for an account in the deletion grace window. This
    endpoint answers 204 no matter what, and neither condition says anything
    about the event being recorded.

    A caller whose row cannot be resolved is still ``authed`` — their token
    verified — with a NULL ``user_id``. That combination is legitimate and is
    precisely why ``user_type`` is its own column (migration 139).
    """
    if not auth_id:
        return None
    try:
        result = (
            supabase.table("users")
            .select("user_id")
            .eq("auth_id", auth_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("analytics: user_id lookup failed for auth_id: %s", exc)
        return None
    rows = getattr(result, "data", None) or []
    if not rows:
        return None
    return rows[0].get("user_id")


def insert_events(supabase: SupabaseClient, rows: list[dict]) -> int:
    """Insert a batch. Returns the number of rows attempted; never raises.

    Analytics is best-effort by construction (plan §7 T9): a dead table, a
    missing migration or a PostgREST blip must cost the reader nothing, so the
    failure is logged and the endpoint still answers 204.

    `returning="minimal"` — nobody reads the inserted rows back, and asking
    PostgREST to echo 20 rows per beacon is pure bandwidth.
    """
    if not rows:
        return 0
    try:
        supabase.table("analytics_events").insert(rows, returning="minimal").execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("analytics: insert of %d event(s) failed: %s", len(rows), exc)
        return 0
    return len(rows)


# ============================================
# RETENTION
# ============================================

# 180 days. Plan §4 proposed 90 and §9 Q2 asked whether the chat-depth cohorts
# (§3b) need longer — they do: return-in-a-later-session and answer-seen rates
# are the metrics that reframe every abandonment number, and they thin out far
# faster than pageview aggregates. Past this window a rollup answers everything
# raw rows do, and keeping them is pure PDPL exposure.
RETENTION_DAYS = 180


def purge_old_analytics_events(supabase: SupabaseClient) -> dict:
    """Delete events older than :data:`RETENTION_DAYS`. Never raises.

    Registered as the daily APScheduler job `analytics_events_purge` in
    `backend.app.main`. Idempotent: a second run in the same day deletes nothing
    because the first already cleared the window.

    `returning="minimal"` matters here more than anywhere — the first pass after
    the window opens can match a large number of rows, and the default
    `representation` would ship every one of them back over the wire.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    ).isoformat()
    stats: dict = {"cutoff": cutoff, "deleted": 0, "ok": False}
    try:
        result = (
            supabase.table("analytics_events")
            .delete(count="exact", returning="minimal")
            .lt("occurred_at", cutoff)
            .execute()
        )
        stats["deleted"] = getattr(result, "count", None) or 0
        stats["ok"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("analytics retention: purge failed (cutoff=%s): %s", cutoff, exc)
    return stats


if __name__ == "__main__":
    # Manual run:  python -m backend.app.services.analytics_service
    logging.basicConfig(level=logging.INFO)
    from shared.db.client import get_supabase_client

    print(purge_old_analytics_events(get_supabase_client()))


__all__ = [
    "CHAT_EVENT_NAMES",
    "ClientBuckets",
    "EVENT_NAMES",
    "GATE_KINDS",
    "MAX_BATCH_EVENTS",
    "MAX_SESSION_KEY_CHARS",
    "PUBLIC_EVENT_NAMES",
    "RETENTION_DAYS",
    "USER_TYPE_ANON",
    "USER_TYPE_AUTHED",
    "build_event_rows",
    "classify_client",
    "insert_events",
    "lookup_user_id",
    "normalize_host",
    "purge_old_analytics_events",
    "sanitize_path",
    "sanitize_props",
]
