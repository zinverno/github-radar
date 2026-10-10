import { Link, useParams } from "react-router-dom";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { ConfidenceBar, TrendBadge } from "../components/confidence";
import { LevelTag, Property } from "../components/misc";
import { topicDetail } from "../lib/endpoints";
import { pct, plain, score } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function TopicDetailPage() {
  const { name = "" } = useParams();
  const { data, loading, error, reload } = useAsync(["topic", name], () =>
    topicDetail(name, "7d"),
  );

  if (loading) return <Loading label="Loading topic…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <EmptyState>No data.</EmptyState>;

  const aggregate = data.aggregate;
  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Topic: {data.name}</h1>
          <p>
            {data.repository_count} participating repositories ·{" "}
            {aggregate ? `avg momentum ${score(aggregate.avg_momentum)}` : "no momentum yet"}
          </p>
        </div>
      </header>

      <section className="kpi-grid" aria-label="Topic aggregate">
        <div className="kpi">
          <div className="kpi-value">{plain(aggregate?.repository_count ?? data.repository_count)}</div>
          <div className="kpi-label">Repositories</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">{aggregate ? score(aggregate.avg_momentum) : "—"}</div>
          <div className="kpi-label">Avg momentum</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">
            {aggregate ? `${Math.round((aggregate.share_with_momentum ?? 0) * 100)}%` : "—"}
          </div>
          <div className="kpi-label">Share with momentum</div>
        </div>
        {aggregate && (
          <div className="kpi">
            <div className="kpi-value">{aggregate.confidence.level}</div>
            <div className="kpi-label">Confidence</div>
          </div>
        )}
      </section>

      <section className="card">
        <header className="card-header">
          <h2>Repositories in this topic</h2>
        </header>
        {data.repositories.length === 0 ? (
          <div className="card-body">
            <EmptyState>No repositories under this topic.</EmptyState>
          </div>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>Repository</th>
                <th>Trend</th>
                <th>Growth</th>
                <th>Momentum</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {data.repositories.map((repo) => (
                <tr key={repo.full_name}>
                  <td>
                    <Link to={`/repositories/${repo.full_name}`}>{repo.full_name}</Link>
                  </td>
                  <td>
                    {repo.trend ? (
                      <TrendBadge
                        trend={repo.trend.trend}
                        explanation={repo.trend.explanation}
                      />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{repo.stars_growth_pct == null ? "—" : pct(repo.stars_growth_pct)}</td>
                  <td>{score(repo.momentum?.score)}</td>
                  <td>
                    <ConfidenceBar value={repo.confidence.score} level={repo.confidence.level} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {data.graph && data.graph.related_topics.length > 0 && (
        <section className="card">
          <header className="card-header">
            <h2>Related topics</h2>
          </header>
          <table className="data">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Shared repos</th>
                <th>Shared devs</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {data.graph.related_topics.map((related) => (
                <tr key={related.topic}>
                  <td>
                    <Link to={`/topics/${encodeURIComponent(related.topic)}`}>{related.topic}</Link>
                  </td>
                  <td>{plain(related.shared_repository_count)}</td>
                  <td>{plain(related.shared_developer_count)}</td>
                  <td>
                    <LevelTag level={related.confidence.level} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data.graph && (
        <section className="card">
          <header className="card-header">
            <h2>Graph footprint</h2>
          </header>
          <div className="card-body">
            <Property label="Repositories">{plain(data.graph.reach.repositories)}</Property>
            <Property label="Contributing developers">{plain(data.graph.reach.contributing_developers)}</Property>
            <Property label="Owning developers">{plain(data.graph.reach.owning_developers)}</Property>
            <Property label="Associated developers">{plain(data.graph.reach.associated_developers)}</Property>
          </div>
        </section>
      )}
    </div>
  );
}