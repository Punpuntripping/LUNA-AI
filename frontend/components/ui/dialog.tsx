"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogPortal = DialogPrimitive.Portal;
const DialogClose = DialogPrimitive.Close;

/**
 * ⚠ z-[70], not z-50 — see the layering note in `ChatLayoutClient`.
 *
 * A modal Radix layer sets `pointer-events: none` on `<body>` and locks scroll
 * for as long as it is open. If it renders BENEATH full-screen app chrome (the
 * mobile workspace overlay is `z-[60]`), the user sees no dialog and can tap
 * nothing: the app reads as frozen, with no way out on a phone since dismissing
 * needs either a visible overlay to tap or an Esc key. So every portalled layer
 * in this directory sits above 60, and app chrome stays below it.
 */
const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-[70] bg-black/80 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className
    )}
    {...props}
  />
));
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName;

/**
 * Below `sm` a centred modal is the wrong shape: it floats mid-screen with the
 * page dimmed on all four sides, its actions land in the middle of the
 * viewport (nowhere near the thumb), and a tall body gets squeezed from both
 * ends. `presentation="mobileSheet"` docks it to the bottom edge instead — the
 * pattern the anon CTA already uses — and it reverts to the centred dialog at
 * `sm` and up. Opt-in, because a two-line confirm reads better centred.
 */
// Written mobile-first + restored at `sm`, NOT as `max-sm:` overrides: these
// are appended after the base string in the same `cn()`, so `twMerge` resolves
// each pair (`top-[50%]` → `top-auto`, `translate-y-[-50%]` → `translate-y-0`)
// by dropping the base class outright. A `max-sm:` prefix would read as a
// different utility to twMerge and both would ship, leaving the winner to CSS
// ordering. Horizontal centring is left alone — `w-full` already pins the
// panel to the viewport width on a phone.
const MOBILE_SHEET_CLASSES =
  "bottom-0 top-auto translate-y-0 max-h-[85dvh] rounded-t-2xl rounded-b-none pb-[calc(1.5rem+env(safe-area-inset-bottom))] sm:bottom-auto sm:top-[50%] sm:translate-y-[-50%] sm:max-h-[calc(100dvh-2rem)] sm:rounded-lg sm:pb-6";

interface DialogContentProps
  extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  /** `mobileSheet` docks the dialog to the bottom edge below `sm`. */
  presentation?: "centered" | "mobileSheet";
}

const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  DialogContentProps
>(({ className, children, presentation = "centered", ...props }, ref) => (
  <DialogPortal>
    <DialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        // `max-h` + `overflow-y-auto`: the content is centred on a fixed layer
      // with no scroll of its own, and Radix has already locked the page
      // scroll — so anything taller than the viewport used to be clipped off
      // both ends with no way to reach it. Bites hardest on a phone, where the
      // source-reveal dialog stacks a title, a 60vh body and an action bar.
      "fixed start-[50%] top-[50%] z-[70] grid max-h-[calc(100dvh-2rem)] w-full max-w-lg translate-x-[-50%] rtl:-translate-x-[-50%] translate-y-[-50%] gap-4 overflow-y-auto border bg-background p-6 shadow-lg duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[state=closed]:slide-out-to-left-1/2 data-[state=closed]:slide-out-to-top-[48%] data-[state=open]:slide-in-from-left-1/2 data-[state=open]:slide-in-from-top-[48%] sm:rounded-lg",
        presentation === "mobileSheet" && MOBILE_SHEET_CLASSES,
        className
      )}
      {...props}
    >
      {children}
      {/* `p-2 -m-2` — the glyph is 16px, i.e. a 16px tap target. The padding
          buys a 32px hit area and the negative margin gives the layout back,
          so nothing moves on screen. */}
      <DialogPrimitive.Close className="absolute end-4 top-4 rounded-sm p-2 -m-2 opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none data-[state=open]:bg-accent data-[state=open]:text-muted-foreground">
        <X className="h-4 w-4" />
        <span className="sr-only">إغلاق</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPortal>
));
DialogContent.displayName = DialogPrimitive.Content.displayName;

const DialogHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      "flex flex-col space-y-1.5 text-center sm:text-start",
      className
    )}
    {...props}
  />
);
DialogHeader.displayName = "DialogHeader";

const DialogFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      "flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 rtl:sm:space-x-reverse sm:space-x-reverse rtl:sm:space-x-reverse",
      className
    )}
    {...props}
  />
);
DialogFooter.displayName = "DialogFooter";

const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn(
      "text-lg font-semibold leading-none tracking-tight",
      className
    )}
    {...props}
  />
));
DialogTitle.displayName = DialogPrimitive.Title.displayName;

const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-sm text-muted-foreground", className)}
    {...props}
  />
));
DialogDescription.displayName = DialogPrimitive.Description.displayName;

export {
  Dialog,
  DialogPortal,
  DialogOverlay,
  DialogClose,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
};
