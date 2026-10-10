import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section aria-labelledby="not-found-title">
      <h1 id="not-found-title">Page not found</h1>
      <p className="muted">The path you requested does not exist in this application.</p>
      <Link className="button" to="/">
        Back to dashboard
      </Link>
    </section>
  );
}