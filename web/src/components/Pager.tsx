export function Pager({
  offset,
  limit,
  total,
  onPage,
}: {
  offset: number;
  limit: number;
  total: number;
  onPage: (offset: number) => void;
}) {
  const hasPrevious = offset > 0;
  const hasNext = offset + limit < total;
  return (
    <nav className="pager" aria-label="Pagination">
      <span className="muted">
        {total === 0 ? "0 results" : `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`}
      </span>
      <button
        type="button"
        className="button"
        disabled={!hasPrevious}
        onClick={() => onPage(Math.max(0, offset - limit))}
      >
        ← Previous
      </button>
      <button
        type="button"
        className="button"
        disabled={!hasNext}
        onClick={() => onPage(offset + limit)}
      >
        Next →
      </button>
    </nav>
  );
}