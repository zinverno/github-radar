import { useState } from "react";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { LevelTag } from "../components/misc";
import { aiArtifacts, aiStatus, generateArtifact } from "../lib/endpoints";
import { ApiError } from "../lib/api";
import { dateTime, plain } from "../lib/format";
import type { AiArtifact, WindowDays } from "../lib/types";
import { useAsync } from "../lib/useAsync";

const ENTITY_TYPES = ["repository", "topic", "developer", "ecosystem", "bridge"];

function ArtifactCard({ artifact }: { artifact: AiArtifact }) {
  return (
    <li className="repo-list-item">
      <div className="repo-title">
        <strong className="full-name">{artifact.headline}</strong>
        <LevelTag level={artifact.confidence} />
      </div>
      <p>{artifact.summary}</p>
      <ul className="plain-list">
        {artifact.key_points.map((point, index) => (
          <li key={index}>
            {point.text}{" "}
            <span className="muted">[{point.evidence_ids.join(", ")}]</span>
          </li>
        ))}
      </ul>
      <div className="repo-meta">
        {artifact.artifact_type} · {artifact.model} · {dateTime(artifact.generated_at)} ·{" "}
        {plain(artifact.input_tokens)} in / {plain(artifact.output_tokens)} out
      </div>
    </li>
  );
}

export function AiPage() {
  const [entityType, setEntityType] = useState("repository");
  const [entityKey, setEntityKey] = useState("");
  const [window, setWindow] = useState<WindowDays>("7d");
  const [busy, setBusy] = useState(false);
  const [generated, setGenerated] = useState<AiArtifact | null>(null);
  const [generateError, setGenerateError] = useState<Error | null>(null);

  const { data: status, error: statusError, loading: statusLoading } = useAsync(
    "ai-status",
    () => aiStatus(),
  );
  const { data: list, error, loading, reload } = useAsync("ai-artifacts", () =>
    aiArtifacts({ limit: 25 }),
  );

  const needsKey = entityType !== "ecosystem";

  const runGenerate = async () => {
    setBusy(true);
    setGenerateError(null);
    setGenerated(null);
    try {
      const result = await generateArtifact({
        entityType,
        entityKey: needsKey && entityKey.trim() ? entityKey.trim() : undefined,
        window,
      });
      setGenerated(result.artifact);
      reload();
    } catch (err) {
      setGenerateError(
        err instanceof ApiError ? err : (err as Error),
      );
    } finally {
      setBusy(false);
    }
  };

  const generateHint =
    entityType === "repository"
      ? "owner/name of a tracked repository"
      : entityType === "developer"
        ? "login of a tracked developer"
        : entityType === "topic"
          ? "topic name (e.g. mcp)"
          : entityType === "bridge"
            ? "two topics separated by a space (topic_a and topic_b below)"
            : "";

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>AI narration</h1>
          <p>
            Cached, deterministic-confidence summaries of the measured facts. Reads always hit
            the cache; only "Generate" spends model budget.
          </p>
        </div>
      </header>

      {statusLoading ? (
        <Loading label="Checking AI status…" />
      ) : statusError ? (
        <ErrorState error={statusError} />
      ) : status ? (
        <section className="card">
          <header className="card-header">
            <h2>Provider status</h2>
          </header>
          <div className="card-body">
            <p>
              {status.configured
                ? `Configured (${status.base_url_host ?? "unknown host"}) · model ${status.model ?? "unknown"}`
                : "Not configured — set AI_API_KEY and AI_MODEL (see .env.example)."}
            </p>
            <p className="muted">
              {plain(status.cached_artifacts)} cached artifacts · cap{" "}
              {plain(status.per_run_request_cap)} calls/run · max{" "}
              {plain(status.max_output_tokens)} output tokens
            </p>
          </div>
        </section>
      ) : null}

      <section className="card">
        <header className="card-header">
          <h2>Generate</h2>
        </header>
        <div className="card-body">
          <form
            className="toolbar"
            onSubmit={(event) => {
              event.preventDefault();
              void runGenerate();
            }}
          >
            <label>
              Entity type
              <select
                value={entityType}
                onChange={(event) => setEntityType(event.target.value)}
              >
                {ENTITY_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </label>
            {needsKey && (
              <label>
                Entity key
                <input
                  type="text"
                  value={entityKey}
                  onChange={(event) => setEntityKey(event.target.value)}
                  placeholder={generateHint}
                  required={needsKey}
                />
              </label>
            )}
            {entityType === "bridge" && <p className="muted">Key format: topicA topicB</p>}
            <label>
              Window
              <select value={window} onChange={(event) => setWindow(event.target.value as WindowDays)}>
                <option value="1d">1d</option>
                <option value="7d">7d</option>
                <option value="30d">30d</option>
              </select>
            </label>
            <button type="submit" className="button button-primary" disabled={busy || !status?.configured}>
              {busy ? "Generating…" : "Generate"}
            </button>
          </form>
          {generateError ? <ErrorState error={generateError} /> : null}
          {generated ? <ArtifactCard artifact={generated} /> : null}
        </div>
      </section>

      <section className="card">
        <header className="card-header">
          <h2>Recent artifacts</h2>
          <button type="button" className="button" onClick={reload}>
            Refresh
          </button>
        </header>
        {loading ? (
          <div className="card-body">
            <Loading label="Loading artifacts…" />
          </div>
        ) : error ? (
          <div className="card-body">
            <ErrorState error={error} />
          </div>
        ) : list && list.items.length === 0 ? (
          <div className="card-body">
            <EmptyState>No cached artifacts yet — generate the first one above.</EmptyState>
          </div>
        ) : (
          <ul className="plain-list card-body">
            {(list?.items ?? []).map((artifact) => (
              <ArtifactCard
                key={`${artifact.entity_type}-${artifact.entity_key}-${artifact.generated_at}`}
                artifact={artifact}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}