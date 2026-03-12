"use client";

import { useState } from "react";
import { FolderOpen, Plus, Loader2 } from "lucide-react";
import { useCases, useCreateCase } from "@/hooks/use-cases";
import { CaseCard } from "@/components/sidebar/CaseCard";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import type { CaseType, CasePriority } from "@/types";

const CASE_TYPES: { value: CaseType; label: string }[] = [
  { value: "عقاري", label: "عقاري" },
  { value: "تجاري", label: "تجاري" },
  { value: "عمالي", label: "عمالي" },
  { value: "جنائي", label: "جنائي" },
  { value: "أحوال_شخصية", label: "أحوال شخصية" },
  { value: "إداري", label: "إداري" },
  { value: "تنفيذ", label: "تنفيذ" },
  { value: "عام", label: "عام" },
];

const PRIORITIES: { value: CasePriority; label: string }[] = [
  { value: "high", label: "عالية" },
  { value: "medium", label: "متوسطة" },
  { value: "low", label: "منخفضة" },
];

function CaseListSkeleton() {
  return (
    <div className="space-y-2 p-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="rounded-md border border-border/50 p-2.5 animate-pulse space-y-2">
          <div className="flex items-center gap-2">
            <div className="h-3.5 w-3.5 rounded bg-muted" />
            <div className="h-3.5 w-3/4 rounded bg-muted" />
          </div>
          <div className="flex gap-1.5">
            <div className="h-4 w-12 rounded-full bg-muted" />
            <div className="h-4 w-10 rounded-full bg-muted" />
          </div>
          <div className="flex gap-3">
            <div className="h-3 w-8 rounded bg-muted" />
            <div className="h-3 w-8 rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function CaseList() {
  const { data, isLoading, isError } = useCases("active");
  const createCase = useCreateCase();

  const [showDialog, setShowDialog] = useState(false);
  const [caseName, setCaseName] = useState("");
  const [caseType, setCaseType] = useState<CaseType>("عام");
  const [priority, setPriority] = useState<CasePriority>("medium");
  const [description, setDescription] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const resetForm = () => {
    setCaseName("");
    setCaseType("عام");
    setPriority("medium");
    setDescription("");
    setFormError(null);
  };

  const handleCreate = () => {
    if (!caseName.trim()) {
      setFormError("اسم القضية مطلوب");
      return;
    }

    createCase.mutate(
      {
        case_name: caseName.trim(),
        case_type: caseType,
        priority,
        description: description.trim() || undefined,
      },
      {
        onSuccess: () => {
          setShowDialog(false);
          resetForm();
        },
        onError: () => {
          setFormError("حدث خطأ أثناء إنشاء القضية");
        },
      }
    );
  };

  if (isLoading) {
    return <CaseListSkeleton />;
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
        <p className="text-sm text-destructive">
          حدث خطأ في تحميل القضايا
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="flex flex-col flex-1 min-h-0">
        {/* New case button */}
        <div className="p-2">
          <Button
            variant="outline"
            size="sm"
            className="w-full justify-center gap-2 text-xs"
            onClick={() => setShowDialog(true)}
          >
            <Plus className="h-3.5 w-3.5" />
            قضية جديدة
          </Button>
        </div>

        {/* Case list */}
        {!data?.cases || data.cases.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center gap-3">
            <FolderOpen className="h-10 w-10 text-muted-foreground/50" />
            <div>
              <p className="text-sm font-medium text-muted-foreground">
                لا توجد قضايا مسجلة
              </p>
              <p className="text-xs text-muted-foreground/70 mt-1">
                أنشئ قضية جديدة لبدء العمل
              </p>
            </div>
          </div>
        ) : (
          <ScrollArea className="flex-1">
            <div className="p-2 space-y-2">
              {data.cases.map((caseSummary) => (
                <CaseCard key={caseSummary.case_id} caseSummary={caseSummary} />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      {/* Create case dialog */}
      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>إنشاء قضية جديدة</DialogTitle>
            <DialogDescription>
              أدخل تفاصيل القضية لإنشائها
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {formError && (
              <div className="rounded-md bg-destructive/10 border border-destructive/20 p-2.5 text-sm text-destructive">
                {formError}
              </div>
            )}

            {/* Case name */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                اسم القضية
              </label>
              <input
                type="text"
                value={caseName}
                onChange={(e) => setCaseName(e.target.value)}
                placeholder="مثال: قضية تحصيل ديون شركة الأفق"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                dir="rtl"
              />
            </div>

            {/* Case type */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                نوع القضية
              </label>
              <select
                value={caseType}
                onChange={(e) => setCaseType(e.target.value as CaseType)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                dir="rtl"
              >
                {CASE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Priority */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                الأولوية
              </label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value as CasePriority)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                dir="rtl"
              >
                {PRIORITIES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                وصف القضية
                <span className="text-muted-foreground font-normal me-1">
                  {" "}(اختياري)
                </span>
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="وصف مختصر للقضية..."
                rows={3}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-none"
                dir="rtl"
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setShowDialog(false);
                resetForm();
              }}
            >
              إلغاء
            </Button>
            <Button
              onClick={handleCreate}
              disabled={createCase.isPending}
            >
              {createCase.isPending && (
                <Loader2 className="h-4 w-4 animate-spin me-2" />
              )}
              إنشاء القضية
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
