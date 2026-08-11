/**
 * Single source for the «جولة المخرجات» coach-mark script — copy AND the
 * per-step machine contract (anchor ids, what makes the step advance, what the
 * stall-guard button should do). Components render this file and never inline
 * copy, exactly like `components/onboarding/onboarding-content.ts`.
 *
 * REGISTER (deliberate): the reader already uses ChatGPT/Claude fluently. The
 * composer, the bubbles, copy and regenerate are NOT explained anywhere here.
 * The only genuinely new machinery is the WI — the workspace item, its
 * references, and the library behind them — so Act 1 is two steps and Act 3 is
 * five.
 *
 * DATA COUPLING (plan §10 trap 2): steps 6–10 assume the demo conversation's
 * trimmed 10-reference set — `[3]` is a نظام with a library page and ≥1 إحالة,
 * and `[10]` is the compliance card that deliberately lacks the library button.
 * Re-trim the reference set and this copy starts lying.
 */

// ---------------------------------------------------------------------------
// Anchors — the `data-tour="…"` contract with the real components.
// ---------------------------------------------------------------------------

export const TOUR_ANCHOR_IDS = [
  "chat-thread",
  "artifact-chip",
  "citation-3",
  "wi-body",
  "wi-badge",
  "pane-close",
  "pane-back",
  "ref-card-10",
  "source-dialog",
  "ref-crossrefs",
  "ref-exits",
  "wi-action-bar",
  "workspace-add",
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
  /** `openItemId` became null again (back to the item list). */
  | "wi-closed"
  /** `focusedReferenceN === 3`. */
  | "reference-3"
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

/** Side effect performed once, when a step becomes the active one. */
export type TourEnterAction = "close-source-dialog";

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
// The script — 13 steps across 5 acts
// ---------------------------------------------------------------------------

const ACT_CHAT = "طبقة المحادثة";
const ACT_WI = "طبقة المخرج";
const ACT_REFS = "المراجع";
const ACT_PUBLISH = "النشر";
const ACT_WORKSPACE = "مساحة العمل";

export const TOUR_STEPS: readonly TourStep[] = [
  // --- Act 1 — طبقة المحادثة (2) ------------------------------------------
  {
    key: "chat-thread",
    act: ACT_CHAT,
    anchors: ["chat-thread"],
    stage: "chat",
    title: "المحادثة كما تتوقعها",
    body: "تسأل ويجيب — لا جديد هنا. الجديد يبدأ من السطر الذي تحت الرد مباشرة.",
  },
  {
    key: "artifact-chip",
    act: ACT_CHAT,
    anchors: ["artifact-chip"],
    stage: "chat",
    title: "الرد مقتطف — والتحليل كامل هنا",
    body: "ما قرأته بالأعلى ملخص مختصر. التحليل الكامل، بمصادره ومراجعه، محفوظ كبطاقة مستقلة نسمّيها «مخرج».",
    cta: "جرّب اضغط الزر.",
    advanceWhen: [{ kind: "store", beat: "wi-open" }],
    fallbackClick: "artifact-chip",
  },

  // --- Act 2 — طبقة المخرج (3) --------------------------------------------
  {
    key: "wi-body",
    act: ACT_WI,
    anchors: ["wi-body"],
    stage: "workspace",
    title: "هذا هو المخرج",
    body: "أطول من الرد، ومقسوم بعناوين، وكل رقم بين قوسين داخل النص مرجع حقيقي تستطيع فتحه.",
  },
  {
    key: "wi-badge",
    act: ACT_WI,
    anchors: ["wi-badge"],
    stage: "workspace",
    title: "لكل مخرج اسم: WI-1",
    body: "هذا اسم البطاقة، وريحان ينادي عليها بنفس الاسم داخل المحادثة. قل له «اختصر WI-1» وهو يعرف أي بطاقة تقصد.",
  },
  {
    key: "pane-exits",
    act: ACT_WI,
    anchors: ["pane-back", "pane-close"],
    stage: "workspace",
    title: "طريقتان للرجوع",
    body: "«←» يرجّعك لقائمة المخرجات · «X» يقفل اللوحة ويعيدك إلى المحادثة.",
  },

  // --- Act 3 — المراجع (5) ------------------------------------------------
  {
    key: "citation-3",
    act: ACT_REFS,
    anchors: ["citation-3"],
    stage: "workspace",
    title: "كل رقم في النص مرجع",
    body: "تفتح المرجع من الرقم نفسه داخل النص، أو من قائمة المراجع أسفل البطاقة.",
    cta: "جرّب اضغط [3].",
    // Two conditions on purpose. The store beat covers a citation click that
    // goes through `openWorkspaceItemAtReference`; the DOM beat covers the
    // in-body marker, which is wired to AgentSearchViewer's LOCAL state and
    // never touches the store — its only observable effect is this dialog.
    advanceWhen: [
      { kind: "store", beat: "reference-3" },
      { kind: "dom", beat: "source-dialog-open" },
    ],
    fallbackClick: "citation-3",
  },
  {
    key: "source-body",
    act: ACT_REFS,
    anchors: ["source-dialog"],
    stage: "workspace",
    title: "المصدر نفسه، بلا إعادة صياغة",
    body: "هنا يظهر القسم الذي استرجعه ريحان من النظام كما هو. وإذا كان المرجع حكمًا قضائيًا، يظهر ملخّص الحكم.",
  },
  {
    key: "crossrefs",
    act: ACT_REFS,
    anchors: ["ref-crossrefs"],
    stage: "workspace",
    title: "الإحالات",
    body: "المواد الأخرى التي يشير إليها هذا النص، مجموعة لك دون بحث إضافي.",
    cta: "جرّب افتحها.",
    advanceWhen: [{ kind: "dom", beat: "crossrefs-expanded" }],
    fallbackClick: "ref-crossrefs",
  },
  {
    key: "source-exits",
    act: ACT_REFS,
    anchors: ["ref-exits"],
    stage: "workspace",
    title: "من أين تكمل القراءة",
    body: "«فتح المصدر الرسمي» يأخذك لموقع الجهة المُصدِرة · «فتح النظام في ريحان» يفتح الوثيقة كاملة داخل مكتبتنا. كل مرجع موثّق بمصدره الرسمي.",
  },
  {
    key: "ref-domains",
    act: ACT_REFS,
    anchors: ["ref-card-10"],
    stage: "workspace",
    title: "أربعة أنواع من المصادر",
    body: "نظام · قضية · تعميم · خدمة حكومية. كلها تُفتح داخل مكتبة ريحان، ما عدا الخدمات الحكومية — تبقى عند جهتها، ولهذا هذه البطاقة وحدها بلا زر المكتبة.",
    // The card lives in the reference list UNDER the reveal dialog. Entering
    // this step with the dialog still open would spotlight a covered node.
    onEnter: "close-source-dialog",
  },

  // --- Act 4 — النشر (1) --------------------------------------------------
  {
    key: "publish",
    act: ACT_PUBLISH,
    anchors: ["wi-action-bar"],
    stage: "workspace",
    title: "شارك التحليل أو انشره",
    body: "«مشاركة» تعطيك رابطًا يفتح هذا التحليل · «حفظ كمدونة» يحفظه في مدوناتك.",
    note: "الزرّان معطّلان في المحادثة التجريبية — هنا نشرحهما فقط.",
  },

  // --- Act 5 — مساحة العمل (2) --------------------------------------------
  {
    key: "back-to-list",
    act: ACT_WORKSPACE,
    anchors: ["pane-back"],
    stage: "workspace",
    title: "ارجع لقائمة المخرجات",
    body: "كل مخرجات المحادثة مجموعة في قائمة واحدة.",
    cta: "جرّب اضغط «←».",
    advanceWhen: [{ kind: "store", beat: "wi-closed" }],
    fallbackClick: "pane-back",
  },
  {
    key: "workspace-add",
    act: ACT_WORKSPACE,
    anchors: ["workspace-add"],
    stage: "workspace",
    title: "مساحة العمل",
    body: "هنا تتجمّع مخرجات المحادثة كلها — نتائج البحث، المسودات، الملفات. ومن «+» تضيف ملاحظة أو ترفع ملفًا ليعمل عليه ريحان معك.",
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
  /** Rendered as «٣ / ١٣» style progress — Western digits, tabular. */
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
