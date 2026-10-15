import { Redirect } from "expo-router";
import { Profiler, useCallback, useEffect, useRef, useState, type ProfilerOnRenderCallback } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { FIXTURES, longAnswer } from "@/components/markdown/__fixtures__";
import { isEnrichedAvailable } from "@/components/markdown/adapters/EnrichedMarkdown";
import { Markdown, type MarkdownEngine, type MarkdownVariant } from "@/components/markdown/Markdown";
import { Screen } from "@/components/Screen";
import { useThemePreference } from "@/stores/theme-preference";
import { useTheme } from "@/theme/ThemeProvider";

const STREAM_TICK_MS = 80;
const STREAM_CHUNK = 120; // chars per flush (~1.5k chars/s, a fast model)

interface Stats {
  last: number;
  avg: number;
  max: number;
  commits: number;
}

/** K0 spike (dev builds only): the same fixtures through (A) and (B), both
 *  themes, plus a streaming replay of the 30k answer with render timings. */
export default function DevMarkdownScreen() {
  if (!__DEV__) return <Redirect href="/" />;
  return <Spike />;
}

function Spike() {
  const { scheme } = useTheme();
  const setPreference = useThemePreference((s) => s.setPreference);
  const [engine, setEngine] = useState<MarkdownEngine>("B");
  const [variant, setVariant] = useState<MarkdownVariant>("chat");
  const [fixture, setFixture] = useState(FIXTURES[0].key);
  const [streamed, setStreamed] = useState<string | null>(null);
  const [cited, setCited] = useState<number | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const acc = useRef<Stats>({ last: 0, avg: 0, max: 0, commits: 0 });
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopStream = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => stopStream, [stopStream]);

  // Publish profiler stats twice a second (never from inside onRender).
  useEffect(() => {
    const id = setInterval(() => {
      if (acc.current.commits) setStats({ ...acc.current });
    }, 500);
    return () => clearInterval(id);
  }, []);

  const onRender: ProfilerOnRenderCallback = (_id, _phase, actual) => {
    const s = acc.current;
    s.commits += 1;
    s.last = actual;
    s.max = Math.max(s.max, actual);
    s.avg += (actual - s.avg) / s.commits;
  };

  const resetStats = () => {
    acc.current = { last: 0, avg: 0, max: 0, commits: 0 };
    setStats(null);
  };

  const startStream = () => {
    stopStream();
    resetStats();
    let shown = 0;
    setStreamed("");
    timer.current = setInterval(() => {
      shown = Math.min(longAnswer.length, shown + STREAM_CHUNK);
      setStreamed(longAnswer.slice(0, shown));
      if (shown >= longAnswer.length) stopStream();
    }, STREAM_TICK_MS);
  };

  const streaming = streamed !== null && streamed.length < longAnswer.length;
  const content = streamed ?? FIXTURES.find((f) => f.key === fixture)?.content ?? "";

  return (
    <Screen>
      <Segment
        label="المحرّك"
        value={engine}
        options={[
          { value: "A", label: isEnrichedAvailable() ? "A (enriched)" : "A (غير مضمَّن)" },
          { value: "B", label: "B (marked)" },
        ]}
        onChange={(v) => {
          resetStats();
          setEngine(v);
        }}
      />
      <Segment
        label="المظهر"
        value={scheme}
        options={[
          { value: "light", label: "فاتح" },
          { value: "dark", label: "داكن" },
        ]}
        onChange={(v) => setPreference(v)}
      />
      <Segment
        label="النمط"
        value={variant}
        options={[
          { value: "chat", label: "محادثة" },
          { value: "reader", label: "قارئ" },
        ]}
        onChange={setVariant}
      />
      <Segment
        label="المثال"
        value={streamed !== null ? "" : fixture}
        options={FIXTURES.map((f) => ({ value: f.key, label: f.label }))}
        onChange={(v) => {
          stopStream();
          setStreamed(null);
          resetStats();
          setFixture(v);
        }}
      />

      <View style={styles.row}>
        <View style={styles.flex}>
          <Button label={streaming ? "إيقاف" : "بثّ الإجابة الطويلة"} variant="secondary" onPress={streaming ? stopStream : startStream} />
        </View>
      </View>

      <AppText variant="xs" tone="muted">
        {`الأحرف: ${content.length}${stats ? ` · آخر عرض: ${stats.last.toFixed(1)} ms · المتوسط: ${stats.avg.toFixed(1)} ms · الأقصى: ${stats.max.toFixed(1)} ms · مرات العرض: ${stats.commits}` : ""}`}
      </AppText>
      {cited !== null ? <AppText variant="xs" tone="link">{`ضُغط المرجع [${cited}]`}</AppText> : null}

      <Profiler id="markdown" onRender={onRender}>
        <Markdown content={content} engine={engine} variant={variant} streaming={streaming} onCitationPress={setCited} />
      </Profiler>
    </Screen>
  );
}

interface SegmentProps<T extends string> {
  label: string;
  value: T | "";
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}

function Segment<T extends string>({ label, value, options, onChange }: SegmentProps<T>) {
  const { colours, radius } = useTheme();
  return (
    <View style={styles.section}>
      <AppText variant="xs" weight={600} tone="secondary">
        {label}
      </AppText>
      <View style={[styles.segment, { backgroundColor: colours.surface2, borderRadius: radius.default }]}>
        {options.map((opt) => {
          const selected = opt.value === value;
          return (
            <Pressable
              key={opt.value}
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              onPress={() => onChange(opt.value)}
              style={[styles.segmentItem, { borderRadius: radius.md, backgroundColor: selected ? colours.surface1 : "transparent" }]}
            >
              <AppText variant="xs" weight={selected ? 600 : 400} align="center">
                {opt.label}
              </AppText>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 4 },
  segment: { flexDirection: "row", flexWrap: "wrap", padding: 4, gap: 4 },
  segmentItem: { flexGrow: 1, minHeight: 36, paddingVertical: 6, paddingHorizontal: 8, justifyContent: "center" },
  row: { flexDirection: "row", gap: 8 },
  flex: { flex: 1 },
});
