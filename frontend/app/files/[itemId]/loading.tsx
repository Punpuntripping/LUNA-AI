// Next.js App Router requires default export for loading files
// eslint-disable-next-line import/no-default-export
export default function FileLoading() {
  return (
    <div className="flex h-full items-center justify-center gap-3">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      <span className="text-sm text-muted-foreground">جارٍ التحميل...</span>
    </div>
  );
}
