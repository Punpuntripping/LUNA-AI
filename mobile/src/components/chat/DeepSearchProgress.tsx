import { memo, useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { DeepSearchStage } from "@contracts";

import { DEEP_SEARCH_TOPIC_PREFIX, type DeepSearchSummary } from "@/chat/deep-search-progress";
import { useDeepSearchProgress } from "@/chat/stream-store";
import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { formatCount, formatElapsed, type ArabicPlural } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";

import { PushReadyChip } from "./PushReadyChip";

// M6 — live tracker for a running deep_search (port of the web's
// DeepSearchProgress.tsx, simplified for the phone): note, title + stopwatch,
// a bar that creeps inside the active stage, the 5 steps, the active detail
// line and the result count. The ONLY subscriber of the progress slice, so the
// list never re-renders on progress events; the stopwatch ticks locally.

type RunningStage = Exclude<DeepSearchStage, "done">;

const TITLE = "بحث معمّق";
const NOTE_TITLE = "شكرًا لتجربتك ريحان";
const NOTE_BODY =
  "البحث المعمّق مصمَّم ليمرّ على كل المصادر ذات الصلة بسؤالك، وقد يستغرق من 3 إلى 5 دقائق. " +
  "يمكنك تركه يعمل في الخلفية والانشغال بشيء آخر — ستجد الإجابة بانتظارك هنا.";

const STEPS: readonly { key: RunningStage; label: string }[] = [
  { key: "planning", label: "تخطيط البحث" },
  { key: "searching", label: "البحث في الأنظمة والأحكام" },
  { key: "evaluating", label: "تقييم النتائج وترجيحها" },
  { key: "aggregating", label: "كتابة الإجابة" },
  { key: "writing", label: "عرض الإجابة" },
];
const ORDER: Record<DeepSearchStage, number> = {
  planning: 0,
  searching: 1,
  evaluating: 2,
  aggregating: 3,
  writing: 4,
  done: 5,
};

const RESULT_FORMS: ArabicPlural = { one: "نتيجة واحدة", two: "نتيجتان", few: "نتائج", many: "نتيجة" };
const SOURCE_FORMS: ArabicPlural = { one: "مصدر واحد", two: "مصدران", few: "مصادر", many: "مصدرًا" };

const QUIET_PHRASES: Partial<Record<RunningStage, readonly string[]>> = {
  planning: ["تحليل السؤال…", "تحديد نطاق البحث…", "اختيار المصادر…"],
  evaluating: ["مطابقة النتائج بالأسئلة…", "استبعاد غير المتصل…", "ترجيح الأقوى…"],
  aggregating: ["ربط الأحكام بالمصادر…", "صياغة الإجابة…", "توثيق المراجع…"],
};

// Bar: each stage owns a band; inside it the fill creeps towards the next
// anchor (never reaching it) on the stage's expected duration — same numbers
// as the web.
const ANCHOR: Record<RunningStage, number> = { planning: 5, searching: 10, evaluating: 35, aggregating: 80, writing: 98 };
const TARGET: Record<RunningStage, number> = { planning: 10, searching: 35, evaluating: 80, aggregating: 98, writing: 100 };
const EXPECTED_MS: Record<RunningStage, number> = {
  planning: 30_000,
  searching: 25_000,
  evaluating: 70_000,
  aggregating: 90_000,
  writing: 15_000,
};
const CREEP_K = 2.5;
const CREEP_CAP = 0.94;
const TICK_MS = 1000;
const QUIET_ROTATION_MS = 7000;

function barPercent(stage: RunningStage, inStageMs: number): number {
  const span = TARGET[stage] - ANCHOR[stage];
  const creep = CREEP_CAP * (1 - Math.exp((-CREEP_K * inStageMs) / EXPECTED_MS[stage]));
  return ANCHOR[stage] + span * creep;
}

function stripTopic(line: string): string {
  return line.startsWith(DEEP_SEARCH_TOPIC_PREFIX) ? line.slice(DEEP_SEARCH_TOPIC_PREFIX.length) : line;
}

export const DeepSearchProgress = memo(function DeepSearchProgress({ conversationId }: { conversationId: string }) {
  const { colours, radius } = useTheme();
  const progress = useDeepSearchProgress(conversationId);
  const startedAt = progress?.startedAt ?? null;
  const stage: RunningStage = progress && progress.stage !== "done" ? progress.stage : "writing";

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, [startedAt]);

  const [stageAt, setStageAt] = useState(() => Date.now());
  useEffect(() => setStageAt(Date.now()), [stage, startedAt]);

  const [phrase, setPhrase] = useState(0);
  useEffect(() => {
    setPhrase(0);
    const phrases = QUIET_PHRASES[stage];
    if (!phrases || phrases.length < 2) return;
    const id = setInterval(() => setPhrase((i) => (i + 1) % phrases.length), QUIET_ROTATION_MS);
    return () => clearInterval(id);
  }, [stage]);

  if (!progress) return null;

  const elapsed = Math.max(0, now - progress.startedAt);
  const pct = Math.min(100, Math.max(0, barPercent(stage, Math.max(0, now - stageAt))));
  const quiet = QUIET_PHRASES[stage];
  const detail = progress.text ? stripTopic(progress.text) : quiet ? quiet[phrase % quiet.length] : null;

  return (
    <View style={styles.wrap} testID="deep-search-progress">
      <View style={[styles.note, { borderColor: colours.accentLine, backgroundColor: colours.accentSoft, borderRadius: radius["2xl"] }]}>
        <Icon name="info" size={16} colour={colours.primary} />
        <View style={styles.flex}>
          <AppText variant="xs" weight={600}>
            {NOTE_TITLE}
          </AppText>
          <AppText variant="xs" tone="muted">
            {NOTE_BODY}
          </AppText>
          {/* Web DeepSearchNote: the push opt-in rides on the note while the run is in flight. */}
          {progress.stage !== "done" ? <PushReadyChip /> : null}
        </View>
      </View>

      <View
        accessibilityRole="progressbar"
        accessibilityLabel={`${TITLE} — ${STEPS[ORDER[stage]]?.label ?? ""}`}
        style={[styles.card, { borderColor: colours.border, backgroundColor: colours.surface1, borderRadius: radius["2xl"] }]}
      >
        <View style={styles.head}>
          <Icon name="deepSearch" size={18} colour={colours.primary} />
          <AppText variant="sm" weight={600} style={styles.flex}>
            {TITLE}
          </AppText>
          <AppText variant="xs" tone="muted">
            {formatElapsed(elapsed)}
          </AppText>
        </View>

        <View style={[styles.track, { backgroundColor: colours.surface3, borderRadius: radius.full }]}>
          <View style={[styles.fill, { width: `${pct}%`, backgroundColor: colours.primary, borderRadius: radius.full }]} />
        </View>

        <View style={styles.steps}>
          {STEPS.map((s, i) => {
            const state = i < ORDER[stage] ? "done" : i === ORDER[stage] ? "active" : "pending";
            return (
              <View key={s.key} style={styles.step}>
                <Icon
                  name={state === "done" ? "checkCircle" : "circle"}
                  size={16}
                  colour={state === "pending" ? colours.textSubtle : colours.primary}
                />
                <AppText
                  variant="xs"
                  weight={state === "active" ? 600 : 400}
                  tone={state === "pending" ? "subtle" : state === "active" ? "primary" : "muted"}
                >
                  {s.label}
                </AppText>
              </View>
            );
          })}
        </View>

        {detail ? (
          <AppText variant="xs" tone="muted" numberOfLines={1}>
            {detail}
          </AppText>
        ) : null}
        {progress.sources > 0 ? (
          <AppText variant="xs" tone="muted">
            {formatCount(progress.sources, RESULT_FORMS)}
          </AppText>
        ) : null}
      </View>
    </View>
  );
});

/** Sealed receipt above a finished deep_search answer (session only — absent
 *  after a reload, same as the web chip). Tap = the step log. */
export const DeepSearchSummaryChip = memo(function DeepSearchSummaryChip({ summary }: { summary: DeepSearchSummary }) {
  const { colours, radius } = useTheme();
  const [open, setOpen] = useState(false);
  const parts = [TITLE];
  if (summary.sources > 0) parts.push(formatCount(summary.sources, SOURCE_FORMS));
  parts.push(formatElapsed(summary.elapsedMs));

  return (
    <View style={styles.summary}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={open ? "إخفاء خطوات البحث المعمّق" : "عرض خطوات البحث المعمّق"}
        onPress={() => setOpen((o) => !o)}
        style={[styles.chip, { borderColor: colours.border, backgroundColor: colours.surface2, borderRadius: radius.full }]}
      >
        <Icon name="deepSearch" size={14} colour={colours.textMuted} />
        <AppText variant="xs" tone="muted">
          {parts.join(" · ")}
        </AppText>
        <Icon name="chevronDown" size={14} colour={colours.textMuted} />
      </Pressable>
      {open ? (
        <View style={[styles.log, { borderColor: colours.border, borderRadius: radius.xl }]}>
          {summary.log.length === 0 ? (
            <AppText variant="xs" tone="muted">
              لا توجد تفاصيل إضافية
            </AppText>
          ) : (
            summary.log.map((line, i) => (
              <AppText key={`${i}-${line}`} variant="xs" tone="muted">
                {line}
              </AppText>
            ))
          )}
        </View>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { gap: 10, alignSelf: "stretch" },
  flex: { flex: 1 },
  note: { flexDirection: "row", alignItems: "flex-start", gap: 8, padding: 12, borderWidth: 1 },
  card: { gap: 10, padding: 14, borderWidth: 1 },
  head: { flexDirection: "row", alignItems: "center", gap: 8 },
  track: { height: 6, overflow: "hidden" },
  fill: { height: 6 },
  steps: { gap: 4 },
  step: { flexDirection: "row", alignItems: "center", gap: 8 },
  summary: { alignSelf: "stretch", alignItems: "flex-start", gap: 6, marginBottom: 6 },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, minHeight: 30, borderWidth: 1 },
  log: { alignSelf: "stretch", gap: 4, padding: 10, borderWidth: 1 },
});
