"use client";

import * as React from "react";
import * as SheetPrimitive from "@radix-ui/react-dialog";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Sheet — a Radix Dialog that enters from an edge instead of the centre.
 *
 * Sides are LOGICAL, not physical: `start` is the right edge in the app's RTL
 * layout (and the left edge in an LTR context), so the sidebar drawer slides
 * out of the same edge the desktop rail occupies. `bottom` is the phone
 * bottom-sheet used by pickers and menus.
 *
 * ⚠ z-[70] — the same tier as every other portalled layer in this directory,
 * and for the same reason (see the layering note in `ChatLayoutClient`): app
 * chrome tops out at z-[60] (the mobile workspace overlay), and a modal Radix
 * layer that renders BENEATH it locks page scroll and kills pointer events
 * while showing the user nothing — a frozen app with no way out on a phone.
 *
 * `viewportFit: "cover"` means the bottom sheet paints under the home
 * indicator, so the bottom variant carries `env(safe-area-inset-bottom)` in
 * its padding.
 */
const Sheet = SheetPrimitive.Root;
const SheetTrigger = SheetPrimitive.Trigger;
const SheetClose = SheetPrimitive.Close;
const SheetPortal = SheetPrimitive.Portal;

const SheetOverlay = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-[70] bg-black/60 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className,
    )}
    {...props}
  />
));
SheetOverlay.displayName = SheetPrimitive.Overlay.displayName;

const sheetVariants = cva(
  "fixed z-[70] flex flex-col gap-4 bg-background shadow-lg transition ease-in-out data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:duration-300 data-[state=open]:duration-500",
  {
    variants: {
      side: {
        // Logical inline-start: right in RTL, left in LTR. The OFFSET is
        // logical (`start-0`), but tailwindcss-animate only ships physical
        // slide keyframes — hence the `rtl:`/`ltr:` pair, so the panel always
        // travels out of the edge it is pinned to.
        start:
          "inset-y-0 start-0 h-full w-3/4 max-w-sm border-e rtl:data-[state=closed]:slide-out-to-right rtl:data-[state=open]:slide-in-from-right ltr:data-[state=closed]:slide-out-to-left ltr:data-[state=open]:slide-in-from-left",
        end: "inset-y-0 end-0 h-full w-3/4 max-w-sm border-s rtl:data-[state=closed]:slide-out-to-left rtl:data-[state=open]:slide-in-from-left ltr:data-[state=closed]:slide-out-to-right ltr:data-[state=open]:slide-in-from-right",
        bottom:
          "inset-x-0 bottom-0 max-h-[85dvh] rounded-t-2xl border-t pb-[env(safe-area-inset-bottom)] data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom",
        top: "inset-x-0 top-0 rounded-b-2xl border-b pt-[env(safe-area-inset-top)] data-[state=closed]:slide-out-to-top data-[state=open]:slide-in-from-top",
      },
    },
    defaultVariants: {
      side: "start",
    },
  },
);

interface SheetContentProps
  extends React.ComponentPropsWithoutRef<typeof SheetPrimitive.Content>,
    VariantProps<typeof sheetVariants> {
  /** Renders the ✕ in the corner. Off for surfaces with their own close row. */
  showClose?: boolean;
}

const SheetContent = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Content>,
  SheetContentProps
>(({ side = "start", showClose = true, className, children, ...props }, ref) => (
  <SheetPortal>
    <SheetOverlay />
    <SheetPrimitive.Content
      ref={ref}
      className={cn(sheetVariants({ side }), className)}
      {...props}
    >
      {children}
      {showClose && (
        <SheetPrimitive.Close
          // `p-2 -m-2` — a 16px glyph is a 16px tap target; the negative
          // margin buys the 44px floor back without moving the icon.
          className="absolute end-4 top-4 rounded-sm p-2 -m-2 opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none"
        >
          <X className="h-4 w-4" />
          <span className="sr-only">إغلاق</span>
        </SheetPrimitive.Close>
      )}
    </SheetPrimitive.Content>
  </SheetPortal>
));
SheetContent.displayName = SheetPrimitive.Content.displayName;

const SheetHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn("flex flex-col space-y-1.5 text-start", className)}
    {...props}
  />
);
SheetHeader.displayName = "SheetHeader";

const SheetFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      "flex flex-col-reverse gap-2 sm:flex-row sm:justify-end",
      className,
    )}
    {...props}
  />
);
SheetFooter.displayName = "SheetFooter";

const SheetTitle = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Title>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Title
    ref={ref}
    className={cn("text-lg font-semibold text-foreground", className)}
    {...props}
  />
));
SheetTitle.displayName = SheetPrimitive.Title.displayName;

const SheetDescription = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Description>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Description
    ref={ref}
    className={cn("text-sm text-muted-foreground", className)}
    {...props}
  />
));
SheetDescription.displayName = SheetPrimitive.Description.displayName;

export {
  Sheet,
  SheetPortal,
  SheetOverlay,
  SheetTrigger,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetFooter,
  SheetTitle,
  SheetDescription,
};
