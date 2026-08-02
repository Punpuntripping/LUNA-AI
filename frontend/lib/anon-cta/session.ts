/**
 * Anon conversion popup — session cadence engine
 * (`.claude/plans/anon_conversion_popup.md` §4, revised 2026-08-02).
 *
 * A ROUND is one document that showed the popup. A round holds up to
 * `MAX_SHOTS_PER_DOC` impressions — one per scroll threshold (§3) — and the
 * session is bounded in ROUNDS, not in raw impressions:
 *
 *   Axis 1 — the hard cap:      at most `MAX_ROUNDS_PER_SESSION` (3) documents
 *                               may ever show the popup ⇒ ≤ 6 impressions.
 *   Axis 2 — the quiet period:  a round arms a cooldown of `QUIET_DOCS` further
 *                               eligible DOCUMENTS (the `{n+1}` period).
 *
 *     doc 1  35% + 8s          →  POPUP   ← opens round 1, arms quietFor
 *            80% (+ MIN_GAP)   →  POPUP   ← same round: the cooldown it just
 *                                            armed must NOT block this
 *     doc 2                    →  silent  (quietFor 3 → 2)
 *     doc 3                    →  silent  (quietFor 2 → 1)
 *     doc 4  35% / 80%         →  POPUP ×2  ← round 2, the {n+1} period
 *     doc 5, 6                 →  silent
 *     doc 7  35% / 80%         →  POPUP ×2  ← round 3 = last of the session
 *     doc 8 …                  →  silent forever (cap reached)
 *
 * ⚠ Counting raw impressions instead of rounds is the mistake this shape exists
 * to prevent: with two thresholds a "3 impressions" cap is spent halfway through
 * the second document, which is not the cadence the ladder promises.
 *
 * `sessionStorage`, not `localStorage` and not the auth store: a new tab starts
 * fresh (the chosen behaviour — "session only, then re-arm"), it survives
 * same-tab navigation between documents, which is exactly the lifetime the
 * counter needs, and it dies with the tab, so nothing persists about a visitor
 * who never created an account — a PDPL-friendly default and one less thing to
 * declare in /privacy.
 *
 * ⚠ EVERY accessor is try/catch-guarded and FAILS CLOSED (T11): storage
 * unavailable — privacy mode, SSR, a blocked third-party context — is treated
 * as muted, so the popup never shows. A broken read must never produce an
 * unbounded loop of impressions, and neither must a broken WRITE: an impression
 * that cannot be recorded is an impression that cannot be counted, so
 * `recordImpression()` reports failure and the caller stays silent.
 *
 * Pure logic. No React, no DOM beyond `sessionStorage` — every function takes an
 * optional storage so the ladder above can be asserted against a fake.
 */

import {
  MAX_ROUNDS_PER_SESSION,
  MAX_SHOTS_PER_DOC,
  QUIET_DOCS,
} from "@/lib/anon-cta/config";

/**
 * The storage namespace. Unchanged across shape revisions — `v` below is what
 * versions the shape, and a tab's state dies with the tab anyway, so there is
 * nothing to migrate and no second key to clean up.
 */
export const ANON_CTA_STORAGE_KEY = "luna_anon_cta_v1";

export interface AnonCtaState {
  /**
   * Shape version. Anything else — including the v1 shape from before the
   * two-threshold revision — is discarded and restarted at the default.
   */
  v: 2;
  /** ROUNDS so far: documents that showed the popup at least once. */
  rounds: number;
  /** Raw impressions so far this session. Bounded by rounds × shots-per-doc. */
  shots: number;
  /** The document whose round is OPEN — it may still fire its later threshold. */
  activeDoc: string;
  /** Impressions already spent on `activeDoc`. */
  docShots: number;
  /**
   * Eligible documents still to skip, COUNTING THE CURRENT ONE.
   *
   * ⚠ The "+ the current one" is the whole off-by-one of this feature. The
   * counter is decremented when a NEW document opens, and the popup fires when
   * it reads 0 — so the document that performs the decrement down to 0 would
   * otherwise be allowed to fire, and `QUIET_DOCS = 2` would silence exactly one
   * document instead of two. Arming with `QUIET_DOCS + 1` (see
   * `recordImpression`) makes the stored number mean «this document and the next
   * N are quiet», which is what produces the doc 1 → doc 4 → doc 7 ladder.
   */
  quietFor: number;
  /** A CTA was clicked → done for this session. */
  muted: boolean;
  /** Last eligible pathname, so a RE-RENDER is not a new document (T8). */
  lastDoc: string;
}

/** The slice of `Storage` this module needs — injectable for tests. */
export type AnonCtaStorage = Pick<Storage, "getItem" | "setItem">;

const DEFAULT_STATE: AnonCtaState = {
  v: 2,
  rounds: 0,
  shots: 0,
  activeDoc: "",
  docShots: 0,
  quietFor: 0,
  muted: false,
  lastDoc: "",
};

/**
 * The backing store, or `null` when there is none. `window.sessionStorage` can
 * THROW on access (not merely be absent) in privacy modes and blocked embeds,
 * hence the try/catch around the property read itself.
 */
function resolveStore(store?: AnonCtaStorage): AnonCtaStorage | null {
  if (store) return store;
  try {
    if (typeof window === "undefined") return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** A non-negative integer, or 0 for anything else on the wire. */
function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.floor(value)
    : 0;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function load(store: AnonCtaStorage): AnonCtaState | null {
  let raw: string | null;
  try {
    raw = store.getItem(ANON_CTA_STORAGE_KEY);
  } catch {
    // The STORAGE itself is unusable → fail closed (T11).
    return null;
  }
  if (!raw) return { ...DEFAULT_STATE };

  try {
    const parsed = JSON.parse(raw) as Partial<AnonCtaState> | null;
    // A junk, foreign or outdated VALUE is a different thing from broken
    // storage: the engine still works, it just has nothing to remember. Start a
    // fresh session rather than muting the tab forever over a bad string.
    if (!parsed || parsed.v !== 2) return { ...DEFAULT_STATE };
    return {
      v: 2,
      rounds: count(parsed.rounds),
      shots: count(parsed.shots),
      activeDoc: text(parsed.activeDoc),
      docShots: count(parsed.docShots),
      quietFor: count(parsed.quietFor),
      muted: parsed.muted === true,
      lastDoc: text(parsed.lastDoc),
    };
  } catch {
    return { ...DEFAULT_STATE };
  }
}

function save(store: AnonCtaStorage, state: AnonCtaState): boolean {
  try {
    store.setItem(ANON_CTA_STORAGE_KEY, JSON.stringify(state));
    return true;
  } catch {
    return false;
  }
}

/**
 * The current cadence state, or `null` when storage is unusable — which every
 * caller must read as "muted" (T11). Never throws.
 */
export function readAnonCtaState(store?: AnonCtaStorage): AnonCtaState | null {
  const target = resolveStore(store);
  return target ? load(target) : null;
}

/**
 * Register that a NEW eligible document was opened, decrementing the quiet
 * period by one and closing any round left open on the previous document.
 *
 * ⚠ T8 — deduped on `lastDoc`: a re-render, a remount, or a second mount of the
 * same path is NOT a new document. Counting renders would drain `quietFor`
 * without the reader opening anything, and the `{n+1}` period would collapse
 * into "every page".
 *
 * Returns the resulting state, or `null` when storage is unusable.
 */
export function noteEligibleDoc(
  pathname: string,
  store?: AnonCtaStorage,
): AnonCtaState | null {
  const target = resolveStore(store);
  if (!target) return null;

  const state = load(target);
  if (!state) return null;
  if (state.lastDoc === pathname) return state; // same document — no-op

  const next: AnonCtaState = {
    ...state,
    lastDoc: pathname,
    quietFor: Math.max(0, state.quietFor - 1),
    // The previous document's round is over: its unfired threshold does not
    // travel, and a later RETURN to that path must not inherit its permission.
    activeDoc: "",
    docShots: 0,
  };
  return save(target, next) ? next : null;
}

/**
 * Gate 3 of the suppression chain (§5) for a given document.
 *
 * Two distinct answers:
 *   · the round on THIS document is already open → permitted while the document
 *     has thresholds left, whatever `quietFor` says. The cooldown a round arms
 *     is about the NEXT documents; letting it block the same document's second
 *     threshold would silently revert the feature to one impression per page.
 *   · otherwise → a NEW round: session cap and quiet period both apply.
 *
 * `null` — the unreadable-storage case — is always `false`.
 */
export function canFire(
  state: AnonCtaState | null,
  pathname: string,
): boolean {
  if (!state) return false;
  if (state.muted) return false;

  if (state.activeDoc === pathname) return state.docShots < MAX_SHOTS_PER_DOC;

  if (state.rounds >= MAX_ROUNDS_PER_SESSION) return false;
  return state.quietFor === 0;
}

/**
 * Count an impression on `pathname`.
 *
 * The FIRST impression on a document opens a round: it increments `rounds` and
 * arms the quiet period. Later impressions on the same document only advance
 * the counters — arming again would push the cooldown out on every threshold
 * and (worse) block the very document doing the arming.
 *
 * The quiet period is armed here rather than on dismissal because a dismissal
 * handler cannot see the one case that matters: a reader who navigates away with
 * the popup open. There, an arm-on-dismiss engine leaves `quietFor` at 0 and
 * pitches again on the very next document.
 *
 * Returns `false` when the impression could not be persisted — the caller must
 * then NOT show the popup (T11: an uncounted impression repeats forever).
 */
export function recordImpression(
  pathname: string,
  store?: AnonCtaStorage,
): boolean {
  const target = resolveStore(store);
  if (!target) return false;

  const state = load(target);
  if (!state) return false;

  const opensRound = state.activeDoc !== pathname;

  return save(target, {
    ...state,
    rounds: opensRound ? state.rounds + 1 : state.rounds,
    shots: state.shots + 1,
    activeDoc: pathname,
    docShots: opensRound ? 1 : state.docShots + 1,
    // `+ 1` = the document showing this round, whose slot is consumed when the
    // NEXT document opens. See the `quietFor` doc comment: without it the ladder
    // skips one document instead of `QUIET_DOCS`.
    quietFor: opensRound ? QUIET_DOCS + 1 : state.quietFor,
  });
}

/**
 * Instant mute — a CTA was clicked (§4). The reader is on their way to /login;
 * if they come back without signing up, pitching them again in the same session
 * is nagging. Best-effort: a failed write only costs one extra impression, and
 * the hard cap still bounds it.
 */
export function muteAnonCta(store?: AnonCtaStorage): void {
  const target = resolveStore(store);
  if (!target) return;

  const state = load(target);
  if (!state) return;

  save(target, { ...state, muted: true });
}
