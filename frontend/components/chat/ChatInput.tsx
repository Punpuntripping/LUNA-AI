"use client";

import { useState, useCallback, useRef, type KeyboardEvent } from "react";
import TextareaAutosize from "react-textarea-autosize";
import { Send, Square, Plus, Paperclip, LayoutGrid, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { FilePreview } from "@/components/chat/FilePreview";
import { AtCommandPalette } from "@/components/chat/AtCommandPalette";
import { parseAtCommands } from "@/lib/commands";
import type { AtCommand } from "@/lib/commands";
import type { PendingFile } from "@/types";

interface ChatInputProps {
  onSend: (content: string) => void;
  onStop?: () => void;
  disabled?: boolean;
  className?: string;
  /** When set, file uploads are enabled (uploaded to this case). */
  caseId?: string | null;
  /** Callback when user selects "My Templates" from the + menu */
  onOpenTemplates?: () => void;
}

const MAX_CHARS = 10_000;
const MAX_FILES = 5;
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const ACCEPTED_TYPES = ["application/pdf", "image/png", "image/jpeg"];

export function ChatInput({ onSend, onStop, disabled, className, caseId, onOpenTemplates }: ChatInputProps) {
  const [content, setContent] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [atQuery, setAtQuery] = useState("");
  const [isAtPaletteOpen, setIsAtPaletteOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const isStreaming = useChatStore((s) => s.isStreaming);
  const pendingFiles = useChatStore((s) => s.pendingFiles);
  const addPendingFile = useChatStore((s) => s.addPendingFile);
  const removePendingFile = useChatStore((s) => s.removePendingFile);
  const setSelectedAgentFamily = useChatStore((s) => s.setSelectedAgentFamily);
  const setModifiers = useChatStore((s) => s.setModifiers);

  const isDisabled = disabled || (isStreaming && !onStop);
  const canSend = (content.trim().length > 0 || pendingFiles.length > 0) && !isStreaming && !disabled;

  // ------------------------------------------
  // @ palette detection
  // ------------------------------------------

  const detectAtTrigger = useCallback((value: string) => {
    // Find the last @ in the text
    const lastAtIndex = value.lastIndexOf("@");
    if (lastAtIndex < 0) {
      setIsAtPaletteOpen(false);
      return;
    }

    const afterAt = value.slice(lastAtIndex + 1);

    // Only show palette if the text after @ has no spaces or newlines
    // (user is still typing the command trigger)
    if (afterAt.includes(" ") || afterAt.includes("\n")) {
      setIsAtPaletteOpen(false);
      return;
    }

    setAtQuery(afterAt);
    setIsAtPaletteOpen(true);
  }, []);

  // ------------------------------------------
  // Text change handler
  // ------------------------------------------

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const val = e.target.value;
      setContent(val);
      detectAtTrigger(val);
    },
    [detectAtTrigger]
  );

  // ------------------------------------------
  // @ command selection
  // ------------------------------------------

  const handleAtCommandSelect = useCallback(
    (cmd: AtCommand) => {
      // Replace the partial @query with the full @trigger
      const lastAtIndex = content.lastIndexOf("@");
      const before = lastAtIndex >= 0 ? content.slice(0, lastAtIndex) : content;
      const newContent = `${before}@${cmd.trigger} `;
      setContent(newContent);
      setIsAtPaletteOpen(false);

      // Refocus the textarea after selection
      requestAnimationFrame(() => {
        textareaRef.current?.focus();
      });
    },
    [content]
  );

  const handleAtPaletteClose = useCallback(() => {
    setIsAtPaletteOpen(false);
  }, []);

  // ------------------------------------------
  // Send handler (with @ command parsing)
  // ------------------------------------------

  const handleSend = useCallback(() => {
    const trimmed = content.trim();
    if (!trimmed && pendingFiles.length === 0) return;

    if (trimmed.length > MAX_CHARS) {
      setValidationError(`الحد الأقصى ${MAX_CHARS.toLocaleString("ar-SA")} حرف`);
      return;
    }

    setValidationError(null);

    // Parse @ commands from the message (only if there's text)
    if (trimmed) {
      const parsed = parseAtCommands(trimmed);
      if (parsed.agent_family) {
        setSelectedAgentFamily(parsed.agent_family);
      }
      if (parsed.modifiers.length > 0) {
        setModifiers(parsed.modifiers);
      }
    }

    onSend(trimmed);
    setContent("");
    setIsAtPaletteOpen(false);
  }, [content, onSend, setSelectedAgentFamily, setModifiers, pendingFiles.length]);

  // ------------------------------------------
  // Keyboard handler
  // ------------------------------------------

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // When the @ palette is open, let it handle navigation keys
      if (isAtPaletteOpen) {
        if (
          e.key === "ArrowUp" ||
          e.key === "ArrowDown" ||
          e.key === "Enter" ||
          e.key === "Tab" ||
          e.key === "Escape"
        ) {
          // These are handled by the AtCommandPalette's window keydown listener
          return;
        }
      }

      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (canSend) handleSend();
      }
    },
    [canSend, handleSend, isAtPaletteOpen]
  );

  // ------------------------------------------
  // File selection
  // ------------------------------------------

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) return;

      setValidationError(null);

      const currentCount = pendingFiles.length;
      const newFiles = Array.from(files);

      if (currentCount + newFiles.length > MAX_FILES) {
        setValidationError(`الحد الأقصى ${MAX_FILES} ملفات`);
        return;
      }

      for (const file of newFiles) {
        if (!ACCEPTED_TYPES.includes(file.type)) {
          setValidationError("الملفات المقبولة: PDF، PNG، JPG فقط");
          return;
        }

        if (file.size > MAX_FILE_SIZE) {
          setValidationError("الحد الأقصى لحجم الملف 50 ميجابايت");
          return;
        }

        const pendingFile: PendingFile = {
          id: `file-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          file,
          previewUrl: file.type.startsWith("image/")
            ? URL.createObjectURL(file)
            : "",
          name: file.name,
          size: file.size,
          mimeType: file.type,
        };

        addPendingFile(pendingFile);
      }

      // Reset input so same file can be selected again
      e.target.value = "";
    },
    [pendingFiles.length, addPendingFile]
  );

  // ------------------------------------------
  // + menu actions
  // ------------------------------------------

  const handleAddFile = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleOpenCommands = useCallback(() => {
    // Insert @ to trigger the command palette
    setContent((prev) => prev + "@");
    setIsAtPaletteOpen(true);
    setAtQuery("");
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
    });
  }, []);

  const handleStopClick = useCallback(() => {
    onStop?.();
  }, [onStop]);

  return (
    <div dir="rtl" lang="ar" className={cn("border-t bg-background px-4 py-3", className)}>
      {/* Pending files preview */}
      {pendingFiles.length > 0 && (
        <FilePreview
          files={pendingFiles}
          onRemove={removePendingFile}
          className="mb-2"
        />
      )}

      {/* Validation error */}
      {validationError && (
        <p className="text-xs text-destructive mb-2">{validationError}</p>
      )}

      {/* Input row with @ palette */}
      <div className="relative flex items-end gap-2">
        {/* @ Command Palette (positioned above the input) */}
        <AtCommandPalette
          query={atQuery}
          isOpen={isAtPaletteOpen}
          onSelect={handleAtCommandSelect}
          onClose={handleAtPaletteClose}
        />

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg"
          className="hidden"
          onChange={handleFileSelect}
        />

        {/* Textarea */}
        <TextareaAutosize
          ref={textareaRef}
          dir="rtl"
          lang="ar"
          value={content}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="اكتب رسالتك هنا... (اكتب @ لعرض الأوامر)"
          minRows={1}
          maxRows={6}
          readOnly={isStreaming}
          disabled={disabled}
          className={cn(
            "flex-1 resize-none rounded-xl border bg-muted/50 px-4 py-2.5 text-sm",
            "placeholder:text-muted-foreground",
            "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1",
            "disabled:cursor-not-allowed disabled:opacity-50",
            isStreaming && "cursor-not-allowed opacity-70"
          )}
        />

        {/* + Menu button */}
        <DropdownMenu dir="rtl">
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-10 w-10 shrink-0"
              disabled={isStreaming}
              aria-label="خيارات إضافية"
            >
              <Plus className="h-5 w-5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="end" className="min-w-[180px]">
            <DropdownMenuItem
              onClick={handleAddFile}
              className="gap-2 cursor-pointer"
            >
              <Paperclip className="h-4 w-4" />
              <span>إضافة مرفق</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={onOpenTemplates}
              className="gap-2 cursor-pointer"
            >
              <LayoutGrid className="h-4 w-4" />
              <span>قوالبي</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={handleOpenCommands}
              className="gap-2 cursor-pointer"
            >
              <Terminal className="h-4 w-4" />
              <span>الأوامر</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Send / Stop button */}
        {isStreaming ? (
          <Button
            variant="destructive"
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={handleStopClick}
            aria-label="إيقاف"
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={handleSend}
            disabled={!canSend}
            aria-label="إرسال"
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
