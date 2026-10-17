import { createContext, useContext } from "react";

import type { ArtifactInfoMap, ArtifactKinds } from "@/chat/use-workspace-lookup";

// Callbacks + per-conversation facts every row needs. Provided once by the
// chat screen with a STABLE value (callbacks read refs), so FlashList rows do
// not re-render when the screen does.

export interface ChatRowActions {
  conversationId: string;
  isDemo: boolean;
  artifactKinds: ArtifactKinds;
  artifactInfo: ArtifactInfoMap;
  /** Paste a next-step prompt into the composer (never sends). */
  pasteIntoComposer: (text: string) => void;
  /** «تعديل» → «حفظ وإرسال»: a NEW send with the edited text. */
  editAndResend: (text: string) => void;
  retry: (userMessageId: string) => void;
  regenerate: (assistantMessageId: string) => void;
  openCitation: (artifactId: string, n: number) => void;
  /** «افتح ال…» — reopen an agent output over the chat (the ArtifactSheet). */
  openArtifact: (itemId: string) => void;
}

const ChatRowContext = createContext<ChatRowActions | null>(null);

export const ChatRowProvider = ChatRowContext.Provider;

export function useChatRow(): ChatRowActions {
  const ctx = useContext(ChatRowContext);
  if (!ctx) throw new Error("useChatRow outside ChatRowProvider");
  return ctx;
}
