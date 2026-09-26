/**
 * Product analytics — the event vocabulary
 * (`.claude/plans/product_analytics.md` §3 public funnel, §3b chat depth).
 *
 * ONE place for the names, so a typo is a compile error instead of a silent
 * hole in a funnel: an event nobody ever emits reads exactly like an event
 * nobody ever triggers, and the difference is invisible in SQL.
 *
 * The union below is COMPLETE from day one even though Phase 1 emits only three
 * of them (`session_start`, `page_view`, `page_exit`). The gate surfaces
 * (Phase 2) and the chat instrumentation (Phase 3) import these names, and a
 * name that is missing here is a build failure over there.
 *
 * ⚠ This module is types only — no runtime, no DOM, no storage. It is safe to
 * import from a server component; `client.ts` is the part that must not be
 * (T2: an ISR bake is shared by every subsequent visitor).
 */

/**
 * Every event this instrument may ever emit. Deliberately small — each name
 * maps to a question in §0. Anything that does not serve one of those questions
 * does not belong here: this is a funnel instrument, not a general event bus.
 */
export type AnalyticsEventName =
  // ---- §3 public funnel -------------------------------------------------
  /** First event of a tab. Props: `entry_path`, `referrer_host`, `utm_*`. */
  | "session_start"
  /** Every route change, client-side navigation included. Props: `path`. */
  | "page_view"
  /** Tab hidden / navigating away. Props: `dwell_ms`, `max_scroll_pct`. */
  | "page_exit"
  /** A gated surface became VISIBLE — not merely rendered (T6). */
  | "gate_view"
  /** Signup/login clicked FROM a gate. Props: `gate_kind`, `cta`. */
  | "gate_cta_click"
  /** Gate popup closed without clicking. Props: `gate_kind`. */
  | "gate_dismiss"
  /** `/login?mode=register` rendered. Props: `next_path`. */
  | "signup_started"
  /** Account created. */
  | "signup_completed"
  /** An authed user was refused by a limit. Props: `limit_kind`. */
  | "quota_blocked"
  // ---- §3b chat depth ---------------------------------------------------
  /** Message submitted — USER SUBMIT ONLY, never an SSE reconnect (T14). */
  | "chat_send"
  /** First `token` SSE arrived. Props: `ms_since_send`. */
  | "run_first_token"
  /** `done` SSE. Props: `ms_since_send`, `was_visible`. */
  | "run_done"
  /** `error` SSE. Props: `ms_since_send`, `stage`. */
  | "run_failed"
  /** `agent_question` SSE — the agent asked and is awaiting a reply. */
  | "run_paused"
  /** `visibilitychange → hidden`. Props: `run_state`, `ms_since_send`, `stage`. */
  | "tab_hidden"
  /** `visibilitychange → visible`. Props: `ms_hidden`, `run_state`. */
  | "tab_visible"
  /** `pagehide` with `persisted === false` — bfcache is not a departure (T12). */
  | "page_leave"
  /** Assistant bubble ≥50% visible for ≥1s AFTER `done` (T15). */
  | "answer_seen"
  /** `workspace_item_created` SSE. Props: `wi_id`, `kind`. */
  | "wi_created"
  /** A workspace card was opened. Props: `wi_id`, `kind`, `ms_since_created`. */
  | "wi_opened"
  /** The workspace viewer was closed. Props: `wi_id`, `dwell_ms`. */
  | "wi_dwell"
  /** A conversation was loaded. Props: `conversation_id`, `has_unseen_answer`. */
  | "conversation_opened"
  // ---- install surfaces (install_app_nudge.md, pwa_step1 §1E) -----------
  /** Chat nudge card appeared. Props: `platform`. */
  | "install_nudge_shown"
  /** «كيف؟» on the nudge. Props: `platform`. */
  | "install_nudge_how"
  /** ✕ on the nudge (14-day snooze). */
  | "install_nudge_dismissed"
  /** Install guide shown. Props: `source` (settings | nudge | app_page). */
  | "install_dialog_opened"
  /** Chrome install prompt answered. Props: `outcome` (accepted | dismissed). */
  | "install_prompt_result"
  /** `appinstalled` fired (Android / desktop Chrome only). Props: `platform`. */
  | "app_installed"
  /** Tab opened as the installed app — the iOS install KPI. Props: `platform`. */
  | "standalone_session"
  // ---- pwa_step1 §1D/§1E web push ----------------------------------------
  /** Permission prompt answered. Props: `result`, `source`. */
  | "push_permission"
  /** Push subscription registered with the backend. Props: `source`. */
  | "push_subscribed"
  /** Push turned off on this device. Props: `source`. */
  | "push_unsubscribed"
  // ---- email_otp_login.md — «الدخول برمز عبر البريد» (dev-gated) ---------
  /** `/auth/otp/request` answered 200 (says nothing about delivery). */
  | "otp_requested"
  /** Code accepted and the session is live. */
  | "otp_verified"
  /** Request or verify refused. Props: `reason` (invalid | rate_limited | error). */
  | "otp_failed";

/**
 * The real conversion surfaces, so the funnel can tell them apart (§3). The
 * names match the components that own them:
 * `anon_popup` = AnonCtaPopup · `full_content` = FullContentGate ·
 * `gate_banner` = the retired library GateBanner (kept so historical rows stay
 * readable; nothing emits it any more) · `hub_wall` = HubCtaWall ·
 * `blog_cta` = BlogConversionCta · `search_modal` = SearchCtaModal ·
 * `judgment_summary` = JudgmentSummary.
 */
export type GateKind =
  | "anon_popup"
  | "full_content"
  | "gate_banner"
  | "hub_wall"
  | "blog_cta"
  | "search_modal"
  | "judgment_summary";

/**
 * The field the whole chat-depth question turns on (§3b): it is what separates
 * «left before the answer arrived» from «left after reading it», on the exact
 * same browser event.
 */
export type RunState = "in_flight" | "paused" | "completed" | "idle";

/**
 * One event on the wire. The batch body is `{ events: AnalyticsEventPayload[] }`
 * and the endpoint always answers `204`.
 *
 * ⚠ `path` is a PATHNAME, never a URL and never a query string (T4): `?q=` on
 * the navigation search surfaces is user-typed legal text in a product for
 * lawyers. The only query parameters that ever travel are `utm_source` /
 * `utm_medium` / `utm_campaign`, read by name into `props` on `session_start`.
 *
 * ⚠ There is no client timestamp and no client-supplied identity beyond
 * `session_key`: `occurred_at`, `user_id`, `user_type`, `device_type`,
 * `browser` and `os` are all settled server-side (§4, §5.5).
 */
export interface AnalyticsEventPayload {
  event_name: AnalyticsEventName;
  session_key: string;
  path?: string;
  props?: Record<string, unknown>;
}
