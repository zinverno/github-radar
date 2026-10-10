import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  error,
  onRetry,
}: {
  title?: string;
  error: Error;
  onRetry?: () => void;
}) {
  return (
    <section className="error-state" role="alert">
      <h2>{title}</h2>
      <p>{error.message}</p>
      <p className="muted">
        Try reloading the page; if it persists, check the server logs.
      </p>
      {onRetry ? (
        <button type="button" className="button" onClick={onRetry}>
          Retry
        </button>
      ) : (
        <Link className="button" to="/">
          Back to dashboard
        </Link>
      )}
    </section>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="empty-state" role="status">
      <p>{children}</p>
    </div>
  );
}

export function Card({
  title,
  children,
  action,
  className,
}: {
  title?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card${className ? ` ${className}` : ""}`}>
      {(title || action) && (
        <header className="card-header">
          <h2>{title}</h2>
          {action && <div className="card-action">{action}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}