import { useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { LevelTag } from "../components/misc";
import { bridges, crossTopicBridges } from "../lib/endpoints";
import { plain, score } from "../lib/format";
import type { DeveloperBridge, GraphConfidence } from "../lib/types";
import { useAsync } from "../lib/useAsync";

interface BridgeRowProps {
  login: string;
  bridgeScore: number;
  confidence: GraphConfidence;
  memberships?: number;
  supportingRepos?: number;
  topicLabels: string[];
  smallSample?: boolean;
}

function BridgeRow({
  login,
  bridgeScore,
  confidence,
  memberships,
  supportingRepos,
  topicLabels,
  smallSample = false,
}: BridgeRowProps) {
  return (
    <li className="repo-list-item">
      <div className="repo-title">
        <Link to={`/developers/${login}`} className="full-name">
          {login}
        </Link>
        <LevelTag level={confidence.level} />
      </div>
      <div className="repo-meta">
        bridge score {score(bridgeScore)}
        {memberships != null ? ` · ${memberships} topic memberships` : ""}
        {supportingRepos != null ? ` · ${supportingRepos} supporting repos` : ""}
      </div>
      {topicLabels.length > 0 && (
        <div className="repo-meta">spans: {topicLabels.join(", ")}</div>
      )}
      {smallSample ? <p className="muted">Small sample.</p> : null}
    </li>
  );
}

function fromDeveloperBridge(bridge: DeveloperBridge): BridgeRowProps {
  return {
    login: bridge.login,
    bridgeScore: bridge.bridge_score,
    confidence: bridge.confidence,
    memberships: bridge.meaningful_topic_memberships,
    supportingRepos: bridge.distinct_supporting_repositories,
    topicLabels: bridge.topic_evidence.map((evidence) => evidence.topic),
    smallSample: bridge.small_sample,
  };
}

export function BridgesPage() {
  const [topicA, setTopicA] = useState("");
  const [topicB, setTopicB] = useState("");

  const { data, loading, error, reload } = useAsync("bridges", () =>
    bridges({ limit: 25 }),
  );
  const { data: cross, loading: crossLoading, error: crossError } = useAsync(
    ["cross-topic", topicA, topicB],
    () =>
      topicA && topicB ? crossTopicBridges({ topicA, topicB }) : Promise.resolve(null),
  );

  if (loading && !data) return <Loading label="Loading bridges…" />;
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Bridges</h1>
          <p>Developers and repositories whose evidence genuinely spans separate ecosystems.</p>
        </div>
      </header>

      {data && data.items.length === 0 ? (
        <EmptyState>No bridging developers found yet.</EmptyState>
      ) : (
        <section className="card">
          <header className="card-header">
            <h2>Developer bridges</h2>
          </header>
          <ul className="plain-list card-body">
            {(data?.items ?? []).map((bridge) => (
              <BridgeRow key={`dev-${bridge.developer_id}`} {...fromDeveloperBridge(bridge)} />
            ))}
          </ul>
        </section>
      )}

      <section className="card">
        <header className="card-header">
          <h2>Cross-topic bridge report</h2>
        </header>
        <div className="card-body">
          <form
            className="toolbar"
            onSubmit={(event) => {
              event.preventDefault();
            }}
          >
            <label>
              Topic A
              <input
                type="text"
                list="topic-a"
                value={topicA}
                onChange={(event) => setTopicA(event.target.value)}
              />
            </label>
            <label>
              Topic B
              <input
                type="text"
                list="topic-b"
                value={topicB}
                onChange={(event) => setTopicB(event.target.value)}
              />
            </label>
          </form>
          {crossLoading ? <Loading label="Building cross-topic report…" /> : null}
          {crossError ? <ErrorState error={crossError} /> : null}
          {cross && (
            <div>
              <p className="muted">
                {cross.topic_a} ↔ {cross.topic_b} · {plain(cross.distinct_bridging_developers)}{" "}
                bridging developers · avg score {score(cross.average_bridge_score)}
              </p>
              {cross.top_bridges.length === 0 ? (
                <p>
                  No bridging developers with real evidence in both ecosystems yet.
                </p>
              ) : (
                <ul className="plain-list">
                  {cross.top_bridges.map((bridge) => (
                    <BridgeRow
                      key={`cross-${bridge.developer_id}`}
                      login={bridge.login}
                      bridgeScore={bridge.bridge_score}
                      confidence={bridge.confidence}
                      topicLabels={[cross.topic_a, cross.topic_b]}
                      smallSample={bridge.small_sample}
                    />
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}