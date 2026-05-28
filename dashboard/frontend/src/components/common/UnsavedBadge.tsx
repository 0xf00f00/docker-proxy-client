export default function UnsavedBadge() {
  return (
    <span
      role="status"
      className="text-warning bg-warning/10 inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[11px] font-medium"
    >
      <span className="bg-warning h-1.5 w-1.5 rounded-full" />
      Unsaved
    </span>
  );
}
