import { router } from "expo-router";
import { useCallback } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { errorMessage } from "@/api/errors";
import { useConversationDetail } from "@/chat/use-conversations";
import { AppText } from "@/components/AppText";
import { ActionChip } from "@/components/artifact/ActionChip";
import { ArtifactBody } from "@/components/artifact/ArtifactBody";
import { useWorkspaceItem } from "@/components/artifact/use-artifact";
import { StackHeader } from "@/components/StackHeader";
import { useTheme } from "@/theme/ThemeProvider";

import { AttachmentViewer } from "./AttachmentViewer";
import { NoteViewer } from "./NoteViewer";
import { TextItemViewer } from "./TextItemViewer";
import { useItemManage } from "./use-item-manage";
import { useFollowListUpdates } from "./use-workspace";
import { WiBadge } from "./WiBadge";
import { ITEM_LOAD_ERROR } from "./workspace-model";

const noop = () => undefined;

/**
 * One workspace item (plan 05 §W2) — the web WorkspacePane detail mode as a
 * stack screen: title + «WI-n» in the header, the kind's viewer below.
 * Agent outputs reuse the chat sheet's viewer (ArtifactBody: markdown, `[n]` →
 * reader, المراجع, disclaimer, the full action bar); notes, attachments,
 * ملخص المحادثة and المراجع have their own viewers. Every bar gets «المزيد»
 * (إخفاء/إظهار · حذف) except on the demo conversation and convo_context.
 */
export function WorkspaceItemScreen({ itemId }: { itemId: string }) {
  const { colours } = useTheme();
  const insets = useSafeAreaInsets();
  const query = useWorkspaceItem(itemId);
  const item = query.data;
  const detail = useConversationDetail(item?.conversation_id ?? undefined);
  // Until the conversation is known, behave as the demo (no إخفاء / حذف).
  const isDemo = item?.conversation_id ? detail.data?.is_demo === true : false;
  const demoForManage = item?.conversation_id ? detail.data === undefined || isDemo : false;

  useFollowListUpdates(item);
  const leave = useCallback(() => {
    if (router.canGoBack()) router.back();
    else router.replace("/");
  }, []);
  const manage = useItemManage(item, demoForManage, leave);

  const header = (
    <StackHeader title={item?.title || "..."} onBack={leave} actions={item ? <WiBadge seq={item.wi_seq} /> : undefined} />
  );

  let body;
  if (query.isPending) {
    body = (
      <View style={styles.centre}>
        <ActivityIndicator color={colours.primary} />
        <AppText variant="sm" tone="muted">
          جارٍ تحميل العنصر...
        </AppText>
      </View>
    );
  } else if (query.isError || !item) {
    body = (
      <View style={styles.centre}>
        <AppText variant="base" weight={600} tone="destructive" align="center">
          {ITEM_LOAD_ERROR}
        </AppText>
        {query.isError ? (
          <AppText variant="sm" tone="muted" align="center">
            {errorMessage(query.error)}
          </AppText>
        ) : null}
        <ActionChip label="إعادة المحاولة" onPress={() => void query.refetch()} />
      </View>
    );
  } else {
    const common = { bottomInset: insets.bottom, manage };
    switch (item.kind) {
      case "agent_search":
      case "agent_writing":
        body = <ArtifactBody itemId={item.item_id} isDemo={isDemo} onLeave={noop} {...common} />;
        break;
      case "note":
        // Keyed: a different note never inherits this one's local edits.
        body = <NoteViewer key={item.item_id} item={item} {...common} />;
        break;
      case "attachment":
        body = <AttachmentViewer key={item.item_id} item={item} {...common} />;
        break;
      case "convo_context":
      case "references":
        body = <TextItemViewer item={item} {...common} />;
        break;
      default:
        body = (
          <View style={styles.centre}>
            <AppText variant="sm" tone="muted">
              نوع غير معروف
            </AppText>
          </View>
        );
    }
  }

  return (
    <View style={[styles.root, { backgroundColor: colours.canvas }]}>
      {header}
      {body}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  centre: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10, padding: 24 },
});
