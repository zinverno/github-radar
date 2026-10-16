/**
 * Human-friendly presentation of trend classes.
 *
 * The ``new`` class from analytics does NOT mean a freshly created GitHub
 * repository: it means the tracked entity has fewer than two observations, so
 * there isn't enough history to classify a trajectory. The badge therefore
 * says what the status actually is instead of implying the repository is new.
 */

import type { Window } from "./types";

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

/** Requested duration is a selection target; only stored endpoints were measured. */
export function observationWindowLabel(window: Window | null | undefined, requestedDays: number): string {
  if (window?.span_days == null) return `unavailable (${requestedDays}d requested)`;
  const span = window.span_days.toLocaleString("en-US", { maximumSignificantDigits: 6 });
  return `${span}d observed; ${window.complete ? "complete" : "partial"} coverage (${requestedDays}d requested)`;
}
