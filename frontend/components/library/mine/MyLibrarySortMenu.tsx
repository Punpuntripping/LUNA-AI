"use client";

import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { MyLibrarySort } from "@/lib/api";
import { MY_LIBRARY_COPY } from "@/components/library/mine/copy";

/**
 * Shelf ordering (§5B.3): default «الأحدث» (`recent`), secondary
 * «الأكثر استخداماً» (`most_used`), plus «الأحدث حفظاً» (`saved`).
 *
 * The label vocabulary is USAGE — «استخدام» — matching `use_count` /
 * `last_used_at` / `sort=most_used` exactly, so there is no translation layer
 * between what the user reads and the column that produced it. Nothing here is
 * ever labelled «فتح».
 */
const SORTS = ["recent", "most_used", "saved"] as const satisfies readonly MyLibrarySort[];

export function MyLibrarySortMenu({
  value,
  onChange,
}: {
  value: MyLibrarySort;
  onChange: (sort: MyLibrarySort) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <span className="text-muted-foreground">
            {MY_LIBRARY_COPY.sortLabel}:
          </span>
          <span>{MY_LIBRARY_COPY.sorts[value]}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        {SORTS.map((sort) => (
          <DropdownMenuItem
            key={sort}
            onClick={() => onChange(sort)}
            className={cn(sort === value && "font-medium text-primary")}
          >
            {MY_LIBRARY_COPY.sorts[sort]}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
