import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { router } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  View,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  type ScrollViewProps,
  type TextInput,
} from "react-native";
import { KeyboardChatScrollView, KeyboardStickyView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AGENT_OUTPUT_DISCLAIMER_AR, type ConversationSummary } from "@contracts";

import { errorMessage } from "@/api/errors";
import { useSession } from "@/auth/session-store";
import { chatEngine } from "@/chat/chat-engine";
import { buildChatItems, type ChatItem } from "@/chat/chat-items";
import { chronological } from "@/chat/message-cache";
import {
  streamStore,
  useAutoOpen,
  useIsConversationRunning,
  useStreamPhase,
  useStreamingMessageId,
} from "@/chat/stream-store";
import { useConversationDetail, useCreateConversation } from "@/chat/use-conversations";
import { useMessages } from "@/chat/use-messages";
import { useParallelGate } from "@/chat/use-parallel-cap";
import { useArtifactKinds } from "@/chat/use-workspace-lookup";
import { AppText } from "@/components/AppText";
import { ArtifactSheet } from "@/components/artifact/ArtifactSheet";
import { Button } from "@/components/Button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { IconButton } from "@/components/IconButton";
import { StackHeader } from "@/components/StackHeader";
import { NEW_CHAT_DRAFT, useComposerDrafts, useDraft } from "@/stores/composer-drafts";
import { useTheme } from "@/theme/ThemeProvider";

import { ChatRowProvider, type ChatRowActions } from "./ChatContext";
import { Composer } from "./Composer";
import { ConversationMenu, conversationTitle, DEFAULT_TITLE } from "./ConversationMenu";
import { MessageRow } from "./MessageRow";
import { NoticeBanner } from "./NoticeBanner";
import { ScrollToBottom } from "./ScrollToBottom";

// M1 — the chat. `conversationId === null` is `c/new`: the first send creates
// the conversation (POST), replaces the route with `c/{id}` and sends there —
// the send engine is app-wide, so the stream survives the route swap.
//
// Layout (top → bottom): header · FlashList (NOT inverted, plan M2) · notices
// + composer + AI footer in a KeyboardStickyView. The list's scroll view is a
// KeyboardChatScrollView, so the newest messages lift with the keyboard.

const SCROLL_PILL_DISTANCE = 400;

const keyExtractor = (item: ChatItem) => item.key;
const getItemType = (item: ChatItem) => item.kind;
const renderItem = ({ item }: { item: ChatItem }) => <MessageRow item={item} />;

export function ChatScreen({ conversationId }: { conversationId: string | null }) {
  const { colours } = useTheme();
  const insets = useSafeAreaInsets();
  const listRef = useRef<FlashListRef<ChatItem>>(null);
  const inputRef = useRef<TextInput>(null);
  const draftKey = conversationId ?? NEW_CHAT_DRAFT;

  const detail = useConversationDetail(conversationId ?? undefined);
  const conversation: ConversationSummary | null = detail.data ?? null;
  const isDemo = conversation?.is_demo === true;
  const messagesQuery = useMessages(conversationId ?? undefined);
  const { data, hasNextPage, isFetchingNextPage, fetchNextPage } = messagesQuery;

  const phase = useStreamPhase(conversationId ?? "");
  const streamingId = useStreamingMessageId(conversationId ?? "");
  const running = useIsConversationRunning(conversationId ?? "");
  const parallel = useParallelGate(conversationId);

  // chronological() reads only `pages` (newest page first, newest row first).
  const messages = useMemo(() => chronological(data ? { pages: data.pages, pageParams: [] } : undefined), [data]);
  const runActive = data?.pages[0]?.run_active;
  // Placeholder ageing («ريحان يفكّر» → failed) needs a clock; 30 s is plenty
  // (the poll refetches every 4–10 s and re-renders anyway).
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  const items = useMemo(
    () => buildChatItems(messages, { phase, assistantMessageId: streamingId }, runActive, now),
    [messages, phase, streamingId, runActive, now],
  );
  const hasArtifacts = useMemo(() => messages.some((m) => (m.artifact_ids?.length ?? 0) > 0), [messages]);
  const artifactKinds = useArtifactKinds(conversationId ?? undefined, hasArtifacts);

  // ---- composer ----
  const draft = useDraft(draftKey);
  const setDraft = useComposerDrafts((s) => s.setDraft);
  const restoreDraft = useComposerDrafts((s) => s.restoreDraft);
  const create = useCreateConversation();
  const [createError, setCreateError] = useState<string | null>(null);

  const scrollToEnd = useCallback(() => {
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 60);
  }, []);

  const startSend = useCallback(
    (id: string, content: string) => {
      chatEngine
        .send({ conversationId: id, content })
        .then((result) => {
          if (result.status === "refused" && result.restoreText) restoreDraft(id, result.restoreText);
        })
        .catch(() => undefined);
    },
    [restoreDraft],
  );

  const onSend = useCallback(async () => {
    const content = draft;
    if (!content.trim()) return;
    if (conversationId) {
      setDraft(draftKey, "");
      startSend(conversationId, content);
      scrollToEnd();
      return;
    }
    setCreateError(null);
    try {
      const created = await create.mutateAsync();
      const id = created.conversation_id;
      setDraft(NEW_CHAT_DRAFT, "");
      startSend(id, content);
      router.replace({ pathname: "/c/[id]", params: { id } });
    } catch (err) {
      setCreateError(errorMessage(err));
    }
  }, [draft, conversationId, draftKey, setDraft, startSend, scrollToEnd, create]);

  const onStop = useCallback(() => {
    if (conversationId) chatEngine.stop(conversationId);
  }, [conversationId]);

  // ---- row actions (stable: rows read them from context) ----
  const rowActions = useMemo<ChatRowActions>(
    () => ({
      conversationId: conversationId ?? "",
      isDemo,
      artifactKinds,
      pasteIntoComposer: (text) => {
        setDraft(draftKey, text);
        setTimeout(() => inputRef.current?.focus(), 50);
      },
      editAndResend: (text) => {
        if (!conversationId) return;
        startSend(conversationId, text);
        scrollToEnd();
      },
      retry: (messageId) => {
        if (conversationId) void chatEngine.retry(conversationId, messageId);
      },
      regenerate: (messageId) => {
        if (conversationId) void chatEngine.regenerate(conversationId, messageId);
        scrollToEnd();
      },
      openCitation: (artifactId, n) => {
        // The reader route belongs to the source module (plan R2).
        router.push({ pathname: "/source/[itemId]/[n]", params: { itemId: artifactId, n: String(n) } });
      },
    }),
    [conversationId, isDemo, artifactKinds, draftKey, setDraft, startSend, scrollToEnd],
  );

  // ---- auto-open agent outputs over the chat (owner decision 2026-10-15) ----
  // The store raises a signal once per item; the sheet shows it until the
  // user dismisses it, which consumes the signal (a newer output replaces it).
  const autoOpen = useAutoOpen(conversationId ?? "");
  const closeArtifact = useCallback(() => {
    if (conversationId && autoOpen) streamStore.consumeAutoOpen(conversationId, autoOpen.itemId);
  }, [conversationId, autoOpen]);

  // ---- scrolling ----
  const [showPill, setShowPill] = useState(false);
  const onScroll = useCallback((e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
    const distance = contentSize.height - (contentOffset.y + layoutMeasurement.height);
    setShowPill(distance > SCROLL_PILL_DISTANCE);
  }, []);
  const onStartReached = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  // Keyboard: the sticky composer rises by (keyboard − bottom inset); the list
  // content lifts by the same amount (offset = what sits below the list and is
  // covered by the keyboard anyway).
  const bottomInset = insets.bottom;
  const renderScrollComponent = useMemo(
    () =>
      function ChatScrollView(props: ScrollViewProps) {
        return <KeyboardChatScrollView {...props} offset={bottomInset} keyboardLiftBehavior="whenAtEnd" />;
      },
    [bottomInset],
  );

  // ---- header ----
  // Back to the list (a deep link may have opened the chat with no history).
  const leave = useCallback(() => (router.canGoBack() ? router.back() : router.replace("/")), []);
  const [menuOpen, setMenuOpen] = useState(false);
  const title = conversationId ? conversationTitle(conversation) : DEFAULT_TITLE;

  const loading = !!conversationId && messagesQuery.isPending;
  const loadError = conversationId && messagesQuery.isError && !data ? errorMessage(messagesQuery.error) : null;
  const empty = !loading && !loadError && items.length === 0;

  return (
    <View style={[styles.root, { backgroundColor: colours.canvas }]}>
      <StackHeader
        title={title}
        onBack={leave}
        actions={
          conversation ? (
            <IconButton icon="more" label="خيارات المحادثة" onPress={() => setMenuOpen(true)} testID="chat-menu" />
          ) : null
        }
      />

      <View style={styles.flex}>
        {loading ? (
          <View style={styles.centre}>
            <ActivityIndicator color={colours.primary} />
          </View>
        ) : loadError ? (
          <View style={styles.centre}>
            <ErrorBanner message={loadError} />
            <Button label="إعادة المحاولة" variant="secondary" onPress={() => void messagesQuery.refetch()} />
          </View>
        ) : empty ? (
          <EmptyChat />
        ) : (
          <ChatRowProvider value={rowActions}>
            <FlashList
              ref={listRef}
              data={items}
              renderItem={renderItem}
              keyExtractor={keyExtractor}
              getItemType={getItemType}
              renderScrollComponent={renderScrollComponent}
              maintainVisibleContentPosition={{ startRenderingFromBottom: true, autoscrollToBottomThreshold: 0.2 }}
              onStartReached={onStartReached}
              onStartReachedThreshold={0.5}
              onScroll={onScroll}
              scrollEventThrottle={100}
              keyboardShouldPersistTaps="handled"
              keyboardDismissMode="interactive"
              contentContainerStyle={styles.listContent}
              ListHeaderComponent={
                isFetchingNextPage ? <ActivityIndicator style={styles.older} color={colours.textMuted} /> : null
              }
            />
            <ScrollToBottom visible={showPill} onPress={() => listRef.current?.scrollToEnd({ animated: true })} />
          </ChatRowProvider>
        )}
      </View>

      <KeyboardStickyView offset={{ closed: 0, opened: bottomInset }}>
        <View style={styles.bottom}>
          {conversationId ? <NoticeBanner conversationId={conversationId} /> : null}
          {createError ? <ErrorBanner message={createError} /> : null}
        </View>
        <Composer
          ref={inputRef}
          value={draft}
          onChangeText={(text) => setDraft(draftKey, text)}
          onSend={() => void onSend()}
          onStop={onStop}
          running={running}
          atParallelCap={parallel.atCap}
          parallelCap={parallel.cap}
          busy={create.isPending}
          demo={isDemo}
          onStartNewChat={() => router.replace("/c/new")}
        />
        <View style={{ height: bottomInset }} />
      </KeyboardStickyView>

      <ConversationMenu
        conversation={menuOpen ? conversation : null}
        onClose={() => setMenuOpen(false)}
        onDeleted={leave}
        testID="chat-conversation-menu"
      />

      {conversationId ? (
        <ArtifactSheet conversationId={conversationId} itemId={autoOpen?.itemId ?? null} onClose={closeArtifact} />
      ) : null}
    </View>
  );
}

/** New chat / empty conversation: greeting + the full AI disclaimer (D1). */
function EmptyChat() {
  const { colours, radius } = useTheme();
  const callName = useSession((s) => s.user?.call_name?.trim() || null);
  return (
    <ScrollView contentContainerStyle={styles.empty} keyboardShouldPersistTaps="handled" testID="chat-empty">
      <AppText variant="h2" weight={700} align="center">
        {callName ? `مرحباً ${callName}` : "مرحباً بك"}
      </AppText>
      <AppText variant="base" tone="muted" align="center">
        اسأل ريحان عن أي مسألة قانونية في الأنظمة السعودية
      </AppText>
      <View style={[styles.disclaimer, { backgroundColor: colours.surface2, borderRadius: radius.xl }]}>
        <AppText variant="sm" tone="secondary" testID="chat-empty-disclaimer">
          {AGENT_OUTPUT_DISCLAIMER_AR}
        </AppText>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  flex: { flex: 1 },
  centre: { flex: 1, alignItems: "stretch", justifyContent: "center", padding: 24, gap: 12 },
  listContent: { paddingTop: 16, paddingBottom: 8 },
  older: { paddingVertical: 12 },
  bottom: { paddingHorizontal: 12 },
  empty: { flexGrow: 1, justifyContent: "center", padding: 24, gap: 12 },
  disclaimer: { padding: 14, marginTop: 8 },
});
