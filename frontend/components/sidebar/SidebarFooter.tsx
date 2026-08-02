"use client";

import { useState } from "react";
import {
  ChevronDown,
  CreditCard,
  Gauge,
  Info,
  KeyRound,
  LogOut,
  Settings,
  SlidersHorizontal,
  Sparkles,
  User,
  UserCog,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { LEGAL_ROUTES } from "@/lib/legal";
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
import { UsageLimitsDialog } from "@/components/Settings/UsageLimitsDialog";
import { RedeemCodeDialog } from "@/components/Settings/RedeemCodeDialog";
import { ConversationSettingsDialog } from "@/components/Settings/ConversationSettingsDialog";
import { AccountSettingsDialog } from "@/components/Settings/AccountSettingsDialog";

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
  const [usageOpen, setUsageOpen] = useState(false);
  const [redeemOpen, setRedeemOpen] = useState(false);
  const [conversationOpen, setConversationOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    router.push("/login");
  };

  return (
    <div>
      <Separator />
      <div className="flex items-center justify-between p-3">
        <div className="flex items-center gap-2 min-w-0">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <User className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-medium text-sidebar-foreground truncate">
              {user?.full_name_ar || user?.email || "مستخدم"}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <Popover
            onOpenChange={(open) => {
              // Reopen collapsed — a stale-expanded عن ريحان section can push
              // the popover taller than a short viewport and clip its top rows.
              if (!open) setAboutOpen(false);
            }}
          >
            <Tooltip>
              <TooltipTrigger asChild>
                <PopoverTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 shrink-0 text-muted-foreground"
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
              className="max-h-[min(80vh,34rem)] w-72 overflow-y-auto"
              data-testid="sidebar-settings-popover"
            >
              <div className="flex flex-col gap-3" dir="rtl">
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={() => setConversationOpen(true)}
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
                  onClick={() => setUsageOpen(true)}
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
                {/* «المكتبة القانونية» and «مكتبتي» deliberately do NOT live
                    here. Settings is for account-level actions; both are
                    content surfaces, so they belong in the nav — مكتبتي is a
                    sidebar tab under مدوناتي, and the public library is reached
                    from the global header. */}
                <Separator />
                {/* Bottom group = 3 rows mirroring the public header:
                    عن ريحان (expandable, السياسات folded in) · اكتشف ريحان
                    (tour popup + أدلة /learn) — replaced the loose عن ريحان /
                    الشروط / الخصوصية rows (5 → 3, 2026-08-02). */}
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
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    className="flex-1 justify-start gap-2 px-2 text-sm font-medium"
                    onClick={() => useOnboardingStore.getState().open()}
                    data-testid="sidebar-settings-onboarding-trigger"
                  >
                    <Sparkles className="h-4 w-4" />
                    اكتشف ريحان
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="shrink-0 px-2 text-xs text-muted-foreground"
                    onClick={() => window.open("/learn", "_blank")}
                    aria-label="أدلة اكتشف ريحان"
                    data-testid="sidebar-settings-learn"
                  >
                    المزيد
                  </Button>
                </div>
              </div>
            </PopoverContent>
          </Popover>
          <UsageLimitsDialog open={usageOpen} onOpenChange={setUsageOpen} />
          <RedeemCodeDialog open={redeemOpen} onOpenChange={setRedeemOpen} />
          <ConversationSettingsDialog
            open={conversationOpen}
            onOpenChange={setConversationOpen}
          />
          <AccountSettingsDialog
            open={accountOpen}
            onOpenChange={setAccountOpen}
          />
          <ThemeToggle />
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                onClick={handleLogout}
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
