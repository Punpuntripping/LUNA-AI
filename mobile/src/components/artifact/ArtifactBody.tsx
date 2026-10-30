import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { router } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";

import {
  appendReferencesForCopy,
  isWiPreviewLocked,
  wiPreviewVisibleContent,
  type Reference,
  type WorkspaceFeedback,
  type WorkspaceItem,
} from "@contracts";

import { errorMessage } from "@/api/errors";
import { AppText } from "@/components/AppText";
import { Markdown } from "@/components/markdown/Markdown";
import { chunkMarkdown } from "@/components/source/chunk-markdown";
import { sourceHref } from "@/components/source/exits";
import { WiPreviewFade, WiPreviewHiddenRefs, WiPreviewWall } from "@/components/workspace/WiPreviewWall";
import { lockedRefCount, previewAccess } from "@/components/workspace/workspace-model";
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
import { ACTION_BAR_SPACE, WorkspaceItemActionBar, type ManageActions } from "./WorkspaceItemActionBar";

type Row =
  /** `fade`: the last block of a preview-locked body (the wall follows). */
  | { kind: "md"; key: string; text: string; fade: boolean }
  | { kind: "wall"; key: string }
  | { kind: "refs-heading"; key: string }
  | { kind: "refs-state"; key: string }
  | { kind: "ref"; key: string; reference: Reference }
  | { kind: "refs-hidden"; key: string; count: number }
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
  /** Workspace screen only: the bar's «المزيد» (إخفاء/إظهار · حذف). The
   *  chat's sheet passes none. */
  manage?: ManageActions;
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
 *
 * WI preview lock (the server sent `preview`): only the visible part, faded
 * into WiPreviewWall, read-only (no معاينة/تحرير, مشاركة, حفظ كمدونة or PDF —
 * نسخ copies the visible part); «المراجع» lists what the server returned plus
 * «وN مصادر أخرى في الجزء المقفل».
 */
export function ArtifactBody({ itemId, bottomInset, isDemo, restore = null, onLeave, manage }: ArtifactBodyProps) {
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
  const common = { bottomInset, restore, onLeave, manage };
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
  manage?: ManageActions;
}

/** References + publish sheet + 👍/👎 — what both agent outputs share. */
function useAgentOutput(item: WorkspaceItem, isDemo: boolean, onLeave: (a: ScrollAnchor | null) => void) {
  const access = previewAccess(item);
  const refs = useReferences(item.item_id, true);
  const references = useMemo(() => refs.data?.references ?? [], [refs.data]);
  const hiddenCount = lockedRefCount(item, refs.data?.hidden_count);
  const { isPending, isError, refetch } = refs;
  const refsState = useMemo<RefsState>(
    () => ({ list: references, hiddenCount, pending: isPending, error: isError, retry: () => void refetch() }),
    [references, hiddenCount, isPending, isError, refetch],
  );
  // The lock flipped while open (upgraded, or the plan ended): the server now
  // lists a different set of sources — ask again.
  const lastLocked = useRef(access.locked);
  useEffect(() => {
    if (lastLocked.current === access.locked) return;
    lastLocked.current = access.locked;
    void refetch();
  }, [access.locked, refetch]);
  const setFeedback = useSetFeedback();
  const [publish, setPublish] = useState<PublishFlow | null>(null);
  const spec = barSpec(item.kind as AgentOutputKind, isDemo, access.locked);

  const openSource = useCallback(
    (n: number, anchor: ScrollAnchor | null) => {
      onLeave(anchor);
      router.push(sourceHref(item.item_id, n));
    },
    [item.item_id, onLeave],
  );

  // Omitting a handler is what hides its control (the bar keys off it): no
  // `onFeedback` → no thumbs; no `onShare` / `onSaveBlog` / `exportTitle` on
  // a preview-locked item.
  const barProps = {
    onShare: spec.publish ? () => setPublish("share") : undefined,
    onSaveBlog: spec.publish ? () => setPublish("saveBlog") : undefined,
    publishDisabledHint: spec.publishDisabled ? DEMO_DISABLED_HINT : undefined,
    exportTitle: spec.export ? item.title : undefined,
    feedback: spec.feedback ? item.feedback : undefined,
    onFeedback: spec.feedback
      ? (next: WorkspaceFeedback) => setFeedback.mutate({ itemId: item.item_id, feedback: next })
      : undefined,
    feedbackPending: setFeedback.isPending,
  };

  // Never offered on a preview-locked item. One already open when the lock
  // arrives (the server's 402) stays until closed, to show its Arabic message.
  const publishSheet =
    spec.publish || publish !== null ? (
      <PublishSheet itemId={item.item_id} flow={publish} onClose={() => setPublish(null)} />
    ) : null;
  return { refsState, references, openSource, barProps, publishSheet, previewLocked: access.locked };
}

function SearchOutput({ item, isDemo, bottomInset, restore, onLeave, manage }: OutputProps) {
  const { refsState, references, openSource, barProps, publishSheet, previewLocked } = useAgentOutput(item, isDemo, onLeave);
  const content = wiPreviewVisibleContent(item);
  // نسخ copies the body PLUS «المراجع» (the [n] markers resolve in the paste).
  const copyText = useMemo(() => appendReferencesForCopy(content, references), [content, references]);

  return (
    <View style={styles.root}>
      <PreviewList
        content={content}
        refs={refsState}
        locked={previewLocked}
        bottomInset={bottomInset}
        restore={restore}
        onOpenSource={openSource}
      />
      <WorkspaceItemActionBar copyText={copyText} bottomInset={bottomInset} manage={manage} {...barProps} />
      {publishSheet}
    </View>
  );
}

function WritingOutput({ item, isDemo, bottomInset, restore, onLeave, manage }: OutputProps) {
  const { colours } = useTheme();
  const { refsState, references, openSource, barProps, publishSheet, previewLocked } = useAgentOutput(item, isDemo, onLeave);
  const locked = isAgentLocked(item);
  const draft = useDraft(item, locked, previewLocked);
  // Preview-locked: the visible part, read as a document (no معاينة/تحرير).
  const content = previewLocked ? wiPreviewVisibleContent(item) : draft.content;
  const mode = previewLocked ? "preview" : draft.mode;
  // LIVE content (not the last saved body), so an edit copies at once.
  const copyText = useMemo(() => appendReferencesForCopy(content, references), [content, references]);

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
      <SaveStatus saving={draft.saving} savedAt={draft.savedAt} updatedAt={item.updated_at} readOnly={previewLocked} />

      {mode === "edit" ? (
        <DraftEditor
          value={draft.content}
          onChangeText={draft.setContent}
          readOnly={locked}
          bottomInset={bottomInset}
          footer={<ReferencesFooter refs={refsState} onOpenSource={(n) => openSource(n, null)} />}
        />
      ) : (
        <PreviewList
          content={content}
          refs={refsState}
          locked={previewLocked}
          bottomInset={bottomInset}
          restore={restore}
          onOpenSource={openSource}
        />
      )}

      <WorkspaceItemActionBar
        copyText={copyText}
        mode={previewLocked ? undefined : draft.mode}
        onModeChange={previewLocked ? undefined : draft.setMode}
        editDisabled={locked}
        bottomInset={bottomInset}
        manage={manage}
        {...barProps}
      />
      {publishSheet}
    </View>
  );
}

/** Non-agent items (reachable, never auto-opened): markdown + نسخ only. */
function PlainOutput({ item, bottomInset, restore, manage }: Omit<OutputProps, "isDemo">) {
  const content = wiPreviewVisibleContent(item);
  return (
    <View style={styles.root}>
      <PreviewList content={content} refs={null} locked={isWiPreviewLocked(item)} bottomInset={bottomInset} restore={restore} />
      <WorkspaceItemActionBar copyText={content} bottomInset={bottomInset} manage={manage} />
    </View>
  );
}

/** «جارٍ الحفظ» / «تم الحفظ تلقائياً» / «التغييرات تُحفظ تلقائياً» + «آخر تحديث».
 *  `readOnly` (preview-locked — nothing is ever saved): «آخر تحديث» only. */
function SaveStatus({
  saving,
  savedAt,
  updatedAt,
  readOnly = false,
}: {
  saving: boolean;
  savedAt: number | null;
  updatedAt: string;
  readOnly?: boolean;
}) {
  const { colours } = useTheme();
  const updated = formatDateTime(updatedAt);
  if (readOnly && !updated) return null;
  return (
    <View style={[styles.status, { borderBottomColor: colours.border }]}>
      {readOnly ? null : (
        <View style={styles.statusStart}>
          {saving ? <ActivityIndicator size="small" color={colours.textMuted} /> : null}
          <AppText variant="xs" tone="muted">
            {saving ? "جارٍ الحفظ" : savedAt ? "تم الحفظ تلقائياً" : "التغييرات تُحفظ تلقائياً"}
          </AppText>
        </View>
      )}
      {updated ? (
        <AppText variant="xs" tone="muted" style={styles.shrink}>
          آخر تحديث: {updated}
        </AppText>
      ) : null}
    </View>
  );
}

interface RefsState {
  list: Reference[];
  /** Sources only the hidden part of a preview-locked item cites (0 otherwise). */
  hiddenCount: number;
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

/** `locked` (preview-locked): the last block fades and the wall follows it,
 *  before «المراجع»; the hidden-sources line closes the list once it loaded. */
function buildRows(content: string, refs: RefsState | null, locked = false): Row[] {
  const chunks = chunkMarkdown(content);
  const out: Row[] = chunks.map((text, i) => ({
    kind: "md",
    key: `md${i}`,
    text,
    fade: locked && i === chunks.length - 1,
  }));
  if (locked) out.push({ kind: "wall", key: "wall" });
  if (!refs) return out;
  const settled = !refs.pending && !refs.error;
  const hidden = settled ? refs.hiddenCount : 0;
  if (!settled || refs.list.length > 0 || hidden > 0) out.push({ kind: "refs-heading", key: "refs-heading" });
  if (!settled) out.push({ kind: "refs-state", key: "refs-state" });
  for (const r of [...refs.list].sort((a, b) => a.n - b.n)) out.push({ kind: "ref", key: `ref${r.n}`, reference: r });
  if (hidden > 0) out.push({ kind: "refs-hidden", key: "refs-hidden", count: hidden });
  out.push({ kind: "disclaimer", key: "disclaimer" });
  return out;
}

function renderRow(row: Row, refs: RefsState | null, onOpenSource?: (n: number) => void): ReactNode {
  switch (row.kind) {
    case "md":
      return (
        <View style={styles.block}>
          <Markdown content={row.text} variant="reader" onCitationPress={onOpenSource} />
          {row.fade ? <WiPreviewFade /> : null}
        </View>
      );
    case "wall":
      return (
        <View style={styles.block}>
          <WiPreviewWall />
        </View>
      );
    case "refs-hidden":
      return <WiPreviewHiddenRefs count={row.count} />;
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
  /** Preview-locked: fade + WiPreviewWall after `content` (the visible part). */
  locked?: boolean;
  bottomInset: number;
  restore: ScrollAnchor | null;
  /** `[n]` / «عرض المصدر» → reader, with where the list was. */
  onOpenSource?: (n: number, anchor: ScrollAnchor | null) => void;
}

/** Back from the reader, the restore re-applies while the content is still
 *  being measured, until it stops growing for this long (or the user drags)… */
const RESTORE_SETTLE_MS = 600;
/** …and never for longer than this. */
const RESTORE_MAX_MS = 2_500;

/** The rendered read: markdown blocks + المراجع + disclaimer in one FlashList
 *  (long drafts stay smooth), padded so its end clears the action bar. */
function PreviewList({ content, refs, locked = false, bottomInset, restore, onOpenSource }: PreviewListProps) {
  const listRef = useRef<FlashListRef<Row>>(null);
  // Mount-time value only: a later prop change must not re-scroll the list.
  const [initialAnchor] = useState(restore);
  const rows = useMemo(() => buildRows(content, refs, locked), [content, refs, locked]);
  const rowCount = rows.length;

  const open = useMemo(
    () => (onOpenSource ? (n: number) => onOpenSource(n, readAnchor(listRef.current)) : undefined),
    [onOpenSource],
  );

  // Back from the reader (QA N5/F4): land on the row the user left from, at
  // the same pixel. One scroll at load was not enough — the sheet's content
  // is mounted fresh as the Modal slides in, so that first scroll ran before
  // the native content had its height (Android clamps it to 0) and long
  // markdown rows kept growing after it. So: bring the anchor row into the
  // render window, then re-apply «row top + delta» on every content-size
  // change until it settles; a drag hands control back to the user.
  const restoring = useRef(initialAnchor !== null);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopRestore = useCallback(() => {
    restoring.current = false;
    if (settleTimer.current) clearTimeout(settleTimer.current);
    settleTimer.current = null;
  }, []);
  useEffect(() => {
    if (!restoring.current) return undefined;
    const cap = setTimeout(stopRestore, RESTORE_MAX_MS);
    return () => {
      clearTimeout(cap);
      stopRestore();
    };
  }, [stopRestore]);
  const applyRestore = useCallback(() => {
    const list = listRef.current;
    if (!restoring.current || !list || !initialAnchor) return;
    const layout = list.getLayout(initialAnchor.index);
    if (!layout) return;
    list.scrollToOffset({ offset: list.getFirstItemOffset() + layout.y + initialAnchor.delta, animated: false });
    if (settleTimer.current) clearTimeout(settleTimer.current);
    settleTimer.current = setTimeout(stopRestore, RESTORE_SETTLE_MS);
  }, [initialAnchor, stopRestore]);
  const onLoad = useCallback(() => {
    const list = listRef.current;
    if (!list || !initialAnchor || !restoring.current) return;
    // Its row not there yet (المراجع still loading): content-size changes retry.
    if (initialAnchor.index >= rowCount) return;
    void list.scrollToIndex({ index: initialAnchor.index, animated: false }).then(applyRestore);
  }, [initialAnchor, rowCount, applyRestore]);

  return (
    <FlashList
      ref={listRef}
      data={rows}
      initialScrollIndex={initialAnchor && initialAnchor.index < rows.length ? initialAnchor.index : undefined}
      onLoad={onLoad}
      onContentSizeChange={applyRestore}
      onScrollBeginDrag={stopRestore}
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
    // Large font scales (QA F5): the two halves wrap instead of clipping.
    flexWrap: "wrap",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  statusStart: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 },
  shrink: { flexShrink: 1 },
  block: { paddingBottom: 12 },
  refsHeading: { marginTop: 12, marginBottom: 8 },
  inlineState: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 8, flexWrap: "wrap" },
});
