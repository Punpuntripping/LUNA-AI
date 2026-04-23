"use client";

import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ScrollToBottomProps {
  visible: boolean;
  newMessageCount?: number;
  onClick: () => void;
  className?: string;
}

/**
 * Floating action button that appears when the user scrolls up in the chat.
 * Clicking it smooth-scrolls back to the bottom of the message list.
 * Optionally shows a badge with the count of new messages received while scrolled up.
 */
export function ScrollToBottom({
  visible,
  newMessageCount = 0,
  onClick,
  className,
}: ScrollToBottomProps) {
  return (
    <div
      className={cn(
        "absolute bottom-4 left-1/2 -translate-x-1/2 z-30 pointer-events-auto",
        "transition-all duration-200 ease-out",
        visible
          ? "opacity-100 translate-y-0"
          : "opacity-0 translate-y-2 pointer-events-none",
        className
      )}
    >
      <Button
        variant="secondary"
        size="icon"
        onClick={onClick}
        aria-label="الانتقال للأسفل"
        className={cn(
          "h-10 w-10 rounded-full shadow-lg",
          "bg-background/80 backdrop-blur-sm border",
          "hover:bg-background/95",
          "transition-colors duration-150"
        )}
      >
        {/* Badge for new message count */}
        {newMessageCount > 0 && (
          <span
            className={cn(
              "absolute -top-1.5 -end-1.5 flex items-center justify-center",
              "min-w-[20px] h-5 px-1 rounded-full",
              "bg-primary text-primary-foreground text-[11px] font-medium",
              "animate-in fade-in zoom-in-75 duration-150"
            )}
          >
            {newMessageCount > 99 ? "99+" : newMessageCount}
          </span>
        )}
        <ChevronDown className="h-5 w-5" />
      </Button>
    </div>
  );
}
