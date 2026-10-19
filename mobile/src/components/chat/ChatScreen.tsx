import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { router } from "expo-router";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
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
import { KeyboardChatScrollView, KeyboardController, KeyboardEvents, KeyboardStickyView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AGENT_OUTPUT_DISCLAIMER_AR, type ConversationListResponse, type ConversationSummary } from "@contracts";

import { errorMessage } from "@/api/errors";
import { useSession } from "@/auth/session-store";
import { chatEngine } from "@/chat/chat-engine";
import { buildChatItems, type ChatItem } from "@/chat/chat-items";
import { conversationKeys, messageKeys } from "@/chat/keys";
import { chronological, type MessagesData } from "@/chat/message-cache";
import {
  streamStore,
  useAutoOpen,
  useIsConversationRunning,
  useStreamPhase,
  useStreamingMessageId,
} from "@/chat/stream-store";
import { useConversationDetail, useCreateConversation, useDeleteConversation } from "@/chat/use-conversations";
import { useMessages } from "@/chat/use-messages";
import { useParallelGate } from "@/chat/use-parallel-cap";
import { useArtifactInfo, useArtifactKinds } from "@/chat/use-workspace-lookup";
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
// the conversation (POST), sends there, and replaces the route with `c/{id}`
// once the server took the send (`message_start`) — the send engine is
// app-wide, so the stream survives the route swap. A first send REFUSED by the
// gate (quota / parallel — nothing persisted) deletes that just-created, empty
// conversation and stays on `c/new` with the text and the banner (QA F6), so
// no empty «محادثة جديدة» is left in the list.
//
// Layout (top → bottom): header · FlashList (NOT inverted, plan M2) · notices
// + composer + AI footer in a KeyboardStickyView. The list's scroll view is a
// KeyboardChatScrollView, so the newest messages lift with the keyboard.

const SCROLL_PILL_DISTANCE = 400;
/** Opening a conversation keeps it pinned to the newest message until its
 *  content stops growing for this long (or the user drags) — QA N7. */
const OPEN_PIN_SETTLE_MS = 1_200;
/** `c/new` waits this long for `message_start` before showing `c/{id}` anyway
 *  (a gate refusal comes back well before; this only bounds a slow start). */
const FIRST_SEND_WAIT_MS = 4_000;

/** While a user bubble is edited inline the list must NOT lift with the
 *  keyboard (QA F3) — read by the scroll component, whose identity must stay
 *  stable (a new component type would remount the list's ScrollView). */
const ListLiftContext = createContext<"whenAtEnd" | "never">("whenAtEnd");

/** A conversation's summary from any cached list page (F9: the header can show
 *  the real title while the detail query is still loading). */
function findInLists(
  pages: [unknown, InfiniteData<ConversationListResponse> | undefined][],
  id: string,
): ConversationSummary | null {
  for (const [, data] of pages) {
    for (const page of data?.pages ?? []) {
      const hit = page.conversations.find((c) => c.conversation_id === id);
      if (hit) return hit;
    }
  }
  return null;
}

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
  const artifactInfo = useArtifactInfo(conversationId ?? undefined, hasArtifacts);
  // An output the user reopened from an answer («افتح ال…»); wins over the
  // auto-open signal while shown.
  const [openedItemId, setOpenedItemId] = useState<string | null>(null);

  // ---- composer ----
  const draft = useDraft(draftKey);
  const setDraft = useComposerDrafts((s) => s.setDraft);
  const restoreDraft = useComposerDrafts((s) => s.restoreDraft);
  const qc = useQueryClient();
  const create = useCreateConversation();
  const removeConversation = useDeleteConversation();
  const [createError, setCreateError] = useState<string | null>(null);
  // c/new: created + sent, waiting for the server to take it (see top note).
  const [firstSending, setFirstSending] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Open-pin state (QA N7, see «scrolling» below) — declared first because the
  // inline edit releases it.
  const pinToEnd = useRef(true);
  const pinTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scrollToEnd = useCallback(() => {
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 60);
  }, []);

  // ---- inline edit (QA F3) ----
  // Editing a bubble near the end: the keyboard lifted the list (whenAtEnd, the
  // newest answer stays visible) and the growing edit box could trip
  // maintainVisibleContentPosition's auto-scroll to the end — both pushed the
  // edited bubble off the top. While a bubble is edited: no lift, no
  // end-autoscroll, and once the keyboard is up the row is scrolled to the top
  // of the visible area (its input + «حفظ وإرسال» sit right under it).
  const [editingId, setEditingId] = useState<string | null>(null);
  const setEditingMessage = useCallback((messageId: string, editing: boolean) => {
    // An edit is a deliberate scroll position: the open-pin (N7) never fights it.
    if (editing) pinToEnd.current = false;
    setEditingId((current) => (editing ? messageId : current === messageId ? null : current));
  }, []);
  const itemsRef = useRef<ChatItem[]>([]);
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  const revealEdited = useCallback((messageId: string) => {
    const index = itemsRef.current.findIndex((it) => it.key === messageId);
    // FlashList ADDS viewOffset to the target offset: negative = a gap above.
    if (index >= 0) void listRef.current?.scrollToIndex({ index, viewPosition: 0, viewOffset: -8, animated: true });
  }, []);
  useEffect(() => {
    if (!editingId) return;
    if (KeyboardController.isVisible()) revealEdited(editingId);
    const sub = KeyboardEvents.addListener("keyboardDidShow", () => revealEdited(editingId));
    return () => sub.remove();
  }, [editingId, revealEdited]);

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
    streamStore.setNotice(NEW_CHAT_DRAFT, null);
    let id: string;
    try {
      const created = await create.mutateAsync();
      id = created.conversation_id;
    } catch (err) {
      setCreateError(errorMessage(err));
      return;
    }
    setDraft(NEW_CHAT_DRAFT, "");
    setFirstSending(true);
    let settled = false;
    const open = () => {
      if (settled) return;
      settled = true;
      setFirstSending(false);
      if (mounted.current) router.replace({ pathname: "/c/[id]", params: { id } });
    };
    const timer = setTimeout(open, FIRST_SEND_WAIT_MS);
    chatEngine
      .send({
        conversationId: id,
        content,
        onCommitted: () => {
          clearTimeout(timer);
          open();
        },
      })
      .then((result) => {
        clearTimeout(timer);
        if (result.status === "refused") {
          // Already shown as c/{id} (slow refusal after the fallback) but the
          // conversation still has no message: same clean-up, back to c/new.
          const navigated = settled;
          const persisted = chronological(qc.getQueryData<MessagesData>(messageKeys.list(id))).some(
            (m) => !m.isOptimistic && !m.isFailed,
          );
          if (!navigated || !persisted) {
            // Nothing was persisted: drop the empty conversation, keep the user
            // on c/new with their text and the server's banner.
            settled = true;
            setFirstSending(false);
            streamStore.setNotice(id, null);
            streamStore.setNotice(NEW_CHAT_DRAFT, result.notice);
            if (result.restoreText) restoreDraft(NEW_CHAT_DRAFT, result.restoreText);
            removeConversation.mutate(id);
            if (navigated) router.replace("/c/new");
            return;
          }
          if (result.restoreText) restoreDraft(id, result.restoreText);
        }
        open();
      })
      .catch(() => open());
  }, [draft, conversationId, draftKey, setDraft, startSend, scrollToEnd, create, restoreDraft, removeConversation, qc]);

  // ---- banner lifetime (QA N3) ----
  // A send-failure / refusal banner belongs to the visit it happened in:
  // leaving the conversation clears it, so reopening never shows a stale «تعذّر
  // إرسال الرسالة…». One that lands while the user is away (a background send
  // failing) is still shown once on the next visit. A new send clears it
  // (stream-store `begin`); so does a later answer reaching the list (below).
  // c/new's key is NEW_CHAT_DRAFT: the F6 refusal moved there shows on the
  // new-chat screen and is cleared when that screen is left.
  useEffect(
    () => () => {
      streamStore.setError(draftKey, null);
      streamStore.setNotice(draftKey, null);
    },
    [draftKey],
  );
  const latestAnswerId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && !m.isOptimistic && !m.isFailed && m.content.trim()) return m.message_id;
    }
    return null;
  }, [messages]);
  const seenAnswerId = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (!conversationId || !data) return;
    const previous = seenAnswerId.current;
    seenAnswerId.current = latestAnswerId;
    if (previous === undefined || latestAnswerId === null || latestAnswerId === previous) return;
    // A newer answer arrived (stream end, poll, adopt): the failure is over.
    // Quota / parallel notices stay — they are about the NEXT send.
    streamStore.setError(conversationId, null);
    if (streamStore.getState().notices[conversationId]?.kind === "duplicate") streamStore.setNotice(conversationId, null);
  }, [conversationId, data, latestAnswerId]);

  const onStop = useCallback(() => {
    if (conversationId) chatEngine.stop(conversationId);
  }, [conversationId]);

  // ---- row actions (stable: rows read them from context) ----
  const rowActions = useMemo<ChatRowActions>(
    () => ({
      conversationId: conversationId ?? "",
      isDemo,
      artifactKinds,
      artifactInfo,
      pasteIntoComposer: (text) => {
        setDraft(draftKey, text);
        setTimeout(() => inputRef.current?.focus(), 50);
      },
      setEditingMessage,
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
      openArtifact: setOpenedItemId,
    }),
    [conversationId, isDemo, artifactKinds, artifactInfo, draftKey, setDraft, setEditingMessage, startSend, scrollToEnd],
  );

  // ---- auto-open agent outputs over the chat (owner decision 2026-10-15) ----
  // The store raises a signal once per item; the sheet shows it until the
  // user dismisses it, which consumes the signal (a newer output replaces it).
  const autoOpen = useAutoOpen(conversationId ?? "");
  const closeArtifact = useCallback(() => {
    if (openedItemId !== null) {
      setOpenedItemId(null);
      return;
    }
    if (conversationId && autoOpen) streamStore.consumeAutoOpen(conversationId, autoOpen.itemId);
  }, [conversationId, autoOpen, openedItemId]);

  // ---- scrolling ----
  // QA N7: startRenderingFromBottom scrolls to the TOP of the last item, laid
  // out with ESTIMATED heights; real rows (markdown, chips, «افتح ال…») are
  // taller and maintainVisibleContentPosition then anchors the visible row, so
  // a long conversation opened a few screens above its end. While the opened
  // list settles, every content-size change re-pins it to the end; the first
  // drag, an inline edit, or OPEN_PIN_SETTLE_MS without growth releases it.
  const releasePin = useCallback(() => {
    pinToEnd.current = false;
    if (pinTimer.current) clearTimeout(pinTimer.current);
    pinTimer.current = null;
  }, []);
  useEffect(() => releasePin, [releasePin]);
  const onContentSizeChange = useCallback(() => {
    if (!pinToEnd.current) return;
    listRef.current?.scrollToEnd({ animated: false });
    if (pinTimer.current) clearTimeout(pinTimer.current);
    pinTimer.current = setTimeout(releasePin, OPEN_PIN_SETTLE_MS);
  }, [releasePin]);
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
        const lift = useContext(ListLiftContext);
        return <KeyboardChatScrollView {...props} offset={bottomInset} keyboardLiftBehavior={lift} />;
      },
    [bottomInset],
  );

  // ---- header ----
  // Back to the list (a deep link may have opened the chat with no history).
  const leave = useCallback(() => (router.canGoBack() ? router.back() : router.replace("/")), []);
  const [menuOpen, setMenuOpen] = useState(false);
  // F9: «محادثة جديدة» only for c/new or a conversation known to be untitled;
  // while an existing one loads, its cached list row's title or a blank header.
  const cached = useMemo(
    () =>
      conversationId
        ? findInLists(qc.getQueriesData<InfiniteData<ConversationListResponse>>({ queryKey: conversationKeys.lists() }), conversationId)
        : null,
    [qc, conversationId],
  );
  const known = conversation ?? cached;
  const title = !conversationId ? DEFAULT_TITLE : known ? conversationTitle(known) : "";

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
            <ListLiftContext.Provider value={editingId ? "never" : "whenAtEnd"}>
              <FlashList
                ref={listRef}
                data={items}
                renderItem={renderItem}
                keyExtractor={keyExtractor}
                getItemType={getItemType}
                renderScrollComponent={renderScrollComponent}
                maintainVisibleContentPosition={{
                  startRenderingFromBottom: true,
                  autoscrollToBottomThreshold: editingId ? undefined : 0.2,
                }}
                onStartReached={onStartReached}
                onStartReachedThreshold={0.5}
                onScroll={onScroll}
                onContentSizeChange={onContentSizeChange}
                onScrollBeginDrag={releasePin}
                scrollEventThrottle={100}
                keyboardShouldPersistTaps="handled"
                keyboardDismissMode="interactive"
                contentContainerStyle={styles.listContent}
                ListHeaderComponent={
                  isFetchingNextPage ? <ActivityIndicator style={styles.older} color={colours.textMuted} /> : null
                }
              />
            </ListLiftContext.Provider>
            <ScrollToBottom visible={showPill} onPress={() => listRef.current?.scrollToEnd({ animated: true })} />
          </ChatRowProvider>
        )}
      </View>

      <KeyboardStickyView offset={{ closed: 0, opened: bottomInset }}>
        <View style={styles.bottom}>
          <NoticeBanner conversationId={conversationId ?? NEW_CHAT_DRAFT} />
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
          busy={create.isPending || firstSending}
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
        <ArtifactSheet
          conversationId={conversationId}
          itemId={openedItemId ?? autoOpen?.itemId ?? null}
          isDemo={isDemo}
          onClose={closeArtifact}
        />
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
