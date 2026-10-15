// Message shapes — moved verbatim from frontend/types/index.ts (the web
// re-exports them from `@/types`).

import type { AgentFamily, WorkspaceItemKind } from "./agents";

// ==========================================
// MESSAGES
// ==========================================

/** Discriminator for messages tied to an agent ask_user pause/resume cycle. */
export type MessageMetadataKind = 'agent_question' | 'agent_answer';

export interface MessageMetadata {
  /** When set, this message is part of an agent ask_user turn. */
  kind?: MessageMetadataKind;
  /** The agent_run that originated the question / is being answered. */
  run_id?: string;
  /** The agent family that paused for input. */
  agent_family?: AgentFamily;
  /** Optional suggested replies surfaced with an agent_question. */
  suggestions?: string[];
  /**
   * Clickable next-step chips (next_step_suggestions plan §3.6/§3.7) offered
   * under a finished deep_search / simple_search answer. NOT the same thing
   * as ``suggestions`` above (read-only agent_question replies).
   */
  next_steps?: NextStep[];
  /**
   * Templates a writer draft was built on. An EMPTY list means a draft was
   * produced without a template; the key is ABSENT on turns that drafted
   * nothing. Validate with ``parseTemplatesUsed`` (lib/templates-used.ts).
   */
  templates_used?: TemplateUsed[];
  [key: string]: unknown;
}

/** Scope of a قالب: ``system`` = قالب عام (read-only), ``user`` = قالب خاص. */
export type TemplateScope = "user" | "system";

/** One template a writer draft was built on (``metadata.templates_used``). */
export interface TemplateUsed {
  template_id: string;
  title: string;
  scope: TemplateScope;
  subtype: string | null;
  court: string | null;
  /** Absolute http(s) URLs (e.g. our blog posts). */
  sources: string[];
}

/**
 * What a next-step chip offers (next_step_suggestions plan §3.2).
 * ``open`` is simple_search only (an unselected candidate / rest of a doc).
 */
export type NextStepKind = "narrow_search" | "draft" | "apply" | "open";

export interface NextStep {
  kind: NextStepKind;
  /** Chip text — short Arabic label, no trailing punctuation. */
  label: string;
  /** Full first-person message pasted into the composer on click. */
  prompt: string;
}

export interface Message {
  message_id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  model?: string;
  attachments: Attachment[];
  created_at: string;
  metadata?: MessageMetadata;
  /**
   * Workspace item ids produced by the agent run that authored this message.
   * Populated by the backend on assistant messages whose agent_runs created
   * one or more ``workspace_items``. Always undefined / empty for user or
   * legacy / Q&A / agent_question messages.
   *
   * NOTE (Window C, 2026-05-20): backend does not yet expose this field on
   * ``MessageResponse``; the frontend treats it defensively as possibly
   * undefined so the new citation + chip UI is a no-op until the backend
   * ships it.
   */
  artifact_ids?: string[] | null;
  /**
   * Workspace items the planner pointed back to instead of publishing a new
   * card (Phase E ``build_artifact=False``). Drives the prior-card chip in
   * the assistant bubble. When set, the chat-store entry recorded by the
   * live ``referenced_existing_item`` SSE event still wins for the
   * just-streamed turn; the persisted value lights up the chip on refresh.
   */
  referenced_item_ids?: string[] | null;
  isOptimistic?: boolean;
  isFailed?: boolean;
  isStreaming?: boolean;
}

export interface Attachment {
  id: string;
  document_id: string;
  attachment_type: 'pdf' | 'image' | 'file';
  filename: string;
  file_size?: number;
  /**
   * ``workspace_items.kind`` of the attached item (messages API embeds it via
   * the migration-088 FK). Distinguishes an uploaded file (``attachment``)
   * from an item attached from within the conversation (e.g. a blog import =
   * ``agent_search``) on the message chips. Optional: absent on legacy cache
   * entries — the chip falls back to ``artifactLookup``.
   */
  kind?: WorkspaceItemKind;
}

export interface MessageListResponse {
  messages: Message[];
  has_more: boolean;
  /**
   * Server truth: this conversation has a live run right now (snapshotted
   * before the read). An empty assistant placeholder with `run_active: false`
   * has nothing left to fill it — the run died with a restart/deploy — so it
   * renders as failed instead of «ريحان يفكّر». Absent on older backends.
   */
  run_active?: boolean;
}

// ==========================================
// SEND MESSAGE PAYLOAD
// ==========================================

export interface SendMessagePayload {
  content: string;
  attachment_ids?: string[] | null;
}
