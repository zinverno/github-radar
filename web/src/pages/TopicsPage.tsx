import { Link } from "react-router-dom";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { Pager } from "../components/Pager";
import { listTopics } from "../lib/endpoints";
import { plain } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const LIMIT = 40;

export function TopicsPage() {
  const { data, loading, error, reload } = useAsync("topics", () =>
    listTopics(LIMIT, 0),
  );

  if (loading) return <Loading label="Loading topics…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <EmptyState>No data yet.</EmptyState>;

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Topics</h1>
          <p>The vocabulary of the tracked dataset, ordered by coverage.</p>
        </div>
      </header>

      {data.items.length === 0 ? (
        <EmptyState>No topics observed yet.</EmptyState>
      ) : (
        <section className="card">
          <table className="data">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Repositories</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((topic) => (
                <tr key={topic.name}>
                  <td>
                    <Link to={`/topics/${encodeURIComponent(topic.name)}`}>{topic.name}</Link>
                  </td>
                  <td>{plain(topic.repository_count)}</td>
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
                // single page for now; further pages via items are not implemented
              }}
            />
          </div>
        </section>
      )}
    </div>
  );
}