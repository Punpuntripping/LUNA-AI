// Next.js App Router requires a default export for loading files.
// eslint-disable-next-line import/no-default-export
export default function PayPlanLoading() {
  return (
    <div className="flex items-center justify-center gap-3 py-20">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      <span className="text-sm text-muted-foreground">
        جارٍ تجهيز صفحة الدفع...
      </span>
    </div>
  );
}
