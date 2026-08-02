import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  myLibraryApi,
  type MyLibraryArticle,
  type MyLibraryContentType,
  type MyLibraryItemRef,
  type MyLibraryListParams,
  type MyLibraryResponse,
  type MyLibraryRow,
  type MyLibrarySort,
} from "@/lib/api";

/**
 * «مكتبتي» server state (access_tiers_gating.md PART 5B).
 *
 * The shelf is per-user and authed — `GET /library/mine` answers
 * `private, no-store`, so it is only ever fetched from the client. Nothing here
 * may run in a server render (§5B.3 ISR trap: a counter write inside a cached
 * render is the blog's exact mistake).
 */

/** Cards per page — matches the backend's `DEFAULT_PAGE_SIZE`. */
export const MY_LIBRARY_PAGE_SIZE = 12;

export const myLibraryKeys = {
  all: ["my-library"] as const,
  lists: () => [...myLibraryKeys.all, "list"] as const,
  list: (params: MyLibraryListParams) =>
    [
      ...myLibraryKeys.lists(),
      params.content_type ?? null,
      params.sort ?? "recent",
      // `""` for "no search" keeps every pre-search cache entry addressable by
      // the same shape, so the optimistic حفظ/إزالة patches below — which walk
      // EVERY cached page under `lists()` — reach search pages too.
      params.q ?? "",
      params.page ?? 1,
      params.page_size ?? MY_LIBRARY_PAGE_SIZE,
    ] as const,
};

/**
 * One page of the shelf. `keepPreviousData` keeps the grid on screen while a
 * tab / sort / page change is in flight instead of collapsing to a skeleton —
 * the counts and the frozen CTA would otherwise flicker on every click.
 */
export function useMyLibrary(params: {
  contentType: MyLibraryContentType | null;
  sort: MyLibrarySort;
  page: number;
  enabled?: boolean;
  /**
   * BM25 over the shelf (bm25_navigation_search.md Wave D). Already debounced,
   * trimmed and past the 3-character floor — `useSearchQuery` owns all three,
   * and a shorter term is a 400 in Arabic rather than a narrower list.
   *
   * ⚠ It REPLACES `sort` server-side. The two are not composable: a shelf
   * ordered by "recently used" is not a result list, so the backend drops the
   * sort key for the duration and `MyLibraryPage` hides the «الترتيب» menu to
   * match. `counts` stay whole-shelf, so the tabs do not empty out mid-search.
   */
  q?: string;
  /**
   * Rows per page. Defaults to the full-page size; the sidebar shelf passes a
   * small cap because a lawyer's shelf grows without bound and a peek panel
   * that fetches 12 rows to show 8 is wasted payload.
   */
  pageSize?: number;
}) {
  const query: MyLibraryListParams = {
    content_type: params.contentType,
    sort: params.sort,
    q: (params.q ?? "").trim() || null,
    page: params.page,
    page_size: params.pageSize ?? MY_LIBRARY_PAGE_SIZE,
  };
  return useQuery({
    queryKey: myLibraryKeys.list(query),
    queryFn: () => myLibraryApi.list(query),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    enabled: params.enabled ?? true,
  });
}

/** Does a cached row (or nested مادة) refer to the same shelf item? */
function isSameItem(
  row: { content_type: string; content_id: string; slug: string | null },
  ref: MyLibraryItemRef,
): boolean {
  if (row.content_type !== ref.content_type) return false;
  if (ref.content_id) return row.content_id === ref.content_id;
  if (ref.slug) return row.slug === ref.slug;
  return false;
}

type ShelfPatch = Pick<MyLibraryRow, "source" | "saved_at">;

/** One cached «مكتبتي» page, snapshotted for rollback. */
type ShelfSnapshot = [readonly unknown[], MyLibraryResponse | undefined];

/**
 * Optimistically patch the matching row — including a nested مادة — across every
 * cached «مكتبتي» page, returning the snapshots for rollback. Mirrors
 * `useStarConversation` (hooks/use-conversations.ts), the established
 * optimistic-toggle pattern in this app.
 */
function patchShelfCaches(
  qc: ReturnType<typeof useQueryClient>,
  ref: MyLibraryItemRef,
  patch: ShelfPatch,
): ShelfSnapshot[] {
  const previous = qc.getQueriesData<MyLibraryResponse>({
    queryKey: myLibraryKeys.lists(),
  });

  qc.setQueriesData<MyLibraryResponse>(
    { queryKey: myLibraryKeys.lists() },
    (old) => {
      if (!old) return old;
      return {
        ...old,
        items: old.items.map((row): MyLibraryRow => {
          const children: MyLibraryArticle[] = (row.child_articles ?? []).map(
            (child) => (isSameItem(child, ref) ? { ...child, ...patch } : child),
          );
          const patched = isSameItem(row, ref) ? { ...row, ...patch } : row;
          return { ...patched, child_articles: children };
        }),
      };
    },
  );

  return previous;
}

function rollbackShelfCaches(
  qc: ReturnType<typeof useQueryClient>,
  snapshots: ShelfSnapshot[] | undefined,
): void {
  if (!snapshots) return;
  for (const [key, data] of snapshots) qc.setQueryData(key, data);
}

/**
 * Pin an item to مكتبتي («حفظ»). FREE AT EVERY TIER AND GRANTS NO ACCESS
 * (§5B.2) — it stores a pointer, never content, so saving a gated item the
 * caller has not unlocked is allowed and simply lists locked.
 */
export function useSaveLibraryItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ref: MyLibraryItemRef) => myLibraryApi.save(ref),
    onMutate: async (ref) => {
      await qc.cancelQueries({ queryKey: myLibraryKeys.all });
      const previous = patchShelfCaches(qc, ref, {
        source: "manual",
        saved_at: new Date().toISOString(),
      });
      return { previous };
    },
    onError: (_err, _ref, context) => {
      rollbackShelfCaches(qc, context?.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: myLibraryKeys.all });
    },
  });
}

/**
 * Unpin («إزالة الحفظ»). Not "erase my history": server-side a row the caller
 * actually used keeps its counters and reverts to `source='auto'`, while a row
 * that existed only because of the pin is removed. The optimistic flip mirrors
 * the first case; the settle invalidate reconciles the second.
 */
export function useUnsaveLibraryItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ref: MyLibraryItemRef) => myLibraryApi.unsave(ref),
    onMutate: async (ref) => {
      await qc.cancelQueries({ queryKey: myLibraryKeys.all });
      const previous = patchShelfCaches(qc, ref, {
        source: "auto",
        saved_at: null,
      });
      return { previous };
    },
    onError: (_err, _ref, context) => {
      rollbackShelfCaches(qc, context?.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: myLibraryKeys.all });
    },
  });
}

/**
 * The shelf USE beacon — records one use of a library item.
 *
 * D16.2 (REVISED 2026-07-27): fire this for GATED and OPEN items alike — the
 * page view IS the use, and §5B.2 shelves an item when it is opened, "gated or
 * not". `/library/full` correspondingly does NOT write to the shelf; only the
 * workspace reference-source endpoint still records its own, because no document
 * page is involved there.
 *
 * The earlier rule ("never fire for a gated item") under-counted gated items —
 * a summary read without a reveal shelved nothing — and once the beacon shipped
 * it would have double-counted them, biasing «الأكثر استخداماً» toward gated
 * content.
 *
 * Fire-and-forget: a shelf write must never break the read the user came for,
 * and the endpoint always answers 204. No cache invalidation — a beacon fired
 * on a public page has no shelf on screen to update.
 */
export function useRecordLibraryUse() {
  return useMutation({
    mutationFn: (ref: MyLibraryItemRef) => myLibraryApi.recordUse(ref),
    onError: () => {
      // Swallowed by design — see above.
    },
  });
}
