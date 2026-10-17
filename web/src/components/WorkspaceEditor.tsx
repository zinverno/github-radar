import { useEffect, useState } from "react";
import { dateTime } from "../lib/format";
import { RELATIONSHIP_STATUSES, type RelationshipStatus, type WorkspaceItem, type WorkspaceKind, type WorkspaceRequest } from "../lib/workspace";

export function WorkspaceEditor({ item, kind, request, onClose, onSaved }: {
  item: WorkspaceItem; kind: WorkspaceKind; request: WorkspaceRequest;
  onClose: () => void; onSaved: () => void;
}) {
  const [notes, setNotes] = useState(item.workspace.notes);
  const [saved, setSaved] = useState(item.workspace.saved);
  const [status, setStatus] = useState(item.workspace.relationship_status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const dirty = notes !== item.workspace.notes || saved !== item.workspace.saved
    || status !== item.workspace.relationship_status;
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const name = "login" in item ? item.login : item.full_name;
  async function submit(remove = false) {
    setBusy(true); setError("");
    try {
      await request(`/${kind}/${item.id}${remove ? `?version=${item.workspace.version}` : ""}`, {
        method: remove ? "DELETE" : "PUT",
        ...(remove ? {} : { body: JSON.stringify({ saved, notes, relationship_status: status, version: item.workspace.version }) }),
      });
      onSaved();
    } catch (err) { setError(err instanceof Error ? err.message : "Changes could not be saved."); }
    finally { setBusy(false); }
  }
  return <section className="card card-body workspace-editor" aria-label={`Edit ${name}`}>
    <h2>Private details · {name}</h2>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <fieldset disabled={busy}>
        <label className="workspace-check"><input type="checkbox" checked={saved} onChange={event => setSaved(event.target.checked)} /> Saved to watchlist</label>
        <p className="muted">Unsaving keeps your notes and relationship status.</p>
        <label htmlFor="relationship-status">Relationship status</label>
        <select id="relationship-status" value={status} onChange={event => setStatus(event.target.value as RelationshipStatus)}>
          {RELATIONSHIP_STATUSES.map(value => <option key={value}>{value}</option>)}
        </select>
        <label htmlFor="private-notes">Personal notes</label>
        <textarea id="private-notes" rows={6} maxLength={10000} value={notes} onChange={event => setNotes(event.target.value)} />
        <p className="muted">{notes.length.toLocaleString()} / 10,000 characters · Last saved {dateTime(item.workspace.updated_at)}</p>
        <div className="workspace-actions">
          <button className="button" type="submit">{busy ? "Saving…" : "Save changes"}</button>
          <button className="button" type="button" onClick={() => {
            if (!dirty || window.confirm("Discard unsaved changes?")) onClose();
          }}>Cancel</button>
          {item.workspace.version && <button className="button" type="button" onClick={() => setConfirmDelete(true)}>Delete private entry…</button>}
        </div>
        {confirmDelete && <div role="alert">
          <p>Delete this watchlist entry, notes and relationship status? The tracked {kind === "developers" ? "developer" : "repository"} will remain.</p>
          <button className="button" type="button" onClick={() => void submit(true)}>Confirm deletion</button>{" "}
          <button className="button" type="button" onClick={() => setConfirmDelete(false)}>Keep entry</button>
        </div>}
      </fieldset>
      {error && <p role="alert">{error} Your unsaved text is still here. Copy it before cancelling or reloading.</p>}
    </form>
  </section>;
}
