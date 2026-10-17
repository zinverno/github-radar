import { useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { EmptyState, ErrorState, Loading } from "../components/basic";
import { ConfidencePill } from "../components/confidence";
import { Pager } from "../components/Pager";
import { WorkspaceEditor } from "../components/WorkspaceEditor";
import { score } from "../lib/format";
import type { Page } from "../lib/types";
import { useAsync } from "../lib/useAsync";
import { RELATIONSHIP_STATUSES, type WorkspaceItem, type WorkspaceKind, type WorkspaceRequest } from "../lib/workspace";

export function WorkspacePage({ kind }: { kind: WorkspaceKind }) {
  const { request } = useOutletContext<{ request: WorkspaceRequest }>();
  const [filters, setFilters] = useState({ saved: "saved", topic: "", status: "", min_relevance: "" });
  const [applied, setApplied] = useState(filters);
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<WorkspaceItem | null>(null);
  const [notice, setNotice] = useState("");
  const query = new URLSearchParams({ saved: applied.saved, limit: "25", offset: String(offset) });
  if (applied.topic.trim()) query.set("topic", applied.topic.trim());
  if (applied.status) query.set("status", applied.status);
  if (kind === "developers" && applied.min_relevance) query.set("min_relevance", applied.min_relevance);
  const { data, loading, error, reload } = useAsync([kind, query.toString()], () =>
    request<Page<WorkspaceItem>>(`/${kind}?${query}`));
  return <div>
    <h2>{kind === "developers" ? "Developer watchlist" : "Repository watchlist"}</h2>
    <p>Choose “All tracked” to add entries. Contacts come from stored public GitHub metadata.</p>
    {notice && <p role="status">{notice}</p>}
    {editing ? <WorkspaceEditor key={editing.id} item={editing} kind={kind} request={request}
      onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setNotice("Private entry updated."); reload(); }} /> : <>
      <form className="workspace-filters" onSubmit={event => { event.preventDefault(); setOffset(0); setApplied(filters); }}>
        <label>Saved status<select value={filters.saved} onChange={e => setFilters({ ...filters, saved: e.target.value })}>
          <option value="saved">Saved</option><option value="all">All tracked</option><option value="unsaved">Not saved</option>
        </select></label>
        <label>Topic<input value={filters.topic} maxLength={100} onChange={e => setFilters({ ...filters, topic: e.target.value })} placeholder="e.g. mcp" /></label>
        <label>Relationship<select value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })}>
          <option value="">Any status</option>{RELATIONSHIP_STATUSES.map(status => <option key={status}>{status}</option>)}
        </select></label>
        {kind === "developers" && <label>Minimum relevance<input type="number" min="0" max="1" step="0.01" value={filters.min_relevance} onChange={e => setFilters({ ...filters, min_relevance: e.target.value })} /></label>}
        <button className="button">Apply filters</button>
      </form>
      {kind === "developers" && <p className="muted">Existing deterministic intelligence · 7-day window · relevance uses the selected topic, or the best available topic. “—” means unavailable.</p>}
      {loading ? <Loading label="Loading private workspace…" /> : error ? <ErrorState error={error} onRetry={reload} /> : data && <>
        {data.items.length === 0 && <EmptyState>No matching entries. Try “All tracked” or clear your filters. Developers require existing stored profile/contributor data; this page does not collect it.</EmptyState>}
        <div className="workspace-list">{data.items.map(item => {
          const developer = "login" in item;
          const name = developer ? item.login : item.full_name;
          const detail = developer ? `/developers/${encodeURIComponent(item.login)}` : `/repositories/${item.full_name.split("/").map(encodeURIComponent).join("/")}`;
          return <article className="card card-body" key={item.id}>
            <div className="workspace-item-head"><h3><Link to={detail}>{name}</Link></h3>
              <button className="button" onClick={() => { setEditing(item); setNotice(""); }}>{item.workspace.version ? "Edit private details" : "Add to workspace"}</button></div>
            <p>{item.workspace.saved ? "Saved" : "Not saved"}{item.workspace.version && ` · ${item.workspace.relationship_status}`}</p>
            {!developer && <p>{item.description}</p>}
            <p className="muted">Topics: {item.topics.join(", ") || "None observed"}</p>
            {developer && <>
              <dl className="workspace-signals">
                <div><dt>Relevance</dt><dd>{score(item.relevance)}</dd></div>
                <div><dt>Activity</dt><dd>{item.activity?.available ? `${item.activity.label ?? "Observed"} · ${score(item.activity.score)}` : "—"}</dd></div>
                <div><dt>Ecosystem</dt><dd>{item.ecosystem?.available ? `${item.ecosystem.label ?? "Observed"} · ${score(item.ecosystem.score)}` : "—"}</dd></div>
                <div><dt>Emerging</dt><dd>{item.emerging.label} · {score(item.emerging.score)}</dd></div>
                <div><dt>Confidence</dt><dd><ConfidencePill level={item.emerging.confidence_level} /></dd></div>
              </dl>
              <p className="muted">{item.emerging.explanation}</p>
              {item.public_email && <p>Public profile email: {item.public_email}</p>}
            </>}
            <div className="workspace-contacts">{item.contacts.map(contact => <a key={contact.url} href={contact.url} target="_blank" rel="noopener noreferrer">{contact.label} ↗</a>)}</div>
            {item.workspace.notes && <p className="workspace-note">{item.workspace.notes}</p>}
          </article>;
        })}</div>
        <Pager limit={data.limit} offset={data.offset} total={data.total} onPage={setOffset} />
      </>}
    </>}
  </div>;
}
