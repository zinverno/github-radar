import { Link } from "react-router-dom";
import { ConfidenceBar, TrendBadge } from "../components/confidence";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { FreshnessNote } from "../components/StaleBadge";
import { useAsync } from "../lib/useAsync";
import { explore } from "../lib/endpoints";
import { compact, pct, score, signed } from "../lib/format";
import type { RepoRanksRow, TopicAggregate } from "../lib/types";

function RepoRow({ row }: { row: RepoRanksRow }) {
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
      </div>
      <div className="repo-meta">
        ★ {compact(row.stars)} · growth {pct(row.stars_growth_pct)} · momentum{" "}
        {score(row.score)}
      </div>
    </li>
  );
}

function TopicRow({ topic }: { topic: TopicAggregate }) {
  return (
    <li className="repo-list-item">
      <div className="repo-title">
        <Link to={`/topics/${topic.name}`} className="full-name">
          {topic.name}
        </Link>
      </div>
      <div className="repo-meta">
        {topic.repository_count} repos · avg momentum {score(topic.avg_momentum)}
      </div>
    </li>
  );
}

/** How many topics the landing page previews before the "All →" link. */
export const TOP_TOPICS_PREVIEW_LIMIT = 6;

export function ExplorePage() {
  const { data, loading, error, reload } = useAsync("explore", () => explore("7d", 10));

  if (loading) return <Loading label="Loading dashboard…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <EmptyState>No data yet.</EmptyState>;

  const topicsPreview = data.topics.slice(0, TOP_TOPICS_PREVIEW_LIMIT);

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Explore</h1>
          <p>Top signals across repositories, topics, developers and the ecosystem graph.</p>
        </div>
      </header>

      <section className="kpi-grid" aria-label="Dataset summary">
        <div className="kpi">
          <div className="kpi-value">{data.tracked_repositories}</div>
          <div className="kpi-label">Tracked repositories</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">{data.repositories_with_momentum}</div>
          <div className="kpi-label">With momentum</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">{data.ecosystem.developers}</div>
          <div className="kpi-label">Developers</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">{data.ecosystem.topics}</div>
          <div className="kpi-label">Topics</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">{data.developers.emerging_count}</div>
          <div className="kpi-label">Emerging developers</div>
        </div>
      </section>

      <FreshnessNote
        observedAt={data.freshness.newest_observation_at ?? data.freshness.observed_at}
        staleDays={data.freshness.stale_days}
      />

      <div className="explore-grid">
        <section className="card span-rows">
          <header className="card-header">
            <h2>Highest momentum repositories</h2>
            <Link className="button" to="/repositories">
              All →
            </Link>
          </header>
          {data.repositories.length === 0 ? (
            <div className="card-body">
              {data.tracked_repositories === 0 ? (
                <EmptyState>
                  No tracked repositories yet — run <code>github-radar discover</code>.
                </EmptyState>
              ) : (
                <EmptyState>
                  {data.tracked_repositories} tracked, but none have computed momentum
                  yet — momentum needs at least two observations.
                </EmptyState>
              )}
            </div>
          ) : (
            <ul className="plain-list card-body">
              {data.repositories.map((row) => (
                <RepoRow key={row.full_name} row={row} />
              ))}
            </ul>
          )}
        </section>

        <section className="card">
          <header className="card-header">
            <h2>Top topics</h2>
            <Link className="button" to="/topics">
              All →
            </Link>
          </header>
          {data.topics.length === 0 ? (
            <div className="card-body">
              <EmptyState>No topics observed yet.</EmptyState>
            </div>
          ) : (
            <ul className="plain-list card-body">
              {topicsPreview.map((topic) => (
                <TopicRow key={topic.name} topic={topic} />
              ))}
            </ul>
          )}
        </section>

        <section className="card">
          <header className="card-header">
            <h2>Emerging developers</h2>
            <Link className="button" to="/developers">
              All →
            </Link>
          </header>
          {data.developers.emerging.length === 0 ? (
            <div className="card-body">
              <EmptyState>No emerging developers yet.</EmptyState>
            </div>
          ) : (
            <ul className="plain-list card-body">
              {data.developers.emerging.map((dev) => (
                <li className="repo-list-item" key={dev.login}>
                  <div className="repo-title">
                    <Link to={`/developers/${dev.login}`} className="full-name">
                      {dev.login}
                    </Link>
                    <ConfidenceBar value={dev.emerging.confidence_score} level={dev.emerging.confidence_level} />
                  </div>
                  <div className="repo-meta">
                    followers {compact(dev.followers)} · {dev.emerging.label}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card span-columns">
          <header className="card-header">
            <h2>Ecosystem edges</h2>
            <Link className="button" to="/ecosystem">
              Graph →
            </Link>
          </header>
          <ul className="plain-list card-body stats-list">
            <li>
              <span>Ownership edges</span>
              <strong>{signed(data.ecosystem.edges.owns)}</strong>
            </li>
            <li>
              <span>Contribution edges</span>
              <strong>{signed(data.ecosystem.edges.contributes_to)}</strong>
            </li>
            <li>
              <span>Topic tag edges</span>
              <strong>{signed(data.ecosystem.edges.tagged_with)}</strong>
            </li>
          </ul>
        </section>
      </div>
    </div>
  );
}