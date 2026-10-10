export function StaleBadge({ days }: { days: number }) {
  if (days <= 0) return null;
  const label = days === 1 ? "1 day stale" : `${days} days stale`;
  return (
    <span
      className="tag"
      title={`Dataset last observed ${label} ago — signals may be outdated.`}
    >
      {label}
    </span>
  );
}

export function FreshnessNote({
  observedAt,
  staleDays,
}: {
  observedAt: string;
  staleDays: number | null;
}) {
  if (staleDays == null || staleDays <= 0) {
    return <p className="muted">Dataset is up to date as of {observedAt}.</p>;
  }
  const label = staleDays === 1 ? "1 day" : `${staleDays} days`;
  return (
    <p className="muted">
      Dataset last updated {label} ago ({observedAt}) — signals may be outdated.
    </p>
  );
}