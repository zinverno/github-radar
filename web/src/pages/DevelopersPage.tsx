import { Link } from "react-router-dom";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { ConfidenceBar } from "../components/confidence";
import { Pager } from "../components/Pager";
import { listDevelopers } from "../lib/endpoints";
import { dateTime, score } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const LIMIT = 40;

export function DevelopersPage() {
  const { data, loading, error, reload } = useAsync(["developers"], () =>
    listDevelopers({ limit: LIMIT, sort: "relevance" }),
  );

  if (loading) return <Loading label="Loading developers…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <EmptyState>No data yet.</EmptyState>;

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Developers</h1>
          <p>Tracked developers with relevance, activity, ecosystem and emerging classification.</p>
        </div>
      </header>

      {data.items.length === 0 ? (
        <EmptyState>No developers observed yet.</EmptyState>
      ) : (
        <section className="card">
          <table className="data">
            <thead>
              <tr>
                <th>Developer</th>
                <th>Relevance</th>
                <th>Activity</th>
                <th>Ecosystem</th>
                <th>Class</th>
                <th>Confidence</th>
                <th>First seen</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((dev) => (
                <tr key={dev.login}>
                  <td>
                    <Link to={`/developers/${dev.login}`}>{dev.login}</Link>
                    {dev.name ? <span className="muted"> · {dev.name}</span> : null}
                  </td>
                  <td>{dev.relevance == null ? "—" : score(dev.relevance)}</td>
                  <td>{dev.activity?.label ?? "—"}</td>
                  <td>{dev.ecosystem?.label ?? "—"}</td>
                  <td>{dev.emerging.label}</td>
                  <td>
                    <ConfidenceBar value={dev.emerging.confidence_score} level={dev.emerging.confidence_level} />
                  </td>
                  <td>{dateTime(dev.first_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="card-body">
            <Pager
              offset={data.offset}
              limit={data.limit}
              total={data.total}
              onPage={() => {
                // single page for now
              }}
            />
          </div>
        </section>
      )}
    </div>
  );
}