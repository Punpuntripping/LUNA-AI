"use client";

import { useState } from "react";
import {
  BookMarked,
  ChevronDown,
  CreditCard,
  Gauge,
  Info,
  KeyRound,
  LogOut,
  Receipt,
  Settings,
  SlidersHorizontal,
  Sparkles,
  User,
  UserCog,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useEduStore } from "@/stores/edu-store";
import { EDU_SYLLABUS } from "@/components/edu/edu-syllabus";
import { useUsageDialogStore } from "@/stores/usage-dialog-store";
import { useConversationSettingsDialogStore } from "@/stores/conversation-settings-dialog-store";
import { LEGAL_ROUTES } from "@/lib/legal";
import { userDisplayName } from "@/lib/user-name";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { RedeemCodeDialog } from "@/components/Settings/RedeemCodeDialog";
import { AccountSettingsDialog } from "@/components/Settings/AccountSettingsDialog";
import { PaymentHistoryDialog } from "@/components/Settings/PaymentHistoryDialog";

/**
 * عن ريحان expandable — a mirror of the public header's «عن ريحان» dropdown
 * with «السياسات» folded in, so the settings popover reflects the header
 * instead of duplicating loose rows (bottom group reduced 5 → 3, 2026-08-02).
 */
const ABOUT_LINKS = [
  { label: "عن ريحان", href: "/about_us", testId: "sidebar-settings-about-hub" },
  { label: "لمن ريحان؟", href: "/audiences", testId: "sidebar-settings-audiences" },
  {
    label: "ريحان مقابل ChatGPT",
    href: "/vs-chatgpt",
    testId: "sidebar-settings-vs-chatgpt",
  },
  {
    label: "الشروط والأحكام",
    href: LEGAL_ROUTES.terms,
    testId: "sidebar-settings-terms",
  },
  {
    label: "سياسة الخصوصية",
    href: LEGAL_ROUTES.privacy,
    testId: "sidebar-settings-privacy",
  },
] as const;

export function SidebarFooter() {
  const router = useRouter();
  const { user, logout } = useAuthStore();
  // «حدود الاستخدام» and «إعدادات المحادثة» are the two dialogs here NOT held
  // in local state: each is mounted once by the surrounding app shell and
  // opened from two places (this row and an edu lesson's action button). A
  // dialog mounted HERE is unreachable on a phone with the drawer closed —
  // the Sheet unmounts its children. See usage-dialog-store for the full note.
  const openUsageDialog = useUsageDialogStore((s) => s.open);
  const openConversationSettings = useConversationSettingsDialogStore(
    (s) => s.open,
  );
  const [redeemOpen, setRedeemOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [receiptsOpen, setReceiptsOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [lessonsOpen, setLessonsOpen] = useState(false);
  // The settings popover is CONTROLLED so a lesson click can close the whole
  // menu stack. Without it the flyout and its parent stay open on top of the
  // lesson card the click just summoned.
  const [settingsOpen, setSettingsOpen] = useState(false);

  const closeMenus = () => {
    setLessonsOpen(false);
    setSettingsOpen(false);
  };

  const handleLogout = async () => {
    await logout();
    router.push("/login");
  };

  return (
    <div>
      <Separator />
      {/* The drawer's last row sits on the home indicator once the sidebar is a
          full-height sheet on a phone, so the bottom padding carries
          `env(safe-area-inset-bottom)` (0 everywhere else). */}
      <div className="flex items-center justify-between p-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        <div className="flex items-center gap-2 min-w-0">
          <div className="flex h-7 w-7 max-md:h-9 max-md:w-9 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <User className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-medium text-sidebar-foreground truncate">
              {userDisplayName(user) || "مستخدم"}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <Popover
            open={settingsOpen}
            onOpenChange={(open) => {
              setSettingsOpen(open);
              // Reopen collapsed — a stale-expanded عن ريحان section can push
              // the popover taller than a short viewport and clip its top rows.
              if (!open) {
                setAboutOpen(false);
                setLessonsOpen(false);
              }
            }}
          >
            <Tooltip>
              <TooltipTrigger asChild>
                <PopoverTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 max-md:h-9 max-md:w-9 shrink-0 text-muted-foreground"
                    aria-label="الإعدادات"
                    data-testid="sidebar-settings-trigger"
                  >
                    <Settings className="h-3.5 w-3.5" />
                  </Button>
                </PopoverTrigger>
              </TooltipTrigger>
              <TooltipContent side="top">
                <p>الإعدادات</p>
              </TooltipContent>
            </Tooltip>
            <PopoverContent
              side="top"
              align="end"
              className="max-h-[min(80dvh,34rem)] w-72 max-w-[calc(100vw-2rem)] overflow-y-auto"
              data-testid="sidebar-settings-popover"
            >
              <div className="flex flex-col gap-3" dir="rtl">
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={openConversationSettings}
                  data-testid="sidebar-settings-conversation-trigger"
                >
                  <span className="flex items-center gap-2">
                    <SlidersHorizontal className="h-4 w-4" />
                    إعدادات المحادثة
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={openUsageDialog}
                  data-testid="sidebar-settings-usage-trigger"
                >
                  <span className="flex items-center gap-2">
                    <Gauge className="h-4 w-4" />
                    حدود الاستخدام
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={() => setRedeemOpen(true)}
                  data-testid="sidebar-settings-redeem-trigger"
                >
                  <span className="flex items-center gap-2">
                    <KeyRound className="h-4 w-4" />
                    تفعيل برمز
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={() => setAccountOpen(true)}
                  data-testid="sidebar-settings-account"
                >
                  <span className="flex items-center gap-2">
                    <UserCog className="h-4 w-4" />
                    إعدادات الحساب
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={() => router.push("/pricing")}
                  data-testid="sidebar-settings-pricing"
                >
                  <span className="flex items-center gap-2">
                    <CreditCard className="h-4 w-4" />
                    ترقية باقتك
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                {/* سجل المدفوعات sits directly under ترقية باقتك — buy and
                    receipts are the same errand, and the 24h refund button
                    lives inside this dialog, so it must be findable without
                    an email to support. */}
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={() => setReceiptsOpen(true)}
                  data-testid="sidebar-settings-receipts-trigger"
                >
                  <span className="flex items-center gap-2">
                    <Receipt className="h-4 w-4" />
                    سجل المدفوعات
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                {/* «المكتبة القانونية» was previously kept OUT of this popover
                    on the reasoning that settings is for account-level actions
                    and the library is a content surface reachable from the
                    global header. Added back by owner request (2026-08-16) —
                    the header is not visible from inside the chat shell, which
                    is where users actually are when they want it. «مكتبتي»
                    stays out; it is still a sidebar tab. */}
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={() => window.open("/library", "_blank")}
                  data-testid="sidebar-settings-library"
                >
                  <span className="flex items-center gap-2">
                    <BookMarked className="h-4 w-4" />
                    المكتبة القانونية
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                <Separator />
                {/* Bottom group = 2 expandables mirroring the public header:
                    عن ريحان (السياسات folded in) · اكتشف ريحان (the 8 lessons
                    of «سلسلة تعلّم ريحان», folded in — 2026-08-16). */}
                <div>
                  <Button
                    variant="ghost"
                    className="w-full justify-between gap-2 px-2 text-sm font-medium"
                    onClick={() => setAboutOpen((open) => !open)}
                    aria-expanded={aboutOpen}
                    data-testid="sidebar-settings-about"
                  >
                    <span className="flex items-center gap-2">
                      <Info className="h-4 w-4" />
                      عن ريحان والسياسات
                    </span>
                    <ChevronDown
                      className={cn(
                        "h-4 w-4 text-muted-foreground transition-transform",
                        aboutOpen && "rotate-180",
                      )}
                    />
                  </Button>
                  {aboutOpen && (
                    <div className="flex flex-col gap-0.5 pb-1 pe-2 ps-8">
                      {ABOUT_LINKS.map((link) => (
                        <button
                          key={link.href}
                          type="button"
                          onClick={() => window.open(link.href, "_blank")}
                          data-testid={link.testId}
                          className="rounded-md px-2 py-1.5 text-start text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                        >
                          {link.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {/* «اكتشف ريحان» — now the browse surface for «سلسلة تعلّم
                    ريحان». The series drips these one per 4 turns; this is
                    where a user reads one on demand instead of waiting, or
                    re-reads one already delivered.

                    Replaces the old row that opened the «اتعرف على ريحان»
                    dialog. That dialog is NOT orphaned: it auto-opens after
                    payment (edu_series §8 A2) and is still reachable as the
                    first entry below, so the only manual re-entry survives.

                    «جولة المخرجات» was deleted from this popover by owner
                    request (2026-08-16). The tour itself still exists and still
                    auto-runs once over the demo conversation — only its manual
                    re-entry is gone. */}
                {/* A FLYOUT, not an inline accordion: the lessons are their own
                    list, and pushing 10 rows into a popover that already holds
                    8 would make the settings menu scroll on any short viewport.

                    `side="left"` is physical, and in this RTL layout the sidebar
                    sits on the RIGHT — so left is away from it. `collisionPadding`
                    lets Radix flip to the right when there is no room (a narrow
                    viewport), which is also what keeps this usable on a phone. */}
                <Popover open={lessonsOpen} onOpenChange={setLessonsOpen}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="ghost"
                      className="w-full justify-between gap-2 px-2 text-sm font-medium"
                      aria-expanded={lessonsOpen}
                      data-testid="sidebar-settings-lessons"
                    >
                      <span className="flex items-center gap-2">
                        <Sparkles className="h-4 w-4" />
                        اكتشف ريحان
                      </span>
                      <span className="text-muted-foreground">›</span>
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent
                    side="left"
                    align="start"
                    sideOffset={8}
                    collisionPadding={8}
                    className="max-h-[min(70dvh,28rem)] w-64 max-w-[calc(100vw-2rem)] overflow-y-auto p-1.5"
                    data-testid="sidebar-settings-lessons-panel"
                  >
                    <div className="flex flex-col gap-0.5" dir="rtl">
                      <button
                        type="button"
                        onClick={() => {
                          useOnboardingStore.getState().open();
                          closeMenus();
                        }}
                        data-testid="sidebar-settings-onboarding-trigger"
                        className="rounded-md px-2 py-1.5 text-start text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                      >
                        جولة التعريف السريعة
                      </button>
                      <Separator className="my-1" />
                      {EDU_SYLLABUS.map((lesson) => (
                        <button
                          key={lesson.id}
                          type="button"
                          onClick={() => {
                            // Close the menu stack, then show. Order is
                            // cosmetic only — `showLesson` deliberately does
                            // NOT gate on an open modal, because this popover
                            // is itself `role="dialog"` and would have blocked
                            // its own lesson. See the note in edu-store.
                            closeMenus();
                            useEduStore.getState().showLesson(lesson.id);
                          }}
                          data-testid={`sidebar-settings-lesson-${lesson.id}`}
                          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-start text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                        >
                          <lesson.icon className="h-3.5 w-3.5 shrink-0" />
                          <span className="truncate">{lesson.title}</span>
                        </button>
                      ))}
                      <Separator className="my-1" />
                      <button
                        type="button"
                        onClick={() => {
                          window.open("/learn", "_blank");
                          closeMenus();
                        }}
                        data-testid="sidebar-settings-learn"
                        className="rounded-md px-2 py-1.5 text-start text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                      >
                        المزيد من الأدلة
                      </button>
                    </div>
                  </PopoverContent>
                </Popover>
              </div>
            </PopoverContent>
          </Popover>
          {/* «حدود الاستخدام» and «إعدادات المحادثة» are NOT mounted here —
              they live in SidebarDialogs, outside the mobile Sheet, because
              edu lesson buttons must be able to open them with the drawer
              shut. The three below are sidebar-only and stay local. */}
          <RedeemCodeDialog open={redeemOpen} onOpenChange={setRedeemOpen} />
          <AccountSettingsDialog
            open={accountOpen}
            onOpenChange={setAccountOpen}
          />
          <PaymentHistoryDialog
            open={receiptsOpen}
            onOpenChange={setReceiptsOpen}
          />
          <ThemeToggle />
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 max-md:h-9 max-md:w-9 shrink-0 text-muted-foreground hover:text-destructive"
                onClick={handleLogout}
                aria-label="تسجيل الخروج"
              >
                <LogOut className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">
              <p>تسجيل الخروج</p>
            </TooltipContent>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
