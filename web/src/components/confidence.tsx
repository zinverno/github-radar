import { trendDisplay } from "../lib/trends";
import type { ConfidenceLevel } from "../lib/types";

export function ConfidencePill({ level }: { level: ConfidenceLevel }) {
  return (
    <span className={`pill pill-confidence pill-${level.toLowerCase()}`} title={`Confidence: ${level}`}>
      {level}
    </span>
  );
}

export function ConfidenceBar({ value, level }: { value: number; level: ConfidenceLevel }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return (
    <span
      className={`confidence-bar confidence-${level.toLowerCase()}`}
      role="img"
      aria-label={`${level} confidence, ${Math.round(pct)}%`}
    >
      <span className="confidence-bar-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

export function TrendBadge({
  trend,
  explanation,
}: {
  trend: string;
  explanation?: string | null;
}) {
  return (
    <span className={`pill pill-trend pill-trend-${trend}`} title={explanation ?? trendDisplay(trend)}>
      {trendDisplay(trend)}
    </span>
  );
}