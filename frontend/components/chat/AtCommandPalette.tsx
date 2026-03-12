"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import { filterCommands, type AtCommand } from "@/lib/commands";

// ==========================================
// AtCommandPalette
// ==========================================
// Autocomplete popup that appears when the user types @ in the chat input.
// Shows a filtered list of available commands with Arabic labels.
// Supports keyboard navigation (ArrowUp/Down + Enter) and mouse selection.

interface AtCommandPaletteProps {
  /** Text typed after the @ symbol (used for filtering) */
  query: string;
  /** Whether the palette is currently visible */
  isOpen: boolean;
  /** Called when a command is selected (via Enter or click) */
  onSelect: (command: AtCommand) => void;
  /** Called when the palette should close (Escape or outside click) */
  onClose: () => void;
  /** Additional className for the container */
  className?: string;
}

export function AtCommandPalette({
  query,
  isOpen,
  onSelect,
  onClose,
  className,
}: AtCommandPaletteProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const commands = filterCommands(query);

  // Reset selection index when the query or open state changes
  useEffect(() => {
    setSelectedIndex(0);
  }, [query, isOpen]);

  // Scroll the selected item into view
  useEffect(() => {
    if (!listRef.current) return;
    const items = listRef.current.querySelectorAll("[data-command-item]");
    const selected = items[selectedIndex];
    if (selected) {
      selected.scrollIntoView({ block: "nearest" });
    }
  }, [selectedIndex]);

  // Handle keyboard navigation
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!isOpen || commands.length === 0) return;

      switch (e.key) {
        case "Escape":
          e.preventDefault();
          e.stopPropagation();
          onClose();
          break;
        case "ArrowDown":
          e.preventDefault();
          e.stopPropagation();
          setSelectedIndex((i) => (i + 1) % commands.length);
          break;
        case "ArrowUp":
          e.preventDefault();
          e.stopPropagation();
          setSelectedIndex((i) => (i - 1 + commands.length) % commands.length);
          break;
        case "Enter":
          e.preventDefault();
          e.stopPropagation();
          onSelect(commands[selectedIndex]);
          break;
        case "Tab":
          e.preventDefault();
          e.stopPropagation();
          onSelect(commands[selectedIndex]);
          break;
      }
    },
    [isOpen, selectedIndex, commands, onSelect, onClose]
  );

  useEffect(() => {
    if (!isOpen) return;
    // Use capture phase so we intercept before the textarea's own handler
    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [isOpen, handleKeyDown]);

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (listRef.current && !listRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen, onClose]);

  if (!isOpen || commands.length === 0) return null;

  return (
    <div
      ref={listRef}
      dir="rtl"
      lang="ar"
      role="listbox"
      aria-label="قائمة الأوامر"
      className={cn(
        "absolute bottom-full mb-2 end-0 z-50 w-72",
        "rounded-lg border bg-popover p-1 shadow-lg",
        "max-h-64 overflow-y-auto",
        className
      )}
    >
      <div className="px-3 py-1.5 text-xs text-muted-foreground border-b mb-1">
        الأوامر المتاحة
      </div>
      {commands.map((cmd, i) => (
        <button
          key={cmd.trigger}
          type="button"
          role="option"
          aria-selected={i === selectedIndex}
          data-command-item
          className={cn(
            "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors text-right",
            i === selectedIndex
              ? "bg-accent text-accent-foreground"
              : "hover:bg-accent/50"
          )}
          onClick={() => onSelect(cmd)}
          onMouseEnter={() => setSelectedIndex(i)}
        >
          <span className="font-semibold text-primary shrink-0">
            @{cmd.trigger}
          </span>
          <span className="text-muted-foreground text-xs truncate">
            {cmd.description}
          </span>
          {cmd.is_modifier && (
            <span className="me-auto text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded-full font-medium">
              معدّل
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
