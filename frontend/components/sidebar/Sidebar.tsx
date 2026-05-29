"use client";

import { useEffect } from "react";
import { Menu, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useConversations, useCreateConversation } from "@/hooks/use-conversations";
import { useCases } from "@/hooks/use-cases";
import { useTemplates } from "@/hooks/use-templates";
import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { ConversationList } from "@/components/sidebar/ConversationList";
import { CaseList } from "@/components/sidebar/CaseList";
import { TemplateList } from "@/components/sidebar/TemplateList";
import { Button } from "@/components/ui/button";
import { TooltipProvider, Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface NavPillProps {
  label: string;
  count: number | undefined;
  active: boolean;
  onSelect: () => void;
  onCreate?: () => void;
  createTooltip?: string;
  isCreating?: boolean;
}

function NavPill({ label, count, active, onSelect, onCreate, createTooltip, isCreating }: NavPillProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "group relative flex items-center justify-between gap-2 rounded-full px-3.5 py-2 cursor-pointer transition-colors",
        "border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary/60 bg-primary/5 text-foreground"
          : "border-transparent text-muted-foreground hover:bg-accent/50 hover:text-foreground"
      )}
      aria-pressed={active}
    >
      <span className="text-sm font-medium truncate">{label}</span>

      <div className="flex items-center gap-1.5 shrink-0">
        {typeof count === "number" && (
          <span
            className={cn(
              "text-[11px] tabular-nums font-medium tracking-tight",
              active ? "text-primary" : "text-muted-foreground/80"
            )}
          >
            {count}
          </span>
        )}

        {onCreate && createTooltip && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onCreate();
                }}
                disabled={isCreating}
                aria-label={createTooltip}
                className={cn(
                  "flex h-5 w-5 items-center justify-center rounded-full transition-all",
                  "text-muted-foreground hover:bg-primary hover:text-primary-foreground",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  "disabled:opacity-50 disabled:cursor-not-allowed",
                  // Hidden by default; appear on pill hover/focus or when active
                  active
                    ? "opacity-100"
                    : "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100"
                )}
              >
                <Plus className="h-3 w-3" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>{createTooltip}</p>
            </TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  );
}

export function Sidebar() {
  const router = useRouter();
  const {
    isOpen,
    activeTab,
    setActiveTab,
    setOpen,
    setSelectedConversation,
    setCreateCaseDialogOpen,
    setCreateTemplateDialogOpen,
  } = useSidebarStore();

  const { data: convData } = useConversations(null);
  const { data: caseData } = useCases("active");
  const { data: templateData } = useTemplates();
  const createConversation = useCreateConversation();

  const conversationCount = convData?.conversations?.length;
  const caseCount = caseData?.cases?.length;
  const templateCount = templateData?.templates?.length;

  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth < 768) {
        setOpen(false);
      }
    };
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [setOpen]);

  const handleNewConversation = () => {
    if (activeTab !== "conversations") setActiveTab("conversations");
    createConversation.mutate(
      { case_id: null },
      {
        onSuccess: (resp) => {
          setSelectedConversation(resp.conversation.conversation_id);
          router.push(`/chat/${resp.conversation.conversation_id}`);
        },
      }
    );
  };

  const handleNewCase = () => {
    if (activeTab !== "cases") setActiveTab("cases");
    setCreateCaseDialogOpen(true);
  };

  const handleNewTemplate = () => {
    if (activeTab !== "templates") setActiveTab("templates");
    setCreateTemplateDialogOpen(true);
  };

  return (
    <TooltipProvider delayDuration={300}>
      {!isOpen && (
        <div className="fixed top-3 start-3 z-50 md:hidden">
          <Button variant="outline" size="icon" className="h-9 w-9" onClick={() => setOpen(true)}>
            <Menu className="h-4 w-4" />
          </Button>
        </div>
      )}

      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      <aside
        className={cn(
          "flex flex-col bg-sidebar border-e border-sidebar-border transition-all duration-200 ease-in-out z-50",
          "relative",
          isOpen ? "w-72" : "w-0 overflow-hidden",
          "max-md:fixed max-md:inset-y-0 max-md:start-0",
          isOpen ? "max-md:w-72" : "max-md:w-0"
        )}
      >
        <SidebarHeader />

        {/* Pill nav */}
        <div className="flex flex-col gap-1.5 px-3 pt-3 pb-2 shrink-0">
          <NavPill
            label="المحادثات"
            count={conversationCount}
            active={activeTab === "conversations"}
            onSelect={() => setActiveTab("conversations")}
            onCreate={handleNewConversation}
            createTooltip="محادثة جديدة"
            isCreating={createConversation.isPending}
          />
          <NavPill
            label="القضايا"
            count={caseCount}
            active={activeTab === "cases"}
            onSelect={() => setActiveTab("cases")}
            onCreate={handleNewCase}
            createTooltip="قضية جديدة"
          />
          <NavPill
            label="قوالبي"
            count={templateCount}
            active={activeTab === "templates"}
            onSelect={() => setActiveTab("templates")}
            onCreate={handleNewTemplate}
            createTooltip="قالب جديد"
          />
        </div>

        {/* Single panel — swaps content based on active tab */}
        <div className="flex-1 flex flex-col min-h-0">
          {activeTab === "conversations" ? (
            <ConversationList />
          ) : activeTab === "cases" ? (
            <CaseList />
          ) : (
            <TemplateList />
          )}
        </div>

        <SidebarFooter />
      </aside>
    </TooltipProvider>
  );
}
