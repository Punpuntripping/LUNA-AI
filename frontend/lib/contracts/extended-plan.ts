// «بحث شامل» plan card (extended_search.md §4.4, D26/D27/D31/D32).
//
// When a deep-search turn plans more than one search, the backend pauses the
// turn and saves an assistant row with `metadata.kind = "agent_question"`,
// `metadata.pause_reason = "extended_plan"` and `metadata.extended_plan` =
// `ExtendedPlanCard` below. The row's `content` is the same plan in words (a
// text fallback for clients that cannot render the card).
//
// A button sends a normal user message whose `content` is the option's
// `label` and whose payload carries `plan_choice` (`SendMessagePayload`). The
// backend resumes the paused turn and runs that stored option exactly — no
// re-planning. Anything TYPED instead is a normal turn through the router and
// the plan expires (D32). The backend writes the result into
// `extended_plan.outcome` on the card row, so the card locks on refetch.
//
// Pure TypeScript — shared by web (`@/lib/contracts`) and mobile (`@contracts`).

/** Which option a button runs. */
export type PlanOptionId = "plan" | "cheaper" | "ordinary";

/** How a card ended. `typed` = the user typed instead of pressing a button;
 *  `insufficient` = at the click the user's points no longer covered it. */
export type PlanOutcome = PlanOptionId | "typed" | "expired" | "insufficient";

/** Which side(s) one search covers — shown as الأنظمة / الأحكام / الأنظمة والأحكام. */
export type PlanSearchSides = "regulations" | "judgments" | "both";

export const PLAN_SIDES_AR: Record<PlanSearchSides, string> = {
  regulations: "الأنظمة",
  judgments: "الأحكام",
  both: "الأنظمة والأحكام",
};

export interface ExtendedPlanSearch {
  /** Short Arabic line written by the planner (≤ ~70 chars). */
  title: string;
  sides: PlanSearchSides;
}

export interface ExtendedPlanOption {
  id: PlanOptionId;
  /** Button text, Arabic, written by the backend: «نفّذ الخطة» / «نفّذ الأوفر» / «بحث عادي». */
  label: string;
  /** Code's estimate in points, already rounded to an integer. */
  points: number;
  /** The searches this option runs ("ordinary" = one search, today's path). */
  searches: ExtendedPlanSearch[];
}

export interface ExtendedPlanCard {
  version: 1;
  /** The paused run; echoed back in `PlanChoicePayload.run_id`. */
  run_id: string;
  /** Points left (tightest window), rounded down — «المتبقي لك». `null` when
   *  unknown (an unlimited account has no remaining figure): the card then
   *  shows the cost alone. */
  points_left: number | null;
  /** Always `plan` and `ordinary`; `cheaper` only when it saves. Display order = array order. */
  options: ExtendedPlanOption[];
  /** The planner's one-line cheaper note («أقدر أدمج البحث 2 مع 3 …»); null when no cheaper option. */
  cheaper_note?: string | null;
  /** Points the cheaper option saves vs the plan (integer); null when no cheaper option. */
  cheaper_saving?: number | null;
  /** Set by the backend once the card is answered; absent/null while it waits. */
  outcome?: PlanOutcome | null;
  /** ISO timestamp of the outcome. */
  outcome_at?: string | null;
}

/** Sent with a button click (`SendMessagePayload.plan_choice`). */
export interface PlanChoicePayload {
  run_id: string;
  option: PlanOptionId;
}

const OPTION_IDS: readonly string[] = ["plan", "cheaper", "ordinary"];
const OUTCOMES: readonly string[] = ["plan", "cheaper", "ordinary", "typed", "expired", "insufficient"];
const SIDES: readonly string[] = ["regulations", "judgments", "both"];

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Validate `metadata.extended_plan` from a message row. Returns null when it is
 * missing or malformed (render the row's text instead). Drops malformed
 * options/searches rather than failing the whole card; a card with no `plan`
 * option is treated as malformed.
 */
export function parseExtendedPlan(raw: unknown): ExtendedPlanCard | null {
  if (!isObj(raw) || raw.version !== 1 || typeof raw.run_id !== "string") return null;
  const options: ExtendedPlanOption[] = [];
  for (const o of Array.isArray(raw.options) ? raw.options : []) {
    if (!isObj(o) || typeof o.id !== "string" || !OPTION_IDS.includes(o.id)) continue;
    if (typeof o.label !== "string" || typeof o.points !== "number" || !Number.isFinite(o.points)) continue;
    const searches: ExtendedPlanSearch[] = [];
    for (const s of Array.isArray(o.searches) ? o.searches : []) {
      if (isObj(s) && typeof s.title === "string" && typeof s.sides === "string" && SIDES.includes(s.sides)) {
        searches.push({ title: s.title, sides: s.sides as PlanSearchSides });
      }
    }
    options.push({ id: o.id as PlanOptionId, label: o.label, points: Math.round(o.points), searches });
  }
  if (!options.some((o) => o.id === "plan")) return null;
  const outcome = typeof raw.outcome === "string" && OUTCOMES.includes(raw.outcome) ? (raw.outcome as PlanOutcome) : null;
  return {
    version: 1,
    run_id: raw.run_id,
    points_left: typeof raw.points_left === "number" && Number.isFinite(raw.points_left) ? Math.floor(raw.points_left) : null,
    options,
    cheaper_note: typeof raw.cheaper_note === "string" ? raw.cheaper_note : null,
    cheaper_saving: typeof raw.cheaper_saving === "number" && Number.isFinite(raw.cheaper_saving) ? Math.round(raw.cheaper_saving) : null,
    outcome,
    outcome_at: typeof raw.outcome_at === "string" ? raw.outcome_at : null,
  };
}

// ---------------------------------------------------------------------------
// The card's words and state. One copy for both apps, so the web `PlanCard`
// and the mobile card read and behave the same (mobile mirrors the web).
// ---------------------------------------------------------------------------

/** Fixed Arabic lines of the card (§4.4). */
export const PLAN_CARD_AR = {
  title: "بحث شامل — الخطة المقترحة",
  merge: "ثم أجمع النتائج في جواب واحد",
  hint: "أو اكتب طلبك في خانة الرسالة",
  expired: "انتهت صلاحية الخطة",
  insufficient: "لم يعد رصيدك يكفي لهذه الخطة",
  chosen: "تم اختيار",
} as const;

/** Fallback button words when a chosen option is missing from `options`
 *  (same words the backend writes: OPTION_LABELS_AR). */
const OPTION_LABELS_AR: Record<PlanOptionId, string> = {
  plan: "نفّذ الخطة",
  cheaper: "نفّذ الأوفر",
  ordinary: "بحث عادي",
};

/** «14 نقطة» / «3 نقاط» — Latin digits; «نقاط» for 3–10, «نقطة» otherwise. */
export function planPointsAr(n: number): string {
  const v = Math.max(0, Math.round(n));
  return `${v} ${v >= 3 && v <= 10 ? "نقاط" : "نقطة"}`;
}

function optionOf(card: ExtendedPlanCard, id: PlanOptionId): ExtendedPlanOption | undefined {
  return card.options.find((o) => o.id === id);
}

/** The numbered lines come from the `plan` option's searches. */
export function planSearches(card: ExtendedPlanCard): ExtendedPlanSearch[] {
  return optionOf(card, "plan")?.searches ?? [];
}

/** «استحقاق قيمة أمر التغيير — الأنظمة والأحكام» (the number is drawn separately). */
export function planSearchLineAr(search: ExtendedPlanSearch): string {
  return `${search.title.trim()} — ${PLAN_SIDES_AR[search.sides]}`;
}

/** «التكلفة التقريبية: نحو 14 نقطة · المتبقي لك: 18 نقطة» — the cost alone
 *  when `points_left` is unknown. X = the `plan` option's points. */
export function planCostLineAr(card: ExtendedPlanCard): string {
  const cost = `التكلفة التقريبية: نحو ${planPointsAr(optionOf(card, "plan")?.points ?? 0)}`;
  return card.points_left === null || card.points_left === undefined
    ? cost
    : `${cost} · المتبقي لك: ${planPointsAr(card.points_left)}`;
}

/** The planner's cheaper note + « — وأوفّر نحو N نقاط». Null unless the card
 *  has both a `cheaper` option and a note; the saving part needs ≥ 1 point
 *  (`cheaper_saving`, else plan − cheaper). */
export function planCheaperLineAr(card: ExtendedPlanCard): string | null {
  const cheaper = optionOf(card, "cheaper");
  const note = (card.cheaper_note ?? "").trim().replace(/[\s.،؛]+$/u, "");
  if (!cheaper || !note) return null;
  const plan = optionOf(card, "plan");
  const saving = card.cheaper_saving ?? (plan ? plan.points - cheaper.points : null);
  return saving !== null && saving >= 1 ? `${note} — وأوفّر نحو ${planPointsAr(saving)}` : note;
}

/** Button text «نفّذ الخطة · 14». */
export function planButtonLabelAr(option: ExtendedPlanOption): string {
  return `${option.label} · ${option.points}`;
}

/**
 * What the card shows right now:
 * - `active`   — buttons live (the latest row, unanswered, nothing running).
 * - `waiting`  — the latest row but a send is in flight in the conversation: buttons off, no line.
 * - `closed`   — a later message exists and no outcome is known yet: buttons off, no line.
 * - `chosen`   — an option ran (server `outcome`, or this client's click until the outcome lands).
 * - `expired`  — the user typed instead, or the plan expired (`typed` / `expired`).
 * - `insufficient` — at the click the points no longer covered the option.
 */
export type PlanCardView =
  | { state: "active" }
  | { state: "waiting" }
  | { state: "closed" }
  | { state: "chosen"; option: PlanOptionId }
  | { state: "expired" }
  | { state: "insufficient" };

export function planCardView(
  card: ExtendedPlanCard,
  ui: {
    /** The card row is the conversation's last message. */
    isLatest: boolean;
    /** A send is in flight in this conversation (from the POST to its end). */
    busy: boolean;
    /** The option this client clicked, if any (optimistic until `outcome`). */
    pick: PlanOptionId | null;
  },
): PlanCardView {
  const o = card.outcome;
  if (o === "plan" || o === "cheaper" || o === "ordinary") return { state: "chosen", option: o };
  if (o === "typed" || o === "expired") return { state: "expired" };
  if (o === "insufficient") return { state: "insufficient" };
  // A click shows as chosen while its send runs and once the reply follows the
  // card; a refused send (quota / parallel cap / duplicate) removes the user
  // row, the card is the latest again and its buttons come back.
  if (ui.pick && (ui.busy || !ui.isLatest)) return { state: "chosen", option: ui.pick };
  if (!ui.isLatest) return { state: "closed" };
  if (ui.busy) return { state: "waiting" };
  return { state: "active" };
}

/**
 * A click whose send has ended with the card still the conversation's latest
 * row and no outcome: the user's row did not survive (a refused send —
 * quota / parallel cap / duplicate — or a failure that dropped the optimistic
 * row), so nothing ran. The card must forget that pick — kept, a later message
 * would make the card read «تم اختيار: …» for a click that never ran. Both
 * cards reset their local pick when this turns true (the in-flight
 * double-click guard is separate: `busy` covers the whole send).
 */
export function planPickRefused(
  card: ExtendedPlanCard,
  ui: {
    isLatest: boolean;
    busy: boolean;
    pick: PlanOptionId | null;
  },
): boolean {
  return ui.pick !== null && !ui.busy && ui.isLatest && (card.outcome ?? null) === null;
}

/** The line under the buttons: the hint while active, else the outcome; null
 *  when nothing is known yet. */
export function planStatusLineAr(card: ExtendedPlanCard, view: PlanCardView): string | null {
  switch (view.state) {
    case "active":
      return PLAN_CARD_AR.hint;
    case "chosen":
      return `${PLAN_CARD_AR.chosen}: ${optionOf(card, view.option)?.label ?? OPTION_LABELS_AR[view.option]}`;
    case "expired":
      return PLAN_CARD_AR.expired;
    case "insufficient":
      return PLAN_CARD_AR.insufficient;
    default:
      return null;
  }
}
