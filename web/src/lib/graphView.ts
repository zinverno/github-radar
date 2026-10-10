/**
 * Pure viewport math for the ecosystem graph: wheel zoom, drag pan, reset,
 * and deterministic label placement that reduces overlaps on dense rows.
 * Kept free of React/DOM so the behavior is unit-testable.
 */

export interface GraphView {
  x: number;
  y: number;
  k: number;
}

export const MIN_SCALE = 0.35;
export const MAX_SCALE = 4;

export const IDENTITY_VIEW: GraphView = { x: 0, y: 0, k: 1 };

/** Dense rows stagger labels across two bands instead of one. */
export const DENSE_ROW_THRESHOLD = 10;

export function clampScale(k: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, k));
}

/**
 * Zoom by ``factor`` keeping the viewBox point (``px``, ``py``) fixed under
 * the pointer. Pure: returns a new view without mutating.
 */
export function zoomAt(
  view: GraphView,
  factor: number,
  px: number,
  py: number,
): GraphView {
  const k = clampScale(view.k * factor);
  return {
    k,
    x: px - (px - view.x) * (k / view.k),
    y: py - (py - view.y) * (k / view.k),
  };
}

/** Pan the view by a viewBox-space delta. Pure. */
export function panBy(view: GraphView, dx: number, dy: number): GraphView {
  return { x: view.x + dx, y: view.y + dy, k: view.k };
}

/**
 * Vertical label band for a node in its type row. Sparse rows keep one band;
 * dense rows alternate between an upper and a lower band so adjacent labels
 * do not collide horizontally.
 */
export function labelBand(index: number, total: number): number {
  if (total < DENSE_ROW_THRESHOLD) return -14;
  return index % 2 === 0 ? -16 : -34;
}

/**
 * Default label length in characters: dense rows get shorter default labels
 * (the full text is revealed on hover/focus/selection).
 */
export function defaultLabelLength(total: number): number {
  return total < DENSE_ROW_THRESHOLD ? 16 : 12;
}