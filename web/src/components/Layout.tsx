import { NavLink, Outlet } from "react-router-dom";
import { api } from "../lib/api";
import { DeploymentContext } from "../lib/deployment";
import { useAsync } from "../lib/useAsync";
import { STATIC_PREVIEW } from "../lib/preview";

const NAV: Array<{ to: string; label: string }> = [
  { to: "/", label: "Explore" },
  { to: "/repositories", label: "Repositories" },
  { to: "/topics", label: "Topics" },
  { to: "/developers", label: "Developers" },
  { to: "/ecosystem", label: "Ecosystem" },
  { to: "/bridges", label: "Bridges" },
  { to: "/ai", label: "AI" },
  { to: "/workspace", label: "Workspace" },
];

export function Layout() {
  const { data } = useAsync("capabilities", () => api<{ mode: "local" | "public" }>("/capabilities"));
  const mode = data?.mode === "local" ? "local" : data?.mode === "public" ? "public" : null;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main" onClick={event => {
        event.preventDefault();
        document.getElementById("main")?.focus();
      }}>
        Skip to content
      </a>
      <header className="site-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            ⌁
          </span>
          <span className="brand-name">github-radar</span>
        </div>
        <nav aria-label="Primary">
          <ul className="nav-list">
            {NAV.filter(item => mode === "local" || !["/ai", "/workspace"].includes(item.to)).map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) => (isActive ? "nav-link is-active" : "nav-link")}
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main id="main" className="site-main" tabIndex={-1}>
        {STATIC_PREVIEW && <aside className="demo-notice" aria-label="Demo data disclosure">
          <strong>DEMO DATA · v0.1.0-preview</strong>
          <p>Fictional repositories and synthetic metrics — not live GitHub analytics.</p>
          <p>Explore repositories, topics and the graph. No GitHub sync, sign-in, AI generation or private workspace.</p>
        </aside>}
        <DeploymentContext.Provider value={mode}><Outlet /></DeploymentContext.Provider>
      </main>
      <footer className="site-footer">
        <p>
          {STATIC_PREVIEW ? "github-radar · static demo · all data is fictional and stays in this preview."
            : "github-radar · intelligence from the tracked dataset only — every signal links back to measured evidence."}
        </p>
      </footer>
    </div>
  );
}
