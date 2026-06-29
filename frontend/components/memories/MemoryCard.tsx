'use client';

import { Pencil, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn, getRelativeTimeAr } from '@/lib/utils';
import type { Memory } from '@/types';

const MEMORY_TYPE_CONFIG: Record<
  Memory['memory_type'],
  { label: string; classes: string }
> = {
  fact: {
    label: 'حقيقة',
    classes: 'bg-info text-info-fg',
  },
  document_reference: {
    label: 'مرجع',
    classes: 'bg-muted text-text-muted',
  },
  strategy: {
    label: 'استراتيجية',
    classes: 'bg-brand-soft text-brand-soft-fg',
  },
  deadline: {
    label: 'موعد',
    classes: 'bg-error text-error-fg',
  },
  party_info: {
    label: 'طرف',
    classes: 'bg-success text-success-fg',
  },
};

interface MemoryCardProps {
  memory: Memory;
  onEdit?: (memory: Memory) => void;
  onDelete?: (id: string) => void;
}

export function MemoryCard({ memory, onEdit, onDelete }: MemoryCardProps) {
  const typeConfig = MEMORY_TYPE_CONFIG[memory.memory_type];

  function handleDelete() {
    const confirmed = window.confirm('هل تريد حذف هذه الذاكرة؟');
    if (confirmed) {
      onDelete?.(memory.memory_id);
    }
  }

  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      {/* Header: badge + actions */}
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            'inline-block rounded-full px-2.5 py-0.5 text-xs font-medium',
            typeConfig.classes
          )}
        >
          {typeConfig.label}
        </span>

        <div className="flex items-center gap-1">
          {onEdit && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-foreground"
              onClick={() => onEdit(memory)}
              aria-label="تعديل"
            >
              <Pencil className="h-4 w-4" />
            </Button>
          )}
          {onDelete && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-destructive"
              onClick={handleDelete}
              aria-label="حذف"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Content */}
      <p className="mt-2 text-sm leading-relaxed text-foreground">
        {memory.content_ar}
      </p>

      {/* Footer: confidence + date */}
      <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
        <span>{getRelativeTimeAr(memory.created_at)}</span>
        {memory.confidence_score != null && (
          <span>
            الثقة: {Math.round(memory.confidence_score * 100)}%
          </span>
        )}
      </div>
    </div>
  );
}
