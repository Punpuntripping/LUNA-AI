import {
  BookMarked,
  Files,
  Gauge,
  Gavel,
  Pin,
  Quote,
  ShieldCheck,
  Telescope,
  type LucideIcon,
} from "lucide-react";
import { useUsageDialogStore } from "@/stores/usage-dialog-store";
import { useConversationSettingsDialogStore } from "@/stores/conversation-settings-dialog-store";
import { useChatStore } from "@/stores/chat-store";

/**
 * «سلسلة تعلّم ريحان» — the ORDERED syllabus.
 *
 * Design: `.claude/plans/edu_series.md`. One lesson every `EDU_CADENCE` user
 * messages, in this order, once each, non-blocking.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * LESSON ZERO IS NOT IN THIS FILE.
 *
 * The ladder starts with المحادثة التجريبية + «جولة المخرجات», which already
 * ship at signup (migrations 127/128). They are the first rung and this syllabus
 * is written assuming they have run — which is why there is no `workspace`
 * lesson here. Coach-marking مساحة العمل at turn 0 and then teaching it again at
 * turn 12 is precisely the overwhelm this series exists to avoid.
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * ADDING A LESSON is a registry entry and nothing else — no engine change. The
 * engine finds "the first entry whose `seen` flag is false", so appending is
 * always safe and inserting mid-list is safe too (a user who has seen 1 and 3
 * gets 2 next; nobody is re-taught and nobody skips).
 *
 * Each `id` maps to the flat preference key `edu_<id>`. Those keys are FLAT and
 * must stay flat — `merge_preferences` is a SHALLOW merge server-side, so a
 * nested map written by one tab would clobber the sibling keys another tab
 * wrote ([[project_edu_popups]]).
 */

/** Union grows by one entry per lesson. Also the `edu_<id>` key suffix. */
export type EduLessonId =
  | "usage_limits"
  | "templates"
  | "citations"
  | "deep_search"
  | "save_memo"
  | "privacy_masking"
  | "library"
  | "judgments";

/** Live-data widgets a lesson can embed. Rendered by `EduLessonCard`. */
export type EduLessonSlot = "usage_bar";

export interface EduLesson {
  id: EduLessonId;
  icon: LucideIcon;
  title: string;
  /** 2–3 short lines. Kept as an array so the card controls spacing. */
  body: string[];
  /** Optional live-data widget rendered between body and buttons. */
  slot?: EduLessonSlot;
  /** In-app action — opens a dialog, toggles a pane, fills the composer. */
  action?: { label: string; run: () => void };
  /** A `/learn` route. Opens in a new tab so the lesson never costs the user
   *  their place in the conversation. */
  learnMore?: { label: string; href: string };
  /** Hold this long after the trigger before appearing, so the card does not
   *  flash in while the answer is still settling. */
  delayMs?: number;
}

export const EDU_SYLLABUS: readonly EduLesson[] = [
  {
    id: "usage_limits",
    icon: Gauge,
    title: "حدود الاستخدام",
    body: [
      "ريحان يحاسبك بالنقطة لا بعدد الرسائل: السؤال العام جزء من نقطة، صياغة المستند نقطة واحدة، والبحث المعمّق ٣–٥ نقاط.",
      "هذا استهلاكك حتى الآن — وتجده كاملاً في أي وقت من «الإعدادات ← حدود الاستخدام».",
    ],
    slot: "usage_bar",
    action: {
      label: "افتح حدود الاستخدام",
      run: () => useUsageDialogStore.getState().open(),
    },
    learnMore: {
      label: "سياسة حد الاستخدام",
      href: "/learn/usage-limits",
    },
    delayMs: 1200,
  },
  {
    id: "templates",
    icon: Files,
    title: "قوالبي",
    // The point of قوالبي is NOT "a place to keep your forms" — it is that the
    // writer drafts INTO them. `writer_planner` receives the user's template
    // titles as a passive `<my_templates>` block, picks one by its `TPL-{n}`
    // alias on its PlannerDecision, and the runner resolves that to the body at
    // package-build time (see agents/writer_planner/deps.py). The user never
    // fetches or attaches anything. Copy that describes a clipboard undersells
    // the one thing that makes this worth a lesson.
    body: [
      "احفظ صيغتك في «قوالبي» مرة واحدة — وحين تطلب من ريحان صياغة مستند، يختار القالب المناسب من قوالبك ويكتب المسودة داخل تنسيقك أنت.",
      "لا تحتاج إلى إرفاقه في كل مرة: ريحان يرى عناوين قوالبك، ويذكر لك أيّها سيستخدم في الخطة قبل أن يبدأ الكتابة.",
      "وبعد كل مستند يصيغه، يعرض عليك حفظه كقالب جديد بضغطة واحدة.",
    ],
    // No action button on purpose. Two dead ends were considered and rejected:
    // the «+» menu is local state inside `ChatInput` with no programmatic
    // opener, and `CreateTemplateDialog` (via `sidebar-store`) is only mounted
    // under /templates, so setting its flag from /chat would open nothing.
    //
    // ⚠ The link goes to `/templates/mine`, NOT `/templates`. The latter is an
    // empty-state landing whose own primary button just pushes to /templates/mine
    // — sending the user there costs them an extra click to reach the collection
    // this lesson is about.
    learnMore: { label: "افتح قوالبي", href: "/templates/mine" },
    delayMs: 1200,
  },
  {
    id: "citations",
    icon: Quote,
    title: "المراجع قابلة للنقر",
    body: [
      "الأرقام بين قوسين داخل إجابات ريحان ليست زينة — كل رقم يفتح المادة أو الحكم الذي بُنيت عليه تلك الجملة.",
      "اضغط أي رقم لتقرأ المصدر بنفسك قبل أن تعتمد الإجابة.",
    ],
    delayMs: 1200,
  },
  {
    id: "deep_search",
    icon: Telescope,
    title: "البحث المعمّق",
    body: [
      "بعض الأسئلة تستدعي بحثاً معمّقاً يمرّ على آلاف المواد — يستغرق دقائق، وتظهر مراحله أمامك أولاً بأول.",
      "الانتظار مقصود: النتيجة مبنيّة على مصادر مقروءة، لا على تخمين سريع.",
    ],
    learnMore: { label: "كيف يعمل ريحان", href: "/learn/how-it-works" },
    delayMs: 1200,
  },
  {
    id: "save_memo",
    icon: Pin,
    title: "ثبّت معلومة للجلسة",
    body: [
      "اطلب من ريحان «احفظ هذه المعلومة» فتُضاف إلى مساحة العمل ويصلها كل وكيل يعمل على أسئلتك بقية الجلسة.",
      "مفيدة لتفاصيل قضيتك التي لا تريد إعادة كتابتها في كل رسالة.",
    ],
    action: {
      label: "جرّبها الآن",
      // Fills the composer only — never sends. Same contract as the onboarding
      // starter questions (`STEP_QUESTIONS`).
      run: () =>
        useChatStore
          .getState()
          .injectComposerText("احفظ هذه المعلومة: "),
    },
    delayMs: 1200,
  },
  {
    id: "privacy_masking",
    icon: ShieldCheck,
    title: "وضع السرية",
    body: [
      "عند تفعيله تُقنَّع الأرقام والأسماء والبريد الإلكتروني في رسالتك قبل أن تصل إلى أي نموذج خارجي، ثم تعود كما هي في الإجابة.",
      "مفعّل افتراضياً، ويمكنك التحكم به من إعدادات المحادثة.",
    ],
    action: {
      label: "إعدادات المحادثة",
      run: () => useConversationSettingsDialogStore.getState().open(),
    },
    learnMore: { label: "حماية بياناتك", href: "/learn/data-protection" },
    delayMs: 1200,
  },
  {
    id: "library",
    icon: BookMarked,
    title: "المكتبة القانونية",
    body: [
      "الأنظمة واللوائح والتعاميم التي يبحث فيها ريحان متاحة لك مباشرة — تتصفّحها وتقرأ موادها بنفسك.",
      "وما تفتحه منها يُحفظ في «مكتبتي» للرجوع إليه لاحقاً.",
    ],
    learnMore: { label: "تصفّح المكتبة", href: "/library" },
    delayMs: 1200,
  },
  {
    id: "judgments",
    icon: Gavel,
    title: "الأحكام القضائية",
    body: [
      "آلاف الأحكام الصادرة عن المحاكم السعودية، مبوّبة حسب المحكمة والموضوع.",
      "ولكل حكم «ملخص ريحان» يختصر الوقائع والحيثيات ومنطوق الحكم قبل أن تقرأه كاملاً.",
    ],
    learnMore: { label: "تصفّح الأحكام", href: "/judgments" },
    delayMs: 1200,
  },
] as const;

export function findLesson(id: EduLessonId): EduLesson | undefined {
  return EDU_SYLLABUS.find((lesson) => lesson.id === id);
}
