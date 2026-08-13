"use client";

import * as React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { useMediaQuery } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";

const TooltipProvider = TooltipPrimitive.Provider;

/**
 * On a touch device a tooltip has no hover to open it, so Radix falls back to
 * long-press — and, worse, the first tap on the trigger only dismisses the
 * tooltip layer instead of firing the button. Every icon button in the chat
 * thread and the workspace action bar therefore costs a dead first tap.
 *
 * So on a COARSE pointer this file degrades to nothing: `Tooltip` renders no
 * Radix root, `TooltipTrigger` renders its child untouched, and
 * `TooltipContent` renders null. The label survives regardless — every trigger
 * in this codebase carries an `aria-label`, which is what a screen reader
 * reads anyway. (Every call site is `<TooltipTrigger asChild>` with a single
 * element child; the non-`asChild` branch keeps that from being a silent
 * assumption.)
 *
 * Input modality, NOT viewport: a tablet with a trackpad keeps its tooltips.
 */
const CoarsePointerContext = React.createContext(false);

function useCoarsePointer(): boolean {
  return useMediaQuery("(pointer: coarse)");
}

const Tooltip = ({
  children,
  ...props
}: React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Root>) => {
  const coarse = useCoarsePointer();
  if (coarse) {
    return (
      <CoarsePointerContext.Provider value>
        {children}
      </CoarsePointerContext.Provider>
    );
  }
  return <TooltipPrimitive.Root {...props}>{children}</TooltipPrimitive.Root>;
};
Tooltip.displayName = "Tooltip";

const TooltipTrigger = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Trigger>
>(({ asChild, children, ...props }, ref) => {
  const coarse = React.useContext(CoarsePointerContext);
  if (coarse) {
    // `asChild` → the child IS the interactive element; hand it back as-is so
    // the first tap lands on it. Otherwise keep a real button in the tree.
    if (asChild) return <>{children}</>;
    return (
      <button ref={ref} type="button" {...props}>
        {children}
      </button>
    );
  }
  return (
    <TooltipPrimitive.Trigger ref={ref} asChild={asChild} {...props}>
      {children}
    </TooltipPrimitive.Trigger>
  );
});
TooltipTrigger.displayName = TooltipPrimitive.Trigger.displayName;

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => {
  const coarse = React.useContext(CoarsePointerContext);
  if (coarse) return null;
  return (
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        // z-[70]: above the mobile workspace overlay (z-[60]) — see dialog.tsx.
        "z-[70] overflow-hidden rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2",
        className,
      )}
      {...props}
    />
  );
});
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
