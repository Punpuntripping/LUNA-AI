import { detailLevelLabel, type PendingTemplate } from "@contracts";
import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";

import type { SheetAction } from "@/components/ActionSheet";
import { MAX_MESSAGE_CHARS } from "@/components/chat/UserBubble";
import { useDetailLevel } from "@/components/settings/use-detail-level";
import { formatNumber } from "@/lib/format";
import { modalCoordinator } from "@/lib/modal-coordinator";
import { NEW_CHAT_DRAFT } from "@/stores/composer-drafts";

import { BlogLinkSheet } from "./BlogLinkSheet";
import { queueBlogTokens } from "./blog-import";
import { ComposerExtrasChips } from "./ComposerExtrasChips";
import { DetailLevelSheet } from "./DetailLevelSheet";
import { useComposerExtrasStore, usePendingBlogs, usePendingTemplate } from "./pending-store";
import { buildExtrasSend, extrasBlockSend, extrasMakeSendable, type ExtrasSendParts } from "./send-parts";
import { TemplatePickerSheet } from "./TemplatePickerSheet";

// The web composer «+» menu's non-file items (ComposerPlusMenu) for the
// attachment «+» sheet: «قالب», «مدونة», and «مستوى التفصيل» below them —
// plus everything the send path needs from them (chips, send gating, the
// directive line, the blog item ids).

type ExtraSheet = "template" | "blog" | "detail";

export interface ComposerExtrasOptions {
  /** `null` on `c/new`. */
  conversationId: string | null;
  /**
   * `c/new` only — the create-on-attach flow (web `onRequireConversation`):
   * create the conversation, carry the draft, navigate to it, and resolve its
   * id (null = failed, already reported). Without it «مدونة» is left out on
   * `c/new` (web: disabled until a conversation exists).
   */
  ensureConversation?: () => Promise<string | null>;
}

export type ExtrasTakeResult =
  | { ok: false; error: string }
  | {
      ok: true;
      parts: ExtrasSendParts;
      /** Call once the server took the send (`onCommitted`): drops exactly the
       *  template + blog chips this send carried. A refused send keeps them. */
      release: () => void;
    };

export interface ComposerExtras {
  /** Rows for the «+» sheet, in web order: قالب · مدونة · مستوى التفصيل. */
  actions: SheetAction[];
  /** Render once next to the composer (the three sheets). */
  sheets: ReactElement;
  /** Render above the input (template + blog pills; null-renders when empty). */
  chips: ReactElement;
  template: PendingTemplate | null;
  /** A template or a ready blog makes an empty text sendable (web canSend). */
  sendableWithoutText: boolean;
  /** A blog import is still running — keep «إرسال» disabled. */
  blocksSend: boolean;
  /** Bumped on every قالب pick and every مدونة queued — the composer clears
   *  its error line then (web ChatInput `setValidationError(null)`). */
  errorResetKey: number;
  /**
   * At send: the outgoing text (directive line appended) + blog attachment ids.
   * Takes nothing out of the store: `release` (the send's `onCommitted`) drops
   * the template and blog chips this send carried, so a refused send — quota,
   * parallel cap — keeps them for the next try (the web clears the template at
   * `onSend`; the phone restores the whole composer instead).
   */
  takeForSend: (text: string) => ExtrasTakeResult;
}

export function useComposerExtraActions({ conversationId, ensureConversation }: ComposerExtrasOptions): ComposerExtras {
  const draftKey = conversationId ?? NEW_CHAT_DRAFT;
  const template = usePendingTemplate(draftKey);
  const blogs = usePendingBlogs(draftKey);
  const setTemplate = useComposerExtrasStore((s) => s.setTemplate);
  const detail = useDetailLevel();

  const [sheet, setSheet] = useState<ExtraSheet | null>(null);
  // The «+» sheet closes as a row is tapped; ours presents once its Modal is
  // gone (iOS refuses a modal over a dismissing one).
  const [requested, setRequested] = useState<ExtraSheet | null>(null);
  useEffect(() => {
    if (!requested) return undefined;
    return modalCoordinator.whenSettled(() => {
      setRequested(null);
      setSheet(requested);
    });
  }, [requested]);
  const openSheet = useCallback((which: ExtraSheet) => setRequested(which), []);
  const close = useCallback(() => setSheet(null), []);

  const canBlog = conversationId !== null || ensureConversation !== undefined;
  const detailLabel = detailLevelLabel(detail.level);

  const actions = useMemo<SheetAction[]>(() => {
    const list: SheetAction[] = [{ key: "template", label: "قالب", icon: "template", onPress: () => openSheet("template") }];
    if (canBlog) list.push({ key: "blog", label: "مدونة", icon: "blog", onPress: () => openSheet("blog") });
    list.push({
      key: "detail-level",
      label: "مستوى التفصيل",
      icon: "sliders",
      hint: detailLabel,
      onPress: () => openSheet("detail"),
    });
    return list;
  }, [canBlog, detailLabel, openSheet]);

  const [errorResetKey, setErrorResetKey] = useState(0);
  const bumpErrorReset = useCallback(() => setErrorResetKey((k) => k + 1), []);
  const onPickTemplate = useCallback(
    (t: PendingTemplate) => {
      bumpErrorReset();
      setTemplate(draftKey, t);
    },
    [draftKey, setTemplate, bumpErrorReset],
  );
  const onAddTokens = useCallback(
    async (tokens: string[]) => {
      const err = await queueBlogTokens({ conversationId, draftKey, tokens, ensureConversation });
      if (err === null) bumpErrorReset();
      return err;
    },
    [conversationId, draftKey, ensureConversation, bumpErrorReset],
  );

  const takeForSend = useCallback(
    (text: string): ExtrasTakeResult => {
      const state = useComposerExtrasStore.getState();
      const t = state.templates[draftKey] ?? null;
      const held = state.blogs[draftKey] ?? [];
      const built = buildExtrasSend(text, t, held, MAX_MESSAGE_CHARS, formatNumber(MAX_MESSAGE_CHARS));
      if (!built.ok) return built;
      const sent = built.parts.sentBlogChipIds;
      return {
        ok: true,
        parts: built.parts,
        release: () => {
          const current = useComposerExtrasStore.getState();
          if (sent.length) current.removeBlogs(draftKey, sent);
          // Only the template this send carried (a newer pick stays).
          if (t && current.templates[draftKey] === t) current.setTemplate(draftKey, null);
        },
      };
    },
    [draftKey],
  );

  const sheets = (
    <>
      <TemplatePickerSheet visible={sheet === "template"} onClose={close} onPick={onPickTemplate} />
      <BlogLinkSheet visible={sheet === "blog"} onClose={close} onAddTokens={onAddTokens} />
      <DetailLevelSheet visible={sheet === "detail"} onClose={close} />
    </>
  );

  return {
    actions,
    sheets,
    chips: <ComposerExtrasChips draftKey={draftKey} />,
    template,
    sendableWithoutText: extrasMakeSendable(template, blogs),
    blocksSend: extrasBlockSend(blogs),
    errorResetKey,
    takeForSend,
  };
}
