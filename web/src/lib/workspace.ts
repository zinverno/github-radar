import { api } from "./api";
import type { DeveloperSummary } from "./types";

export const RELATIONSHIP_STATUSES = ["interested", "contacted", "connected", "collaborating", "paused"] as const;
export type RelationshipStatus = typeof RELATIONSHIP_STATUSES[number];
export type WorkspaceKind = "developers" | "repositories";
export interface WorkspaceState {
  saved: boolean;
  notes: string;
  relationship_status: RelationshipStatus;
  version: string | null;
  updated_at: string | null;
}
export interface WorkspaceEntity {
  id: number;
  topics: string[];
  contacts: Array<{ label: string; url: string }>;
  workspace: WorkspaceState;
}
export interface WorkspaceDeveloper extends WorkspaceEntity, DeveloperSummary {
  public_email: string | null;
}
export interface WorkspaceRepository extends WorkspaceEntity {
  full_name: string;
  description: string | null;
  primary_language: string | null;
}
export type WorkspaceItem = WorkspaceDeveloper | WorkspaceRepository;
export type WorkspaceRequest = <T>(path: string, init?: RequestInit) => Promise<T>;

/** Token is supplied from React memory, never persisted or attached to public requests. */
export function workspaceApi<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  return api<T>(`/workspace${path}`, {
    ...init,
    credentials: "omit", cache: "no-store", redirect: "error",
    headers: {
      Accept: "application/json", "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
}
