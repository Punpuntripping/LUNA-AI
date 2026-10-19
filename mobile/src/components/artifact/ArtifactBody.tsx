import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { router } from "expo-router";
import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";

import { appendReferencesForCopy, type Reference, type WorkspaceFeedback, type WorkspaceItem } from "@contracts";

import { errorMessage } from "@/api/errors";
import { AppText } from "@/components/AppText";
import { Markdown } from "@/components/markdown/Markdown";
import { chunkMarkdown } from "@/components/source/chunk-markdown";
import { sourceHref } from "@/components/source/exits";
import { formatDateTime } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";

import { barSpec, DEMO_DISABLED_HINT, type AgentOutputKind } from "./action-bar";
import { ActionChip } from "./ActionChip";
import { AgentOutputDisclaimer } from "./Disclaimer";
import { DraftEditor } from "./DraftEditor";
import { PublishSheet, type PublishFlow } from "./PublishSheet";
import { ReferenceCard } from "./ReferenceCard";
import { isAgentLocked, useReferences, useWorkspaceItem } from "./use-artifact";
import { useDraft, useSetFeedback } from "./use-output-actions";
import { ACTION_BAR_SPACE, WorkspaceItemActionBar } from "./WorkspaceItemActionBar";

type Row =
  | { kind: "md"; key: string; text: string }
  | { kind: "refs-heading"; key: string }
  | { kind: "refs-state"; key: string }
  | { kind: "ref"; key: string; reference: Reference }
  | { kind: "disclaimer"; key: string };

/** Scroll position to come back to: the first visible row + how far into it. */
export interface ScrollAnchor {
  index: number;
  delta: number;
}

export interface ArtifactBodyProps {
  itemId: string;
  bottomInset: number;
  /** The shared demo conversation: 👍/👎 hidden, مشاركة / حفظ كمدونة disabled. */
  isDemo: boolean;
  /** Where to start (the user is back from the reader); read on mount only. */
  restore?: ScrollAnchor | null;
  /** Called with the current scroll anchor right before the reader is pushed
   *  (the sheet hides while the reader is up and comes back — ArtifactSheet). */
  onLeave: (anchor: ScrollAnchor | null) => void;
}

function isAgentKind(kind: WorkspaceItem["kind"]): kind is AgentOutputKind {
  return kind === "agent_search" || kind === "agent_writing";
}

/**
 * The viewer inside the sheet. `agent_search` / `agent_writing`: markdown body
 * with tappable `[n]` → reader, the reference cards (free list), the legal
 * disclaimer, and the floating action bar (WorkspaceItemActionBar — the web's
 * one standard). Anything else (never auto-opened, but reachable) renders its
 * markdown with a نسخ-only bar (web: convo_context / references).
 */
export function ArtifactBody({ itemId, bottomInset, isDemo, restore = null, onLeave }: ArtifactBodyProps) {
  const { colours } = useTheme();
  const item = useWorkspaceItem(itemId);

  if (item.isPending) {
    return (
      <View style={styles.centre}>
        <ActivityIndicator color={colours.primary} />
        <AppText variant="sm" tone="muted">
          جارٍ تحميل العنصر…
        </AppText>
      </View>
    );
  }
  if (item.isError || !item.data) {
    return (
      <View style={styles.centre}>
        <AppText variant="base" weight={600}>
          تعذّر تحميل العنصر
        </AppText>
        <AppText variant="sm" tone="muted" align="center">
          {errorMessage(item.error)}
        </AppText>
        <ActionChip label="إعادة المحاولة" onPress={() => void item.refetch()} />
      </View>
    );
  }

  const data = item.data;
  const common = { bottomInset, restore, onLeave };
  if (data.kind === "agent_writing") return <WritingOutput key={data.item_id} item={data} isDemo={isDemo} {...common} />;
  if (isAgentKind(data.kind)) return <SearchOutput key={data.item_id} item={data} isDemo={isDemo} {...common} />;
  return <PlainOutput item={data} {...common} />;
}

interface OutputProps {
  item: WorkspaceItem;
  isDemo: boolean;
  bottomInset: number;
  restore: ScrollAnchor | null;
  onLeave: (anchor: ScrollAnchor | null) => void;
}

/** References + publish sheet + 👍/👎 — what both agent outputs share. */
function useAgentOutput(item: WorkspaceItem, isDemo: boolean, onLeave: (a: ScrollAnchor | null) => void) {
  const refs = useReferences(item.item_id, true);
  const references = useMemo(() => refs.data ?? [], [refs.data]);
  const { isPending, isError, refetch } = refs;
  const refsState = useMemo<RefsState>(
    () => ({ list: references, pending: isPending, error: isError, retry: () => void refetch() }),
    [references, isPending, isError, refetch],
  );
  const setFeedback = useSetFeedback();
  const [publish, setPublish] = useState<PublishFlow | null>(null);
  const spec = barSpec(item.kind as AgentOutputKind, isDemo);

  const openSource = useCallback(
    (n: number, anchor: ScrollAnchor | null) => {
      onLeave(anchor);
      router.push(sourceHref(item.item_id, n));
    },
    [item.item_id, onLeave],
  );

  // Omitting `onFeedback` is what hides the thumbs (the bar keys off it).
  const barProps = {
    onShare: () => setPublish("share"),
    onSaveBlog: () => setPublish("saveBlog"),
    publishDisabledHint: spec.publishDisabled ? DEMO_DISABLED_HINT : undefined,
    feedback: spec.feedback ? item.feedback : undefined,
    onFeedback: spec.feedback
      ? (next: WorkspaceFeedback) => setFeedback.mutate({ itemId: item.item_id, feedback: next })
      : undefined,
    feedbackPending: setFeedback.isPending,
  };

  const publishSheet = <PublishSheet itemId={item.item_id} flow={publish} onClose={() => setPublish(null)} />;
  return { refsState, references, openSource, barProps, publishSheet };
}

function SearchOutput({ item, isDemo, bottomInset, restore, onLeave }: OutputProps) {
  const { refsState, references, openSource, barProps, publishSheet } = useAgentOutput(item, isDemo, onLeave);
  const content = item.content_md ?? "";
  // نسخ copies the body PLUS «المراجع» (the [n] markers resolve in the paste).
  const copyText = useMemo(() => appendReferencesForCopy(content, references), [content, references]);

  return (
    <View style={styles.root}>
      <PreviewList
        content={content}
        refs={refsState}
        bottomInset={bottomInset}
        restore={restore}
        onOpenSource={openSource}
      />
      <WorkspaceItemActionBar copyText={copyText} exportTitle={item.title} bottomInset={bottomInset} {...barProps} />
      {publishSheet}
    </View>
  );
}

function WritingOutput({ item, isDemo, bottomInset, restore, onLeave }: OutputProps) {
  const { colours } = useTheme();
  const { refsState, references, openSource, barProps, publishSheet } = useAgentOutput(item, isDemo, onLeave);
  const locked = isAgentLocked(item);
  const draft = useDraft(item, locked);
  // LIVE content (not the last saved body), so an edit copies at once.
  const copyText = useMemo(() => appendReferencesForCopy(draft.content, references), [draft.content, references]);

  return (
    <View style={styles.root}>
      {locked ? (
        <View style={[styles.banner, { backgroundColor: colours.warningBg }]} accessibilityRole="alert">
          <AppText variant="sm" weight={600} style={{ color: colours.warningFg }}>
            ريحان يحرر هذا الملف الآن…
          </AppText>
        </View>
      ) : draft.conflict ? (
        <View style={[styles.banner, styles.bannerRow, { backgroundColor: colours.errorBg }]} accessibilityRole="alert">
          <AppText variant="sm" style={[styles.flex, { color: colours.errorFg }]}>
            {draft.conflict}
          </AppText>
          <Pressable accessibilityRole="button" accessibilityLabel="إخفاء" onPress={draft.clearConflict} style={styles.bannerAction}>
            <AppText variant="sm" weight={600} style={{ color: colours.errorFg, textDecorationLine: "underline" }}>
              إخفاء
            </AppText>
          </Pressable>
        </View>
      ) : null}
      <SaveStatus saving={draft.saving} savedAt={draft.savedAt} updatedAt={item.updated_at} />

      {draft.mode === "edit" ? (
        <DraftEditor
          value={draft.content}
          onChangeText={draft.setContent}
          readOnly={locked}
          bottomInset={bottomInset}
          footer={<ReferencesFooter refs={refsState} onOpenSource={(n) => openSource(n, null)} />}
        />
      ) : (
        <PreviewList content={draft.content} refs={refsState} bottomInset={bottomInset} restore={restore} onOpenSource={openSource} />
      )}

      <WorkspaceItemActionBar
        copyText={copyText}
        exportTitle={item.title}
        mode={draft.mode}
        onModeChange={draft.setMode}
        editDisabled={locked}
        bottomInset={bottomInset}
        {...barProps}
      />
      {publishSheet}
    </View>
  );
}

/** Non-agent items (reachable, never auto-opened): markdown + نسخ only. */
function PlainOutput({ item, bottomInset, restore }: Omit<OutputProps, "isDemo">) {
  const content = item.content_md ?? "";
  return (
    <View style={styles.root}>
      <PreviewList content={content} refs={null} bottomInset={bottomInset} restore={restore} />
      <WorkspaceItemActionBar copyText={content} bottomInset={bottomInset} />
    </View>
  );
}

/** «جارٍ الحفظ» / «تم الحفظ تلقائياً» / «التغييرات تُحفظ تلقائياً» + «آخر تحديث». */
function SaveStatus({ saving, savedAt, updatedAt }: { saving: boolean; savedAt: number | null; updatedAt: string }) {
  const { colours } = useTheme();
  const updated = formatDateTime(updatedAt);
  return (
    <View style={[styles.status, { borderBottomColor: colours.border }]}>
      <View style={styles.statusStart}>
        {saving ? <ActivityIndicator size="small" color={colours.textMuted} /> : null}
        <AppText variant="xs" tone="muted">
          {saving ? "جارٍ الحفظ" : savedAt ? "تم الحفظ تلقائياً" : "التغييرات تُحفظ تلقائياً"}
        </AppText>
      </View>
      {updated ? (
        <AppText variant="xs" tone="muted">
          آخر تحديث: {updated}
        </AppText>
      ) : null}
    </View>
  );
}

interface RefsState {
  list: Reference[];
  pending: boolean;
  error: boolean;
  retry: () => void;
}

/** المراجع + disclaimer as plain views (under the editor). */
function ReferencesFooter({ refs, onOpenSource }: { refs: RefsState; onOpenSource: (n: number) => void }) {
  const rows = buildRows("", refs);
  return (
    <View>
      {rows.map((row) => (
        <View key={row.key}>{renderRow(row, refs, onOpenSource)}</View>
      ))}
    </View>
  );
}

function buildRows(content: string, refs: RefsState | null): Row[] {
  const out: Row[] = chunkMarkdown(content).map((text, i) => ({ kind: "md", key: `md${i}`, text }));
  if (!refs) return out;
  if (refs.pending || refs.error || refs.list.length > 0) out.push({ kind: "refs-heading", key: "refs-heading" });
  if (refs.pending || refs.error) out.push({ kind: "refs-state", key: "refs-state" });
  for (const r of [...refs.list].sort((a, b) => a.n - b.n)) out.push({ kind: "ref", key: `ref${r.n}`, reference: r });
  out.push({ kind: "disclaimer", key: "disclaimer" });
  return out;
}

function renderRow(row: Row, refs: RefsState | null, onOpenSource?: (n: number) => void): ReactNode {
  switch (row.kind) {
    case "md":
      return (
        <View style={styles.block}>
          <Markdown content={row.text} variant="reader" onCitationPress={onOpenSource} />
        </View>
      );
    case "refs-heading":
      return (
        <AppText variant="lg" weight={600} accessibilityRole="header" style={styles.refsHeading}>
          المراجع
        </AppText>
      );
    case "refs-state":
      return <RefsStatus refs={refs} />;
    case "ref":
      return (
        <View style={styles.block}>
          <ReferenceCard reference={row.reference} onViewSource={() => onOpenSource?.(row.reference.n)} />
        </View>
      );
    case "disclaimer":
      return <AgentOutputDisclaimer />;
  }
}

function RefsStatus({ refs }: { refs: RefsState | null }) {
  const { colours } = useTheme();
  if (refs?.error) {
    return (
      <View style={styles.inlineState}>
        <AppText variant="sm" tone="muted">
          تعذّر تحميل المراجع
        </AppText>
        <ActionChip label="إعادة المحاولة" variant="ghost" onPress={refs.retry} />
      </View>
    );
  }
  return (
    <View style={styles.inlineState}>
      <ActivityIndicator size="small" color={colours.primary} />
      <AppText variant="sm" tone="muted">
        جارٍ تحميل المراجع…
      </AppText>
    </View>
  );
}

interface PreviewListProps {
  content: string;
  /** `null` = not an agent output (no المراجع / disclaimer / `[n]`). */
  refs: RefsState | null;
  bottomInset: number;
  restore: ScrollAnchor | null;
  /** `[n]` / «عرض المصدر» → reader, with where the list was. */
  onOpenSource?: (n: number, anchor: ScrollAnchor | null) => void;
}

/** The rendered read: markdown blocks + المراجع + disclaimer in one FlashList
 *  (long drafts stay smooth), padded so its end clears the action bar. */
function PreviewList({ content, refs, bottomInset, restore, onOpenSource }: PreviewListProps) {
  const listRef = useRef<FlashListRef<Row>>(null);
  // Mount-time value only: a later prop change must not re-scroll the list.
  const [initialAnchor] = useState(restore);
  const rows = useMemo(() => buildRows(content, refs), [content, refs]);

  const open = useMemo(
    () => (onOpenSource ? (n: number) => onOpenSource(n, readAnchor(listRef.current)) : undefined),
    [onOpenSource],
  );

  // Back from the reader: start at the row the user left from, then nudge to
  // the exact pixel once that row is measured.
  const onLoad = useCallback(() => {
    const list = listRef.current;
    if (!list || !initialAnchor || initialAnchor.delta <= 0) return;
    const layout = list.getLayout(initialAnchor.index);
    if (!layout) return;
    list.scrollToOffset({ offset: list.getFirstItemOffset() + layout.y + initialAnchor.delta, animated: false });
  }, [initialAnchor]);

  return (
    <FlashList
      ref={listRef}
      data={rows}
      initialScrollIndex={initialAnchor && initialAnchor.index < rows.length ? initialAnchor.index : undefined}
      onLoad={onLoad}
      keyExtractor={(row) => row.key}
      getItemType={(row) => row.kind}
      renderItem={({ item: row }) => <>{renderRow(row, refs, open)}</>}
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 12, paddingBottom: 24 + ACTION_BAR_SPACE + bottomInset }}
      ListEmptyComponent={
        <AppText variant="sm" tone="muted">
          لا يوجد محتوى بعد.
        </AppText>
      }
    />
  );
}

function readAnchor(list: FlashListRef<Row> | null): ScrollAnchor | null {
  if (!list) return null;
  const index = list.getFirstVisibleIndex();
  if (index < 0) return null;
  const layout = list.getLayout(index);
  const offset = list.getAbsoluteLastScrollOffset();
  const delta = layout ? offset - (list.getFirstItemOffset() + layout.y) : 0;
  return { index, delta: Number.isFinite(delta) && delta > 0 ? delta : 0 };
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  flex: { flex: 1 },
  centre: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10, padding: 24 },
  banner: { paddingHorizontal: 16, paddingVertical: 8 },
  bannerRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  bannerAction: { minHeight: 44, justifyContent: "center", paddingHorizontal: 4 },
  status: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  statusStart: { flexDirection: "row", alignItems: "center", gap: 6 },
  block: { paddingBottom: 12 },
  refsHeading: { marginTop: 12, marginBottom: 8 },
  inlineState: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 8, flexWrap: "wrap" },
});
