/**
 * Human-friendly presentation of trend classes.
 *
 * The ``new`` class from analytics does NOT mean a freshly created GitHub
 * repository: it means the tracked entity has fewer than two observations, so
 * there isn't enough history to classify a trajectory. The badge therefore
 * says what the status actually is instead of implying the repository is new.
 */

export const TREND_CLASS_NEW = "new";

export const TREND_DISPLAY: Record<string, string> = {
  rising: "Rising",
  steady: "Steady",
  declining: "Declining",
  inactive: "Inactive",
  [TREND_CLASS_NEW]: "Insufficient history",
};

/** Present a trend class for the UI, keeping the API value only as a fallback. */
export function trendDisplay(trend: string): string {
  return TREND_DISPLAY[trend] ?? trend;
}