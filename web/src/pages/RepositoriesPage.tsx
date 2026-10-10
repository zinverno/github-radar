import { Link, useSearchParams } from "react-router-dom";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { ConfidenceBar, TrendBadge } from "../components/confidence";
import { Pager } from "../components/Pager";
import { StaleBadge } from "../components/StaleBadge";
import { listRepositories } from "../lib/endpoints";
import { compact, dateTime, pct } from "../lib/format";
import { trendDisplay } from "../lib/trends";
import {
  REPO_SORTS,
  REPO_TRENDS,
  parseRepoFilters,
  repoFiltersToSearchParams,
  type RepoFilters,
  type RepoSort,
} from "../lib/repoFilters";
import type { RepositorySummary } from "../lib/types";
import { useAsync } from "../lib/useAsync";

const LIMIT = 25;

function Row({ row }: { row: RepositorySummary }) {
  return (
    <li className="repo-list-item">
      <div className="repo-title">
        <Link to={`/repositories/${row.full_name}`} className="full-name">
          {row.full_name}
        </Link>
        {row.trend && (
          <TrendBadge
            trend={row.trend.trend}
            explanation={row.trend.explanation}
          />
        )}
        <ConfidenceBar value={row.confidence.score} level={row.confidence.level} />
        {row.stale_days != null && row.stale_days > 0 && (
          <StaleBadge days={row.stale_days} />
        )}
      </div>
      <div className="repo-meta">
        ★ {compact(row.stars)} · {row.primary_language ?? "unknown"} · last seen{" "}
        {dateTime(row.last_seen_at)}
        {row.trend?.growth_pct != null ? ` · ${pct(row.trend.growth_pct)} 7d` : ""}
      </div>
      <div className="repo-meta">{row.description}</div>
    </li>
  );
}

export function RepositoriesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = parseRepoFilters(searchParams);

  const request = {
    limit: LIMIT,
    offset: filters.offset,
    sort: filters.sort,
    order: filters.order,
    language: filters.language || undefined,
    trend: filters.trend || undefined,
    minStars: filters.minStars ? Math.max(0, Number(filters.minStars) || 0) : undefined,
    maxStars: filters.maxStars ? Math.max(0, Number(filters.maxStars) || 0) : undefined,
    archived: filters.hideArchived ? false : undefined,
    fork: filters.hideForks ? false : undefined,
  };

  const { data, loading, error, reload } = useAsync(
    ["repositories", request],
    () => listRepositories(request),
  );

  const patch = (next: Partial<RepoFilters>) =>
    setSearchParams(repoFiltersToSearchParams({ ...filters, ...next }));

  if (loading) return <Loading label="Loading repositories…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <EmptyState>No data yet.</EmptyState>;

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Repositories</h1>
          <p>Tracked repositories with momentum, trend and staleness.</p>
        </div>
      </header>

      <form className="toolbar" onSubmit={(event) => event.preventDefault()}>
        <label>
          Sort
          <select
            value={filters.sort}
            onChange={(event) => patch({ sort: event.target.value as RepoSort, offset: 0 })}
          >
            {REPO_SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {sort.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          Direction
          <select
            value={filters.order}
            onChange={(event) =>
              patch({ order: event.target.value as "asc" | "desc", offset: 0 })
            }
          >
            <option value="desc">Descending</option>
            <option value="asc">Ascending</option>
          </select>
        </label>
        <label>
          Trend
          <select
            value={filters.trend}
            onChange={(event) => patch({ trend: event.target.value, offset: 0 })}
          >
            <option value="">Any</option>
            {REPO_TRENDS.map((trend) => (
              <option key={trend} value={trend}>
                {trendDisplay(trend)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Language
          <input
            type="search"
            value={filters.language}
            placeholder="e.g. Go"
            onChange={(event) => patch({ language: event.target.value, offset: 0 })}
          />
        </label>
        <label>
          Min stars
          <input
            type="number"
            min={0}
            value={filters.minStars}
            onChange={(event) => patch({ minStars: event.target.value, offset: 0 })}
          />
        </label>
        <label>
          Max stars
          <input
            type="number"
            min={0}
            value={filters.maxStars}
            onChange={(event) => patch({ maxStars: event.target.value, offset: 0 })}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={filters.hideArchived}
            onChange={(event) => patch({ hideArchived: event.target.checked, offset: 0 })}
          />
          Hide archived
        </label>
        <label>
          <input
            type="checkbox"
            checked={filters.hideForks}
            onChange={(event) => patch({ hideForks: event.target.checked, offset: 0 })}
          />
          Hide forks
        </label>
      </form>

      {data.items.length === 0 ? (
        data.total === 0 && data.offset === 0 ? (
          <EmptyState>
            No tracked repositories yet — run <code>github-radar discover</code> first.
          </EmptyState>
        ) : (
          <EmptyState>No repositories match the current filters.</EmptyState>
        )
      ) : (
        <section className="card">
          <ul className="plain-list card-body">
            {data.items.map((row) => (
              <Row key={row.full_name} row={row} />
            ))}
          </ul>
          <div className="card-body">
            <Pager
              offset={data.offset}
              limit={data.limit}
              total={data.total}
              onPage={(offset) => patch({ offset })}
            />
          </div>
        </section>
      )}
    </div>
  );
}