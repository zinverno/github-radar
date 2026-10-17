import { NavLink, Outlet } from "react-router-dom";

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
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
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
            {NAV.map((item) => (
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
      <main id="main" className="site-main">
        <Outlet />
      </main>
      <footer className="site-footer">
        <p>
          github-radar · intelligence from the tracked dataset only — every signal
          links back to measured evidence.
        </p>
      </footer>
    </div>
  );
}
