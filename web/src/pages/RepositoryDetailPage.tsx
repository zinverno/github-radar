import { Link, useParams } from "react-router-dom";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { ConfidenceBar, TrendBadge } from "../components/confidence";
import { GithubLink, LevelTag, Property, Tag } from "../components/misc";
import { StaleBadge } from "../components/StaleBadge";
import { repositoryDetail } from "../lib/endpoints";
import { compact, dateTime, pct, plain, score } from "../lib/format";
import { observationWindowLabel } from "../lib/trends";
import { useAsync } from "../lib/useAsync";

export function RepositoryDetailPage() {
  const { owner = "", name = "" } = useParams();
  const fullName = `${owner}/${name}`;
  const { data, loading, error, reload } = useAsync(
    ["repository-detail", fullName],
    () => repositoryDetail(fullName, "7d"),
  );

  if (loading) return <Loading label="Loading repository…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <EmptyState>No data.</EmptyState>;

  const metrics = data.metrics;
  const related = data.graph?.related_repositories ?? [];
  const bridge = data.bridge;

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>
            <GithubLink fullName={data.full_name} />
          </h1>
          <p>
            {data.primary_language ?? "unknown language"} · ★ {compact(metrics.latest_counts.stars)}{" "}
            · ⑂ {compact(metrics.latest_counts.forks)} · last seen {dateTime(data.last_seen_at)}
          </p>
        </div>
        <div className="card-action">
          {data.trend && (
            <TrendBadge
              trend={data.trend.trend}
              explanation={data.trend.explanation}
              window={data.trend.window}
            />
          )}
          {data.stale_days != null && data.stale_days > 0 && (
            <StaleBadge days={data.stale_days} />
          )}
          {data.confidence && (
            <ConfidenceBar value={data.confidence.score} level={data.confidence.level} />
          )}
        </div>
      </header>

      <section className="grid grid-overview">
        <section className="card span-all">
          <header className="card-header">
            <h2>Overview</h2>
          </header>
          <div className="card-body">
            <Property label="Description">{data.description}</Property>
            <Property label="Homepage">
              {data.homepage ? (
                <a className="external-link" href={data.homepage}>
                  {data.homepage} ↗
                </a>
              ) : null}
            </Property>
            <Property label="Topics">
              {data.topics.map((topic) => (
                <Tag key={topic} href={`/topics/${encodeURIComponent(topic)}`}>
                  {topic}
                </Tag>
              ))}
            </Property>
            <Property label="Flags">
              {[
                data.flags.fork && "fork",
                data.flags.archived && "archived",
                data.flags.template && "template",
              ]
                .filter(Boolean)
                .join(", ") || "none"}
            </Property>
            <Property label="First / last seen">
              {dateTime(data.first_seen_at)} → {dateTime(data.last_seen_at)}
            </Property>
            <Property label="Snapshots">{plain(data.snapshot_count)}</Property>
            {data.stale_days != null && data.stale_days > 0 && (
              <p className="muted">
                Dataset last observed {data.stale_days} day
                {data.stale_days === 1 ? "" : "s"} ago — signals above may be outdated.
              </p>
            )}
          </div>
        </section>

        <section className="card">
          <header className="card-header">
            <h2>Momentum &amp; trend</h2>
          </header>
          <div className="card-body">
            <Property label="Momentum">
              {data.momentum ? (
                <>
                  {score(data.momentum.score)} / {score(data.momentum.max_score)}
                </>
              ) : null}
            </Property>
            <Property label="Observed star growth">
              {pct(metrics.stars_7d?.growth_pct == null ? null : metrics.stars_7d.growth_pct / 100)}
            </Property>
            <Property label="Star delta (7d requested)">{data.stars_7d}</Property>
            <Property label="Star coverage (7d requested)">
              {observationWindowLabel(metrics.stars_7d?.window, 7)}
            </Property>
            <Property label="Star delta (30d requested)">{data.stars_30d}</Property>
            <Property label="Star coverage (30d requested)">
              {observationWindowLabel(metrics.stars_30d?.window, 30)}
            </Property>
            <Property label="Trend explanation">{data.trend?.explanation}</Property>
            <Property label="Confidence">
              {data.confidence ? (
                <>
                  <LevelTag level={data.confidence.level} /> {score(data.confidence.score)}
                </>
              ) : null}
            </Property>
          </div>
        </section>

        <section className="card">
          <header className="card-header">
            <h2>Contributors</h2>
          </header>
          {data.contributors.length === 0 ? (
            <div className="card-body">
              <EmptyState>No contributors observed.</EmptyState>
            </div>
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>Login</th>
                  <th>Contributions</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {data.contributors.map((contributor) => (
                  <tr key={contributor.login}>
                    <td>
                      <Link to={`/developers/${contributor.login}`}>{contributor.login}</Link>
                    </td>
                    <td>{plain(contributor.contributions)}</td>
                    <td>{dateTime(contributor.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </section>

      {related.length > 0 && (
        <section className="card">
          <header className="card-header">
            <h2>Related repositories</h2>
          </header>
          <div className="card-body">
            <ul className="plain-list">
              {related.map((relationship) => (
                <li className="repo-list-item" key={relationship.full_name}>
                  <div className="repo-title">
                    <Link to={`/repositories/${relationship.full_name}`} className="full-name">
                      {relationship.full_name}
                    </Link>
                    <span className="muted">{relationship.confidence.level}</span>
                  </div>
                  <div className="repo-meta">
                    shared developers {relationship.shared_developer_count} · shared topics{" "}
                    {relationship.shared_topic_count} · score{" "}
                    {score(relationship.relationship_score)}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}

      {bridge && (
        <section className="card">
          <header className="card-header">
            <h2>Bridge signal</h2>
            <span className="muted">score {score(bridge.bridge_score)}</span>
          </header>
          <div className="card-body">
            <p>{bridge.full_name} connects people across separate topic ecosystems.</p>
            <Property label="Confidence">
              <LevelTag level={bridge.confidence.level} /> {score(bridge.confidence.score)}
            </Property>
            <Property label="Cross-topic contributors">{bridge.cross_topic_contributors}</Property>
            <Property label="Cross-repository contributors">{bridge.cross_repository_contributors}</Property>
            {bridge.small_sample && (
              <p className="muted">Small sample: treat this signal cautiously.</p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
