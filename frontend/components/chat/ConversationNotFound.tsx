"use client";

import Link from "next/link";
import { MessageSquareOff } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * What `/chat/<id>` renders when the backend answers 404.
 *
 * The route param outlives the session that minted it, so a conversation id
 * can survive into a browser that is no longer allowed to open it — an account
 * switch (the identity-scoped `?next=` in `lib/safe-next` closes the common
 * path, a bookmark or a pasted link does not), a deletion in another tab, or a
 * link shared between two people. Before this existed the page rendered as a
 * perfectly ordinary empty thread with a live composer, and the first thing the
 * user learned was a red «المحادثة غير موجودة» band AFTER typing a message that
 * 404'd on send. Saying it up front, with the way out attached, is the whole
 * job here.
 *
 * ⚠ The copy must not assert deletion. A 404 on this route is equally "not
 * yours" and "not there any more", the API deliberately does not distinguish
 * them (saying which would confirm the existence of another account's
 * conversation), and the client cannot tell either.
 */
export function ConversationNotFound({ className }: { className?: string }) {
  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn(
        "flex h-full flex-col items-center justify-center gap-3 px-6 text-center",
        className,
      )}
    >
      <MessageSquareOff
        aria-hidden="true"
        className="h-10 w-10 text-muted-foreground/40"
      />
      <p className="text-sm font-medium">هذه المحادثة غير متاحة</p>
      <p className="max-w-sm text-xs text-muted-foreground">
        قد تكون محذوفة أو تخصّ حساباً آخر. ابدأ محادثة جديدة للمتابعة.
      </p>
      <Link
        href="/chat"
        className={cn(buttonVariants({ size: "sm" }), "mt-1")}
      >
        محادثة جديدة
      </Link>
    </div>
  );
}
