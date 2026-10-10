import { useParams } from "react-router-dom";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { ConfidenceBar } from "../components/confidence";
import { LevelTag, Property } from "../components/misc";
import { developerDetail } from "../lib/endpoints";
import { compact, dateOnly, score } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function DeveloperDetailPage() {
  const { login = "" } = useParams();
  const { data, loading, error, reload } = useAsync(["developer", login], () =>
    developerDetail(login, "7d"),
  );

  if (loading) return <Loading label="Loading developer…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <EmptyState>No data.</EmptyState>;

  const graph = data.graph;
  const coContributors = graph?.co_contributors ?? [];

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>{data.login}</h1>
          <p>
            {data.name ?? "no display name"} · followers {compact(data.followers)} · first seen{" "}
            {dateOnly(data.first_seen_at)}
          </p>
        </div>
        <div className="card-action">
          <span className="pill pill-confidence">{data.emerging.label}</span>
          <ConfidenceBar value={data.emerging.confidence_score} level={data.emerging.confidence_level} />
        </div>
      </header>

      <section className="grid">
        <section className="card">
          <header className="card-header">
            <h2>Signals</h2>
          </header>
          <div className="card-body">
            <Property label="Relevance">
              {data.relevance == null ? null : score(data.relevance)}
            </Property>
            <Property label="Activity">{data.activity?.label ?? "—"}</Property>
            <Property label="Ecosystem">{data.ecosystem?.label ?? "—"}</Property>
            <Property label="Emerging explanation">{data.emerging.explanation}</Property>
            <Property label="Associated repositories">
              {data.associated_repositories.length}
            </Property>
            <Property label="Languages">
              {data.languages.length > 0 ? data.languages.join(", ") : "—"}
            </Property>
          </div>
        </section>

        <section className="card">
          <header className="card-header">
            <h2>Public contacts</h2>
          </header>
          <div className="card-body">
            <Property label="Github">
              {data.public_contacts.github ? data.public_contacts.github : "—"}
            </Property>
            <Property label="Email">{data.public_contacts.public_email ?? "—"}</Property>
            <Property label="Website">{data.public_contacts.website ?? "—"}</Property>
            <Property label="Twitter">{data.public_contacts.twitter ?? "—"}</Property>
          </div>
        </section>
      </section>

      {data.topic_relevances.length > 0 && (
        <section className="card">
          <header className="card-header">
            <h2>Topic relevance</h2>
          </header>
          <table className="data">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Score</th>
                <th>Confidence</th>
                <th>Repos</th>
              </tr>
            </thead>
            <tbody>
              {data.topic_relevances.map((relevance) => (
                <tr key={relevance.topic}>
                  <td>{relevance.topic}</td>
                  <td>{relevance.score == null ? "—" : score(relevance.score)}</td>
                  <td>
                    <ConfidenceBar value={relevance.confidence_score} level={relevance.confidence_level} />
                  </td>
                  <td>{relevance.repo_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {coContributors.length > 0 && (
        <section className="card">
          <header className="card-header">
            <h2>Co-contributors</h2>
          </header>
          <table className="data">
            <thead>
              <tr>
                <th>Developer</th>
                <th>Relationship</th>
                <th>Shared repos</th>
                <th>Strength</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {coContributors.map((relation) => (
                <tr key={relation.login}>
                  <td>{relation.login}</td>
                  <td>{relation.relationship}</td>
                  <td>{relation.shared_repository_count}</td>
                  <td>{score(relation.strength)}</td>
                  <td>
                    <LevelTag level={relation.confidence.level} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {graph?.bridge && (
        <section className="card">
          <header className="card-header">
            <h2>Bridge signal</h2>
            <span className="muted">score {score(graph.bridge.bridge_score)}</span>
          </header>
          <div className="card-body">
            <Property label="Meaningful memberships">
              {graph.bridge.meaningful_topic_memberships}
            </Property>
            <Property label="Distinct supporting repos">
              {graph.bridge.distinct_supporting_repositories}
            </Property>
            {graph.bridge.topic_evidence.length ? (
              <Property label="Topic evidence">
                {graph.bridge.topic_evidence.map((evidence) => evidence.topic).join(", ")}
              </Property>
            ) : null}
          </div>
        </section>
      )}
    </div>
  );
}