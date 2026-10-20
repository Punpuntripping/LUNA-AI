import { FlashList } from "@shopify/flash-list";
import { router } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { WorkspaceItem, WorkspaceItemKind } from "@contracts";

import { errorMessage } from "@/api/errors";
import { useConversationDetail } from "@/chat/use-conversations";
import { ActionSheet, type SheetAction } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { ActionChip } from "@/components/artifact/ActionChip";
import { DEMO_DISABLED_HINT } from "@/components/artifact/action-bar";
import { Icon } from "@/components/Icon";
import { StackHeader } from "@/components/StackHeader";
import { showToast } from "@/stores/toast";
import { useTheme } from "@/theme/ThemeProvider";

import { workspaceItemHref } from "./hrefs";
import { useConversationWorkspace, useCreateWorkspaceItem } from "./use-workspace";
import { WorkspaceCard } from "./WorkspaceCard";
import {
  ADD_ITEM_LABEL,
  EMPTY_LIST_TEXT,
  groupItems,
  hiddenCount,
  toggleHiddenLabel,
  visibleItems,
} from "./workspace-model";

type Row =
  | { type: "toggle"; key: string }
  | { type: "group"; key: string; kind: WorkspaceItemKind; label: string; count: number; collapsed: boolean }
  | { type: "card"; key: string; item: WorkspaceItem };

/**
 * «مساحة العمل» of one conversation (plan 05 §W1) — the web WorkspacePane list
 * mode: items grouped by kind in the web's order (collapsible headings with a
 * count), each card with «WI-n», its kind/subtype pill and age; items the user
 * hid from ريحان are left out behind «عرض العناصر المخفية (n)»; pull to
 * refresh; «إضافة عنصر» (ملاحظة · مراجع) at the bottom — rendered disabled on
 * the demo conversation. The stream's `workspace_item_*` events refresh the
 * list through the shared query key (W4).
 */
export function WorkspaceScreen({ conversationId }: { conversationId: string }) {
  const { colours, radius } = useTheme();
  const insets = useSafeAreaInsets();
  const list = useConversationWorkspace(conversationId);
  const detail = useConversationDetail(conversationId);
  const isDemo = detail.data?.is_demo === true;
  const create = useCreateWorkspaceItem(conversationId);

  const [showHidden, setShowHidden] = useState(false);
  const [collapsed, setCollapsed] = useState<ReadonlySet<WorkspaceItemKind>>(new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const all = useMemo(() => list.data?.items ?? [], [list.data]);
  const hidden = hiddenCount(all);
  const shownItems = useMemo(() => visibleItems(all, showHidden || hidden === 0), [all, showHidden, hidden]);

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    if (hidden > 0) out.push({ type: "toggle", key: "toggle" });
    for (const g of groupItems(shownItems)) {
      const isCollapsed = collapsed.has(g.kind);
      out.push({ type: "group", key: `g-${g.kind}`, kind: g.kind, label: g.label, count: g.items.length, collapsed: isCollapsed });
      if (!isCollapsed) for (const item of g.items) out.push({ type: "card", key: item.item_id, item });
    }
    return out;
  }, [shownItems, collapsed, hidden]);

  const toggleGroup = useCallback((kind: WorkspaceItemKind) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  const openItem = useCallback((item: WorkspaceItem) => router.push(workspaceItemHref(item.item_id)), []);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await list.refetch();
    } finally {
      setRefreshing(false);
    }
  }, [list]);

  const add = (kind: "note" | "references") =>
    create.mutate(kind, {
      onSuccess: (created) => router.push(workspaceItemHref(created.item_id)),
      onError: (err) => showToast(errorMessage(err)),
    });
  const addActions: SheetAction[] = [
    { key: "note", label: "ملاحظة", icon: "draft", onPress: () => add("note") },
    { key: "references", label: "مراجع (قيد التطوير)", icon: "open", onPress: () => add("references") },
  ];

  const leave = () => {
    if (router.canGoBack()) router.back();
    else router.replace("/");
  };

  const renderItem = ({ item: row }: { item: Row }) => {
    switch (row.type) {
      case "toggle":
        return (
          <Pressable
            testID="wi-toggle-hidden"
            accessibilityRole="button"
            accessibilityState={{ expanded: showHidden }}
            onPress={() => setShowHidden((v) => !v)}
            style={({ pressed }) => [styles.toggle, { backgroundColor: pressed ? colours.surface2 : "transparent", borderRadius: radius.md }]}
          >
            <Icon name={showHidden ? "hide" : "preview"} size={16} colour={colours.primary} />
            <AppText variant="sm" tone="link">
              {toggleHiddenLabel(showHidden, hidden)}
            </AppText>
          </Pressable>
        );
      case "group":
        return (
          <Pressable
            testID={`wi-group-${row.kind}`}
            accessibilityRole="button"
            accessibilityState={{ expanded: !row.collapsed }}
            accessibilityLabel={`${row.label} (${row.count})`}
            onPress={() => toggleGroup(row.kind)}
            style={({ pressed }) => [styles.group, { backgroundColor: pressed ? colours.surface2 : "transparent", borderRadius: radius.md }]}
          >
            {/* Collapsed: the chevron points to the inline end (web ChevronLeft in RTL). */}
            <View style={row.collapsed ? styles.chevronCollapsed : undefined}>
              <Icon name="chevronDown" size={14} colour={colours.textMuted} />
            </View>
            <AppText variant="xs" weight={600} tone="muted" style={styles.flex}>
              {row.label}
            </AppText>
            <AppText variant="xs" tone="muted">
              {String(row.count)}
            </AppText>
          </Pressable>
        );
      case "card":
        return (
          <View style={styles.cardWrap}>
            <WorkspaceCard item={row.item} onPress={openItem} />
          </View>
        );
    }
  };

  let content;
  if (list.isPending) {
    content = (
      <View style={styles.centre}>
        <ActivityIndicator color={colours.primary} />
      </View>
    );
  } else if (list.isError && all.length === 0) {
    content = (
      <View style={styles.centre}>
        <AppText variant="sm" tone="muted" align="center">
          {errorMessage(list.error)}
        </AppText>
        <ActionChip label="إعادة المحاولة" onPress={() => void list.refetch()} />
      </View>
    );
  } else {
    content = (
      <FlashList
        data={rows}
        keyExtractor={(row) => row.key}
        getItemType={(row) => row.type}
        renderItem={renderItem}
        contentContainerStyle={styles.listContent}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colours.primary} colors={[colours.primary]} />
        }
        ListEmptyComponent={
          <View style={styles.empty} testID="wi-empty">
            <Icon name="template" size={36} colour={colours.textSubtle} />
            <AppText variant="sm" tone="muted" align="center">
              {EMPTY_LIST_TEXT}
            </AppText>
          </View>
        }
        testID="wi-list"
      />
    );
  }

  return (
    <View style={[styles.root, { backgroundColor: colours.canvas }]}>
      <StackHeader title="مساحة العمل" onBack={leave} />
      <View style={styles.flex}>{content}</View>
      <View style={[styles.footer, { borderTopColor: colours.border, paddingBottom: insets.bottom + 8 }]}>
        <Pressable
          testID="wi-add"
          accessibilityRole="button"
          accessibilityLabel={ADD_ITEM_LABEL}
          accessibilityHint={isDemo ? DEMO_DISABLED_HINT : undefined}
          accessibilityState={{ disabled: isDemo || create.isPending, busy: create.isPending }}
          disabled={create.isPending}
          onPress={() => (isDemo ? showToast(DEMO_DISABLED_HINT) : setAddOpen(true))}
          style={({ pressed }) => [
            styles.addButton,
            {
              borderColor: colours.borderStrong,
              backgroundColor: pressed ? colours.surface2 : colours.surface1,
              borderRadius: radius.full,
              opacity: isDemo ? 0.45 : 1,
            },
          ]}
        >
          {create.isPending ? (
            <ActivityIndicator size="small" color={colours.textMuted} />
          ) : (
            <Icon name="plus" size={16} colour={colours.textPrimary} />
          )}
          <AppText variant="sm" weight={500}>
            {ADD_ITEM_LABEL}
          </AppText>
        </Pressable>
      </View>
      <ActionSheet
        visible={addOpen}
        onClose={() => setAddOpen(false)}
        title={ADD_ITEM_LABEL}
        actions={addActions}
        testID="wi-add-sheet"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  flex: { flex: 1 },
  centre: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12, padding: 24 },
  listContent: { paddingHorizontal: 8, paddingTop: 8, paddingBottom: 16 },
  toggle: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 44, paddingHorizontal: 8 },
  group: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 40, paddingHorizontal: 8, marginTop: 4 },
  chevronCollapsed: { transform: [{ rotate: "90deg" }] },
  cardWrap: { paddingStart: 8, paddingBottom: 6 },
  empty: { alignItems: "center", gap: 12, paddingVertical: 64, paddingHorizontal: 24 },
  footer: { paddingHorizontal: 8, paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth },
  addButton: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, height: 44, borderWidth: StyleSheet.hairlineWidth },
});
