import type { ReactNode } from "react";
import type { ConfidenceLevel } from "../lib/types";

export function Property({ label, children }: { label: string; children: ReactNode }) {
  return (
    <dl className="property">
      <dt>{label}</dt>
      <dd>{children ?? <span className="muted">—</span>}</dd>
    </dl>
  );
}

export function Tag({ children, href }: { children: ReactNode; href?: string }) {
  const className = "tag";
  if (href) {
    return (
      <a className={className} href={href}>
        {children}
      </a>
    );
  }
  return <span className={className}>{children}</span>;
}

export function LevelTag({ level }: { level: ConfidenceLevel }) {
  return (
    <span className={`tag tag-level tag-${level.toLowerCase()}`}>{level}</span>
  );
}

export function GithubLink({ fullName }: { fullName: string }) {
  return (
    <a className="external-link" href={`https://github.com/${fullName}`}>
      {fullName}
      <span aria-hidden="true"> ↗</span>
    </a>
  );
}

export function ExternalLink({
  href,
  children,
  label,
}: {
  href: string;
  children: ReactNode;
  label?: string;
}) {
  return (
    <a
      className="external-link"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
    >
      {children}
      <span aria-hidden="true"> ↗</span>
    </a>
  );
}