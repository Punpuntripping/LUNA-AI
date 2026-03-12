'use client';

import { useState } from 'react';
import { Plus, Brain } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import {
  useMemories,
  useCreateMemory,
  useUpdateMemory,
  useDeleteMemory,
} from '@/hooks/use-memories';
import { MemoryCard } from '@/components/memories/MemoryCard';
import type { Memory } from '@/types';

type MemoryType = Memory['memory_type'];

const FILTER_TABS: { value: string; label: string; type?: MemoryType }[] = [
  { value: 'all', label: 'الكل' },
  { value: 'fact', label: 'حقائق', type: 'fact' },
  { value: 'party_info', label: 'أطراف', type: 'party_info' },
  { value: 'deadline', label: 'مواعيد', type: 'deadline' },
  { value: 'strategy', label: 'استراتيجية', type: 'strategy' },
  { value: 'document_reference', label: 'مراجع', type: 'document_reference' },
];

const MEMORY_TYPE_OPTIONS: { value: MemoryType; label: string }[] = [
  { value: 'fact', label: 'حقيقة' },
  { value: 'party_info', label: 'طرف' },
  { value: 'deadline', label: 'موعد' },
  { value: 'strategy', label: 'استراتيجية' },
  { value: 'document_reference', label: 'مرجع' },
];

interface MemoryListProps {
  caseId: string;
}

export function MemoryList({ caseId }: MemoryListProps) {
  const [activeTab, setActiveTab] = useState('all');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingMemory, setEditingMemory] = useState<Memory | null>(null);

  // Form state
  const [formType, setFormType] = useState<MemoryType>('fact');
  const [formContent, setFormContent] = useState('');

  const selectedType = FILTER_TABS.find((t) => t.value === activeTab)?.type;
  const { data, isLoading } = useMemories(caseId, selectedType);
  const createMemory = useCreateMemory();
  const updateMemory = useUpdateMemory();
  const deleteMemory = useDeleteMemory();

  const memories = data?.memories ?? [];

  function openCreateDialog() {
    setEditingMemory(null);
    setFormType('fact');
    setFormContent('');
    setDialogOpen(true);
  }

  function openEditDialog(memory: Memory) {
    setEditingMemory(memory);
    setFormType(memory.memory_type);
    setFormContent(memory.content_ar);
    setDialogOpen(true);
  }

  function handleSubmit() {
    if (!formContent.trim()) return;

    if (editingMemory) {
      updateMemory.mutate(
        {
          memoryId: editingMemory.memory_id,
          body: { memory_type: formType, content_ar: formContent.trim() },
        },
        { onSuccess: () => setDialogOpen(false) }
      );
    } else {
      createMemory.mutate(
        {
          caseId,
          body: { memory_type: formType, content_ar: formContent.trim() },
        },
        { onSuccess: () => setDialogOpen(false) }
      );
    }
  }

  function handleDelete(memoryId: string) {
    deleteMemory.mutate(memoryId);
  }

  const isMutating = createMemory.isPending || updateMemory.isPending;

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">الذاكرات</h3>
        <Button size="sm" onClick={openCreateDialog}>
          <Plus className="h-4 w-4 me-1" />
          إضافة ذاكرة
        </Button>
      </div>

      {/* Filter Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="w-full flex-wrap h-auto gap-1 p-1">
          {FILTER_TABS.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value} className="text-xs">
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Content */}
      {isLoading ? (
        <div className="flex flex-col gap-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-28 animate-pulse rounded-lg border bg-muted"
            />
          ))}
        </div>
      ) : memories.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-12 text-muted-foreground">
          <Brain className="h-10 w-10" />
          <p className="text-sm">لا توجد ذاكرات</p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {memories.map((memory) => (
            <MemoryCard
              key={memory.memory_id}
              memory={memory}
              onEdit={openEditDialog}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      {/* Create / Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingMemory ? 'تعديل الذاكرة' : 'إضافة ذاكرة جديدة'}
            </DialogTitle>
            <DialogDescription>
              {editingMemory
                ? 'عدّل محتوى الذاكرة أو نوعها'
                : 'أضف ذاكرة جديدة لهذه القضية'}
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4 py-2">
            {/* Type select */}
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="memory-type"
                className="text-sm font-medium text-foreground"
              >
                نوع الذاكرة
              </label>
              <select
                id="memory-type"
                value={formType}
                onChange={(e) => setFormType(e.target.value as MemoryType)}
                className={cn(
                  'h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm',
                  'ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
                )}
              >
                {MEMORY_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Content textarea */}
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="memory-content"
                className="text-sm font-medium text-foreground"
              >
                المحتوى
              </label>
              <textarea
                id="memory-content"
                value={formContent}
                onChange={(e) => setFormContent(e.target.value)}
                rows={4}
                placeholder="اكتب محتوى الذاكرة هنا..."
                className={cn(
                  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm',
                  'ring-offset-background placeholder:text-muted-foreground',
                  'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
                  'resize-none'
                )}
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={isMutating}
            >
              إلغاء
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={!formContent.trim() || isMutating}
            >
              {isMutating
                ? 'جارٍ الحفظ...'
                : editingMemory
                  ? 'حفظ التعديلات'
                  : 'إضافة'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
