"use client";

import { useState } from "react";
import {
  CreditCard,
  FileText,
  Gauge,
  KeyRound,
  LogOut,
  Settings,
  ShieldCheck,
  User,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { LEGAL_ROUTES } from "@/lib/legal";
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
import { DetailLevelToggle } from "@/components/Settings/DetailLevelToggle";
import { UsageLimitsDialog } from "@/components/Settings/UsageLimitsDialog";
import { RedeemCodeDialog } from "@/components/Settings/RedeemCodeDialog";

export function SidebarFooter() {
  const router = useRouter();
  const { user, logout } = useAuthStore();
  const [usageOpen, setUsageOpen] = useState(false);
  const [redeemOpen, setRedeemOpen] = useState(false);

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
          <Popover>
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
              className="w-72"
              data-testid="sidebar-settings-popover"
            >
              <div className="flex flex-col gap-3" dir="rtl">
                <h3 className="text-sm font-semibold text-foreground">
                  مستوى التفصيل
                </h3>
                <DetailLevelToggle />
                <Separator />
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
                  onClick={() => router.push("/pricing")}
                  data-testid="sidebar-settings-pricing"
                >
                  <span className="flex items-center gap-2">
                    <CreditCard className="h-4 w-4" />
                    الباقات والأسعار
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                <Separator />
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={() => window.open(LEGAL_ROUTES.terms, "_blank")}
                  data-testid="sidebar-settings-terms"
                >
                  <span className="flex items-center gap-2">
                    <FileText className="h-4 w-4" />
                    الشروط والأحكام
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
                <Button
                  variant="ghost"
                  className="w-full justify-between gap-2 px-2 text-sm font-medium"
                  onClick={() => window.open(LEGAL_ROUTES.privacy, "_blank")}
                  data-testid="sidebar-settings-privacy"
                >
                  <span className="flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4" />
                    سياسة الخصوصية
                  </span>
                  <span className="text-muted-foreground">›</span>
                </Button>
              </div>
            </PopoverContent>
          </Popover>
          <UsageLimitsDialog open={usageOpen} onOpenChange={setUsageOpen} />
          <RedeemCodeDialog open={redeemOpen} onOpenChange={setRedeemOpen} />
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
