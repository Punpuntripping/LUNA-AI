/**
 * Single source for the «جولة المخرجات» coach-mark script — copy AND the
 * per-step machine contract (anchor ids, what makes the step advance, what the
 * stall-guard button should do). Components render this file and never inline
 * copy, exactly like `components/onboarding/onboarding-content.ts`.
 *
 * REGISTER (deliberate): the reader already uses ChatGPT/Claude fluently. The
 * composer, the bubbles, copy and regenerate are NOT explained anywhere here.
 * The only genuinely new machinery is the WI — the workspace item, its
 * references, and the library behind them.
 *
 * SCOPE — FIVE steps, one per idea (owner decision 2026-08-15; cut down from
 * thirteen). What survived is the chain a ChatGPT user has never seen: the
 * answer is a snippet of a bigger مخرج · the مخرج itself · its numbers are real
 * references · the source text behind a number · the workspace they collect in.
 * What was cut is either self-evident on screen or folded into one line of a
 * surviving step — the chat thread, the WI badge, the two pane exits, الإحالات,
 * the source-card exits, the four reference domains, مشاركة/حفظ كمدونة. Their
 * anchors are still emitted by the components and still listed below, so
 * restoring any of them is a copy-only change inside this file.
 *
 * DATA COUPLING (plan §10 trap 2): steps 3–4 assume the demo conversation's
 * trimmed reference set, as left by migration 128 — **[6]** is an in-force
 * دليل with a library page and 5 إحالات (the Act 3 anchor). Re-trim the
 * reference set and this copy starts lying.
 *
 * ⚠ THAT NUMBER IS HARDCODED IN FIVE PLACES. Migration 128 renumbered the set
 * (it dropped a draft-status نظام that migration 127 had left at [3]), and
 * every one of these has to move together:
 *   this file  — TOUR_ANCHOR_IDS · the step `key` · `anchors` · `cta` text ·
 *                `fallbackClick` · the `TourStoreBeat` union member
 *   TourOverlay — the `case` label and the `focusedReferenceN === N` compare
 * The SQL side is what actually catches a mismatch: 128's verification block
 * asserts [6] is `regulations` and aborts otherwise. If it moves a third time,
 * collapse it into one exported constant first.
 */

// ---------------------------------------------------------------------------
// Anchors — the `data-tour="…"` contract with the real components.
//
// The five-step script uses only the ones marked LIVE. The rest are kept both
// because the components still emit them and because they are what a restored
// step would point at; an unused id costs nothing at runtime.
// ---------------------------------------------------------------------------

export const TOUR_ANCHOR_IDS = [
  "chat-thread",
  "artifact-chip", // LIVE — step 1
  "citation-6", // LIVE — step 3
  "wi-body", // LIVE — step 2
  "wi-badge",
  "pane-close",
  "pane-back",
  "ref-card-9",
  "source-dialog", // LIVE — step 4
  "ref-crossrefs",
  "ref-exits",
  "wi-action-bar",
  "workspace-add", // LIVE — step 5
] as const;

export type TourAnchorId = (typeof TOUR_ANCHOR_IDS)[number];

// ---------------------------------------------------------------------------
// Advance conditions
// ---------------------------------------------------------------------------

/**
 * Navigation beats already modelled in `stores/chat-store.ts`. Watched through
 * a store subscription rather than a DOM click listener: the anchor node is
 * re-rendered by the very click we care about, so a listener bound to it can
 * miss its own event — a store transition cannot.
 */
export type TourStoreBeat =
  /** `workspaceByConversation[id].openItemId` became non-null. */
  | "wi-open"
  /**
   * `openItemId` became null again (back to the item list). Unused by the
   * five-step script — the last step performs that transition itself through
   * `onEnter: "return-to-item-list"` instead of asking for the click.
   */
  | "wi-closed"
  /** `focusedReferenceN === 6` — the Act 3 anchor (migration 128). */
  | "reference-6"
  /** The whole pane closed (`isOpen === false`). */
  | "pane-closed";

/**
 * The two beats with no store state behind them. Both are observed on the DOM
 * (MutationObserver + a slow safety poll) — see `useTourDomBeats`.
 */
export type TourDomBeat =
  /** The «عرض المصدر» Radix dialog is mounted and open. */
  | "source-dialog-open"
  /** The «الإحالات» disclosure reports `aria-expanded="true"`. */
  | "crossrefs-expanded";

export type TourCondition =
  | { readonly kind: "store"; readonly beat: TourStoreBeat }
  | { readonly kind: "dom"; readonly beat: TourDomBeat };

/**
 * Side effect performed once, when a step becomes the active one.
 *
 * `return-to-item-list` dismisses the reveal dialog AND closes the open WI, so
 * the workspace step can land on the item list without spending a whole step
 * asking the user to click «←» twice.
 */
export type TourEnterAction = "close-source-dialog" | "return-to-item-list";

/**
 * Which surface the step's anchors live on. Below `md` the workspace is a
 * full-viewport overlay and the chat is completely covered (§7.1), so a
 * `"chat"` step's anchors are still in the DOM with perfectly real rects while
 * being invisible behind the overlay. The engine uses this to refuse to
 * measure them instead of spotlighting a hidden node.
 */
export type TourStage = "chat" | "workspace";

export interface TourStep {
  /** Stable key — used for React keys and the anchor-change signal. */
  readonly key: string;
  /** Act label, shown as small print above the title. */
  readonly act: string;
  /**
   * Anchor(s) this step frames. More than one ⇒ the spotlight frames the
   * union of their rects (step 5 covers both pane exits at once).
   */
  readonly anchors: readonly TourAnchorId[];
  readonly stage: TourStage;
  readonly title: string;
  readonly body: string;
  /** «جرّب اضغط …» — the emphasised do-this line, when the step wants a click. */
  readonly cta?: string;
  /** Small print under the body (e.g. "معطّل في المحادثة التجريبية"). */
  readonly note?: string;
  /**
   * Any of these satisfied ⇒ the step advances by itself. Empty/absent ⇒ a
   * plain «التالي» step.
   */
  readonly advanceWhen?: readonly TourCondition[];
  /**
   * What the stall-guard «التالي» clicks on the user's behalf when the
   * expected transition never happened. Absent ⇒ the button just advances.
   */
  readonly fallbackClick?: TourAnchorId;
  /** Fired once when this step becomes active. */
  readonly onEnter?: TourEnterAction;
}

// ---------------------------------------------------------------------------
// The script — 5 steps across 4 acts
//
// Two of the five ask for a click (open the مخرج · open a reference); the rest
// read and advance on «التالي». Anything a step could only *say* rather than
// *show* was folded into the body of a step that shows something.
// ---------------------------------------------------------------------------

const ACT_CHAT = "طبقة المحادثة";
const ACT_WI = "طبقة المخرج";
const ACT_REFS = "المراجع";
const ACT_WORKSPACE = "مساحة العمل";

export const TOUR_STEPS: readonly TourStep[] = [
  // --- Act 1 — طبقة المحادثة (1) ------------------------------------------
  // The old opener spotlighted the thread itself to say «لا جديد هنا» — a whole
  // step spent on the one surface the reader already knows. Cut; this step now
  // carries its point in its first sentence.
  {
    key: "artifact-chip",
    act: ACT_CHAT,
    anchors: ["artifact-chip"],
    stage: "chat",
    title: "الرد مقتطف — والتحليل كامل هنا",
    body: "المحادثة تعرف كيف تستخدمها. الجديد يبدأ من هنا: ما قرأته بالأعلى ملخص مختصر، والتحليل الكامل بمصادره ومراجعه محفوظ كبطاقة مستقلة نسمّيها «مخرج».",
    cta: "جرّب اضغط الزر.",
    advanceWhen: [{ kind: "store", beat: "wi-open" }],
    fallbackClick: "artifact-chip",
  },

  // --- Act 2 — طبقة المخرج (1) --------------------------------------------
  // Absorbed here: the WI-1 badge step (one sentence) and the two-pane-exits
  // step (dropped — «←» and «X» are self-evident, and the last step returns to
  // the list by itself).
  {
    key: "wi-body",
    act: ACT_WI,
    anchors: ["wi-body"],
    stage: "workspace",
    title: "هذا هو المخرج",
    body: "أطول من الرد، ومقسوم بعناوين، وكل رقم بين قوسين داخل النص مرجع حقيقي تستطيع فتحه. ولكل مخرج اسم — WI-1 — وريحان ينادي عليه بنفس الاسم داخل المحادثة.",
  },

  // --- Act 3 — المراجع (2) ------------------------------------------------
  {
    key: "citation-6",
    act: ACT_REFS,
    anchors: ["citation-6"],
    stage: "workspace",
    title: "كل رقم في النص مرجع",
    body: "تفتح المرجع من الرقم نفسه داخل النص، أو من قائمة المراجع أسفل البطاقة.",
    cta: "جرّب اضغط [6].",
    // Two conditions on purpose. The store beat covers a citation click that
    // goes through `openWorkspaceItemAtReference`; the DOM beat covers the
    // in-body marker, which is wired to AgentSearchViewer's LOCAL state and
    // never touches the store — its only observable effect is this dialog.
    advanceWhen: [
      { kind: "store", beat: "reference-6" },
      { kind: "dom", beat: "source-dialog-open" },
    ],
    fallbackClick: "citation-6",
  },
  {
    key: "source-body",
    act: ACT_REFS,
    anchors: ["source-dialog"],
    stage: "workspace",
    title: "المصدر نفسه، بلا إعادة صياغة",
    // Absorbed here: الإحالات, the two exits, and the four source domains — as
    // one sentence each, and deliberately WITHOUT naming a document type. The
    // in-app button's label is derived from the document type (النظام /
    // اللائحة / الدليل / الحكم — `referenceDefiniteType` in ReferencePanel), so
    // naming one here would be contradicted by the screen.
    body: "هنا يظهر القسم الذي استرجعه ريحان من النظام كما هو — وإذا كان المرجع حكمًا قضائيًا يظهر ملخّص الحكم. ومن البطاقة نفسها تفتح «الإحالات» — المواد الأخرى التي يشير إليها النص — أو تنتقل إلى المصدر الرسمي عند الجهة المُصدِرة، أو إلى الوثيقة كاملة داخل مكتبة ريحان.",
  },

  // --- Act 4 — مساحة العمل (1) --------------------------------------------
  // The old script spent a step asking the user to press «←» just to reach this
  // one, and another on مشاركة/حفظ كمدونة — both disabled in the demo. The back
  // trip is now an onEnter side effect and the publish step is gone.
  {
    key: "workspace-add",
    act: ACT_WORKSPACE,
    anchors: ["workspace-add"],
    stage: "workspace",
    title: "مساحة العمل",
    body: "هنا تتجمّع مخرجات المحادثة كلها — نتائج البحث، المسودات، الملفات. ومن «+» تضيف ملاحظة أو ترفع ملفًا ليعمل عليه ريحان معك.",
    // The «+» lives in the item LIST, under the open WI and under the reveal
    // dialog Act 3 ended in. Entering this step has to undo both, or it
    // spotlights a covered node.
    onEnter: "return-to-item-list",
  },
] as const;

export const TOUR_STEP_COUNT = TOUR_STEPS.length;

// ---------------------------------------------------------------------------
// Chrome copy
// ---------------------------------------------------------------------------

export const TOUR_UI = {
  /** Accessible name of the coach-mark card. */
  ariaLabel: "جولة المخرجات",
  skip: "تخطّي الجولة",
  next: "التالي",
  finish: "تمام، فهمت",
  /** Rendered as «٣ / ٥» style progress — Western digits, tabular. */
  progress: (current: number, total: number) => `${current} / ${total}`,
  /**
   * Shown instead of a spotlight when the step's anchor is nowhere on screen
   * (wrong layout, item closed by hand, list not rendered). Never a dead end:
   * «التالي» is revealed with it.
   */
  anchorMissing: "لم نجد هذا العنصر على الشاشة الآن — تقدر تكمل الجولة.",
} as const;

/**
 * How long a click-driven step waits for its transition before revealing a
 * «التالي» that performs the transition itself (§5.3). A tour escapable only
 * by reloading is worse than no tour.
 */
export const TOUR_STALL_MS = 8_000;

/**
 * Shorter fuse for the same button when the anchor cannot even be found —
 * there is nothing for the user to click, so waiting the full stall budget
 * only looks broken.
 */
export const TOUR_MISSING_ANCHOR_MS = 2_000;
