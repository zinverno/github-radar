import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { ApiError } from "../lib/api";
import { workspaceApi, type WorkspaceRequest } from "../lib/workspace";

export function WorkspaceLayout() {
  const [token, setToken] = useState("");
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const accessEpoch = useRef(0);
  const lock = useCallback(() => {
    accessEpoch.current += 1;
    setToken(""); setInput(""); setBusy(false);
  }, []);
  useEffect(() => {
    window.addEventListener("pagehide", lock);
    return () => { window.removeEventListener("pagehide", lock); accessEpoch.current += 1; };
  }, [lock]);
  const request: WorkspaceRequest = useCallback(async <T,>(path: string, init?: RequestInit) => {
    try {
      return await workspaceApi<T>(token, path, init);
    } catch (err) {
      if (err instanceof ApiError && [401, 403, 503].includes(err.status)) {
        lock(); setError(err.message);
      }
      throw err;
    }
  }, [token, lock]);

  return <div className="workspace">
    <header className="page-head">
      <div><h1>Networking workspace</h1><p>Private watchlists, notes and relationships · single admin, local access.</p></div>
      {token && <button className="button" onClick={lock}>Lock workspace</button>}
    </header>
    {!token ? <form className="card card-body workspace-unlock" onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setError("");
      const attempt = ++accessEpoch.current;
      try {
        await workspaceApi(input, "/session");
        if (attempt === accessEpoch.current) { setToken(input); setInput(""); }
      } catch (err) {
        if (attempt === accessEpoch.current) setError(err instanceof Error ? err.message : "Could not unlock workspace.");
      } finally { if (attempt === accessEpoch.current) setBusy(false); }
    }}>
      <h2>Unlock private workspace</h2>
      <p>Enter the admin token configured for this local installation. Reloading or leaving the workspace locks it.</p>
      <label htmlFor="workspace-token">Admin token</label>
      <input id="workspace-token" type="password" autoComplete="off" value={input}
        onChange={event => setInput(event.target.value)} required minLength={32} maxLength={256} />
      <button className="button" disabled={busy}>{busy ? "Checking access…" : "Unlock"}</button>
      {error && <p role="alert">{error}</p>}
    </form> : <>
      <nav className="workspace-tabs" aria-label="Workspace">
        <NavLink to="/workspace/developers">Developer watchlist</NavLink>
        <NavLink to="/workspace/repositories">Repository watchlist</NavLink>
      </nav>
      <Outlet context={{ request }} />
    </>}
  </div>;
}
