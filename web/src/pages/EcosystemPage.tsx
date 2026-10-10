import { Link } from "react-router-dom";
import { EmptyState, Loading } from "../components/basic";
import { EcoGraph } from "../components/EcoGraph";
import { ecosystemReport, graphEdges, graphNodes } from "../lib/endpoints";
import { plain, score } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function EcosystemPage() {
  const { data: report } = useAsync("ecosystem-report", () => ecosystemReport("7d"));

  const { data: nodes } = useAsync("ecosystem-nodes", () => graphNodes(null));
  const { data: edges } = useAsync("ecosystem-edges", () => graphEdges());

  if (!report && !nodes && !edges) {
    return <Loading label="Building ecosystem graph…" />;
  }

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Ecosystem</h1>
          <p>The derived graph: who owns and contributes to what, and how topics connect.</p>
        </div>
      </header>

      {report && (
        <section className="kpi-grid" aria-label="Graph size">
          <div className="kpi">
            <div className="kpi-value">{plain(report.node_counts.developers)}</div>
            <div className="kpi-label">Developers</div>
          </div>
          <div className="kpi">
            <div className="kpi-value">{plain(report.node_counts.repositories)}</div>
            <div className="kpi-label">Repositories</div>
          </div>
          <div className="kpi">
            <div className="kpi-value">{plain(report.node_counts.topics)}</div>
            <div className="kpi-label">Topics</div>
          </div>
          <div className="kpi">
            <div className="kpi-value">{plain(report.component_count)}</div>
            <div className="kpi-label">Components</div>
          </div>
        </section>
      )}

      {nodes && nodes.length > 0 ? (
        <section className="card">
          <div className="card-body">
            <EcoGraph nodes={nodes} edges={edges ?? []} />
          </div>
        </section>
      ) : (
        <EmptyState>
          The graph is empty. Discover repositories and sync contributors to grow it.
        </EmptyState>
      )}

      {report && report.largest_component && (
        <section className="card">
          <header className="card-header">
            <h2>Largest connected component</h2>
          </header>
          <div className="card-body">
            <p className="muted">
              {report.largest_component.node_count} nodes ·{" "}
              {report.largest_component.repository_full_names.join(", ")}
            </p>
          </div>
        </section>
      )}

      {report && report.top_developer_centrality.length > 0 && (
        <section className="card">
          <header className="card-header">
            <h2>Most central developers</h2>
          </header>
          <table className="data">
            <thead>
              <tr>
                <th>Login</th>
                <th>Degree</th>
                <th>Weighted</th>
              </tr>
            </thead>
            <tbody>
              {report.top_developer_centrality.map((centrality) => (
                <tr key={centrality.label}>
                  <td>
                    <Link to={`/developers/${centrality.label}`}>{centrality.label}</Link>
                  </td>
                  <td>{plain(centrality.degree)}</td>
                  <td>{score(centrality.weighted_degree)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}