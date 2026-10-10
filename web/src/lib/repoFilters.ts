/** URL ↔ repository listing filter/sort state (mirrors ``/repositories`` params). */

export const REPO_SORTS = [
  "last_seen",
  "name",
  "stars",
  "forks",
  "momentum",
  "stars_7d",
  "trend",
] as const;

export type RepoSort = (typeof REPO_SORTS)[number];

export const REPO_TRENDS = ["rising", "steady", "declining", "inactive", "new"] as const;

export interface RepoFilters {
  offset: number;
  sort: RepoSort;
  order: "asc" | "desc";
  language: string;
  trend: string;
  minStars: string;
  maxStars: string;
  hideArchived: boolean;
  hideForks: boolean;
}

export function parseRepoFilters(searchParams: URLSearchParams): RepoFilters {
  const sortRaw = searchParams.get("sort") ?? "last_seen";
  const sort = (REPO_SORTS as readonly string[]).includes(sortRaw)
    ? (sortRaw as RepoSort)
    : "last_seen";
  const trendRaw = searchParams.get("trend") ?? "";
  const trend = (REPO_TRENDS as readonly string[]).includes(trendRaw) ? trendRaw : "";
  return {
    offset: Math.max(0, Number(searchParams.get("offset") ?? 0) || 0),
    sort,
    order: searchParams.get("order") === "asc" ? "asc" : "desc",
    language: searchParams.get("language") ?? "",
    trend,
    minStars: searchParams.get("minStars") ?? "",
    maxStars: searchParams.get("maxStars") ?? "",
    hideArchived: searchParams.get("archived") === "false",
    hideForks: searchParams.get("fork") === "false",
  };
}

export function repoFiltersToSearchParams(filters: RepoFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (filters.offset > 0) sp.set("offset", String(filters.offset));
  if (filters.sort !== "last_seen") sp.set("sort", filters.sort);
  if (filters.order !== "desc") sp.set("order", filters.order);
  if (filters.language) sp.set("language", filters.language);
  if (filters.trend) sp.set("trend", filters.trend);
  if (filters.minStars) sp.set("minStars", filters.minStars);
  if (filters.maxStars) sp.set("maxStars", filters.maxStars);
  if (filters.hideArchived) sp.set("archived", "false");
  if (filters.hideForks) sp.set("fork", "false");
  return sp;
}