import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { router } from "expo-router";
import { useCallback, useMemo, useRef, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";

import { appendReferencesForCopy, type Reference, type WorkspaceItem } from "@contracts";

import { errorMessage } from "@/api/errors";
import { AppText } from "@/components/AppText";
import { Markdown } from "@/components/markdown/Markdown";
import { chunkMarkdown } from "@/components/source/chunk-markdown";
import { sourceHref } from "@/components/source/exits";
import { useTheme } from "@/theme/ThemeProvider";

import { ActionChip } from "./ActionChip";
import { CopyActions } from "./CopyActions";
import { AgentOutputDisclaimer } from "./Disclaimer";
import { ReferenceCard } from "./ReferenceCard";
import { isAgentLocked, useReferences, useWorkspaceItem } from "./use-artifact";

type Row =
  | { kind: "md"; key: string; text: string }
  | { kind: "refs-heading"; key: string }
  | { kind: "refs-state"; key: string }
  | { kind: "ref"; key: string; reference: Reference }
  | { kind: "disclaimer"; key: string };

const AGENT_KINDS: ReadonlySet<WorkspaceItem["kind"]> = new Set<WorkspaceItem["kind"]>(["agent_search", "agent_writing"]);

/** Scroll position to come back to: the first visible row + how far into it. */
export interface ScrollAnchor {
  index: number;
  delta: number;
}

export interface ArtifactBodyProps {
  itemId: string;
  bottomInset: number;
  /** Where to start (the user is back from the reader); read on mount only. */
  restore?: ScrollAnchor | null;
  /** Called with the current scroll anchor right before the reader is pushed
   *  (the sheet hides while the reader is up and comes back — ArtifactSheet). */
  onLeave: (anchor: ScrollAnchor | null) => void;
}

/**
 * The viewer inside the sheet. `agent_search` / `agent_writing`: markdown body
 * with tappable `[n]` → reader, the reference cards (free list), copy, and the
 * legal disclaimer. Anything else (never auto-opened, but reachable) renders
 * its markdown alone.
 */
export function ArtifactBody({ itemId, bottomInset, restore = null, onLeave }: ArtifactBodyProps) {
  const { colours } = useTheme();
  const item = useWorkspaceItem(itemId);
  const isAgent = item.data ? AGENT_KINDS.has(item.data.kind) : false;
  const refs = useReferences(itemId, isAgent);
  const references = useMemo(() => refs.data ?? [], [refs.data]);

  const listRef = useRef<FlashListRef<Row>>(null);
  // Mount-time value only: a later prop change must not re-scroll the list.
  const [initialAnchor] = useState(restore);

  const openSource = useCallback(
    (n: number) => {
      onLeave(readAnchor(listRef.current));
      router.push(sourceHref(itemId, n));
    },
    [itemId, onLeave],
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

  const content = item.data?.content_md ?? "";
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = chunkMarkdown(content).map((text, i) => ({ kind: "md", key: `md${i}`, text }));
    if (!isAgent) return out;
    if (refs.isPending || refs.isError || references.length > 0) {
      out.push({ kind: "refs-heading", key: "refs-heading" });
    }
    if (refs.isPending || refs.isError) out.push({ kind: "refs-state", key: "refs-state" });
    for (const r of [...references].sort((a, b) => a.n - b.n)) out.push({ kind: "ref", key: `ref${r.n}`, reference: r });
    out.push({ kind: "disclaimer", key: "disclaimer" });
    return out;
  }, [content, isAgent, refs.isPending, refs.isError, references]);

  const copyText = useMemo(() => appendReferencesForCopy(content, references), [content, references]);

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
  const locked = data.kind === "agent_writing" && isAgentLocked(data);

  const renderRow = ({ item: row }: { item: Row }) => {
    switch (row.kind) {
      case "md":
        return (
          <View style={styles.block}>
            <Markdown content={row.text} variant="reader" onCitationPress={isAgent ? openSource : undefined} />
          </View>
        );
      case "refs-heading":
        return (
          <AppText variant="lg" weight={600} accessibilityRole="header" style={styles.refsHeading}>
            المراجع
          </AppText>
        );
      case "refs-state":
        return refs.isError ? (
          <View style={styles.inlineState}>
            <AppText variant="sm" tone="muted">
              تعذّر تحميل المراجع
            </AppText>
            <ActionChip label="إعادة المحاولة" variant="ghost" onPress={() => void refs.refetch()} />
          </View>
        ) : (
          <View style={styles.inlineState}>
            <ActivityIndicator size="small" color={colours.primary} />
            <AppText variant="sm" tone="muted">
              جارٍ تحميل المراجع…
            </AppText>
          </View>
        );
      case "ref":
        return (
          <View style={styles.block}>
            <ReferenceCard reference={row.reference} onViewSource={() => openSource(row.reference.n)} />
          </View>
        );
      case "disclaimer":
        return <AgentOutputDisclaimer />;
    }
  };

  return (
    <View style={styles.root}>
      {locked ? (
        <View style={[styles.lock, { backgroundColor: colours.warningBg }]} accessibilityRole="alert">
          <AppText variant="sm" weight={600} style={{ color: colours.warningFg }}>
            ريحان يحرر هذا الملف الآن…
          </AppText>
        </View>
      ) : null}
      <View style={[styles.toolbar, { borderBottomColor: colours.border }]}>
        <CopyActions text={copyText} />
      </View>
      <FlashList
        ref={listRef}
        data={rows}
        initialScrollIndex={initialAnchor && initialAnchor.index < rows.length ? initialAnchor.index : undefined}
        onLoad={onLoad}
        keyExtractor={(row) => row.key}
        getItemType={(row) => row.kind}
        renderItem={renderRow}
        contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 12, paddingBottom: 24 + bottomInset }}
        ListEmptyComponent={
          <AppText variant="sm" tone="muted">
            لا يوجد محتوى بعد.
          </AppText>
        }
      />
    </View>
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
  centre: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10, padding: 24 },
  toolbar: { paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  lock: { paddingHorizontal: 16, paddingVertical: 8 },
  block: { paddingBottom: 12 },
  refsHeading: { marginTop: 12, marginBottom: 8 },
  inlineState: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 8, flexWrap: "wrap" },
});
