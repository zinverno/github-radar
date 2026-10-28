import { api } from "./api";
import type {
  AiArtifactsList,
  AiEntityArtifacts,
  AiGenerateResult,
  AiStatus,
  CrossTopicBridge,
  DeveloperBridge,
  DeveloperDetail,
  DeveloperSummary,
  EcosystemReport,
  ExploreBody,
  GraphEdge,
  GraphNode,
  Page,
  RepoNodesPage,
  RepositoryBridge,
  RepositoryDetail,
  RepositorySummary,
  RepositorySummaryBody,
  SearchResult,
  StatusBody,
  TopicDetail,
  TopicRow,
  WindowDays,
} from "./types";

function params(values: Record<string, string | number | boolean | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== null && value !== undefined && value !== "") {
      qs.set(key, String(value));
    }
  }
  const raw = qs.toString();
  return raw ? `?${raw}` : "";
}

export function listRepositories(
  options: {
    limit?: number;
    offset?: number;
    sort?: "last_seen" | "name" | "stars" | "forks" | "momentum" | "stars_7d" | "trend";
    order?: "asc" | "desc";
    language?: string;
    trend?: string;
    minStars?: number;
    maxStars?: number;
    archived?: boolean;
    fork?: boolean;
  } = {},
): Promise<Page<RepositorySummary>> {
  const { limit = 25, offset = 0, sort = "last_seen", order = "desc", ...filters } = options;
  return api(`/repositories${params({ limit, offset, sort, order, ...filters })}`);
}

export function repositoriesSummary(window: WindowDays = "7d"): Promise<RepositorySummaryBody> {
  return api(`/repositories/summary${params({ window })}`);
}

export function repositoryDetail(
  fullName: string,
  window: WindowDays = "7d",
): Promise<RepositoryDetail> {
  return api(`/repositories/${encodeURIComponent(fullName)}${params({ window })}`);
}

export function listTopics(limit = 25, offset = 0): Promise<Page<TopicRow>> {
  return api(`/topics${params({ limit, offset })}`);
}

export function topicDetail(
  name: string,
  window: WindowDays = "7d",
): Promise<TopicDetail> {
  return api(`/topics/${encodeURIComponent(name)}${params({ window })}`);
}

export function listDevelopers(
  options: {
    limit?: number;
    offset?: number;
    sort?: string;
    topic?: string;
    window?: WindowDays;
    minConfidence?: number;
  } = {},
): Promise<Page<DeveloperSummary>> {
  const {
    limit = 25,
    offset = 0,
    sort = "relevance",
    topic,
    window = "7d",
    minConfidence,
  } = options;
  return api(`/developers${params({ limit, offset, sort, topic, window, minConfidence })}`);
}

export function emergingDevelopers(
  options: { limit?: number; window?: WindowDays } = {},
): Promise<Page<DeveloperSummary>> {
  const { limit = 25, window = "7d" } = options;
  return api(`/developers/emerging${params({ limit, window })}`);
}

export function developerDetail(
  login: string,
  window: WindowDays = "7d",
): Promise<DeveloperDetail> {
  return api(`/developers/${encodeURIComponent(login)}${params({ window })}`);
}

export function ecosystemReport(window: WindowDays = "7d"): Promise<EcosystemReport> {
  return api(`/ecosystem${params({ window })}`);
}

export async function graphNodes(
  nodeType: GraphNode["node_type"] | null,
): Promise<GraphNode[]> {
  const limit = 100;
  const rows: GraphNode[] = [];
  for (let offset = 0; offset < 10000; offset += limit) {
    const page = await api<RepoNodesPage>(
      `/ecosystem/nodes${params({ node_type: nodeType, limit, offset })}`,
    );
    rows.push(...page.items);
    if (rows.length >= page.total || page.items.length === 0) break;
  }
  return rows;
}

export async function graphEdges(): Promise<GraphEdge[]> {
  const limit = 100;
  const rows: GraphEdge[] = [];
  for (let offset = 0; offset < 10000; offset += limit) {
    const page = await api<{
      items: Array<{
        source: [string, number];
        target: [string, number];
        edge_type: string;
      }>;
      total: number;
      truncated: boolean;
    }>(`/ecosystem/edges${params({ limit, offset })}`);
    const items = page.items ?? [];
    for (const item of items) {
      rows.push({
        source: {
          node_type: item.source[0] as GraphNode["node_type"],
          node_id: item.source[1],
          label: "",
          degree: 0,
          weighted_degree: 0,
        },
        target: {
          node_type: item.target[0] as GraphNode["node_type"],
          node_id: item.target[1],
          label: "",
          degree: 0,
          weighted_degree: 0,
        },
        edge_type: item.edge_type as GraphEdge["edge_type"],
      });
    }
    if (rows.length >= page.total || items.length === 0) break;
  }
  return rows;
}

export function search(q: string, limit = 10): Promise<SearchResult> {
  return api(`/search${params({ q, limit })}`);
}

export function aiStatus(): Promise<AiStatus> {
  return api("/ai/status");
}

export function aiArtifacts(options: {
  limit?: number;
  entityType?: string;
  artifactType?: string;
}): Promise<AiArtifactsList> {
  const { limit = 25, entityType, artifactType } = options;
  return api(`/ai/artifacts${params({ limit, entityType, artifactType })}`);
}

export function aiEntityArtifacts(entityType: string, entityKey: string): Promise<AiEntityArtifacts> {
  return api(`/ai/artifacts/${encodeURIComponent(entityType)}/${encodeURIComponent(entityKey)}`);
}

export function generateArtifact(options: {
  entityType: string;
  entityKey?: string;
  artifactType?: string;
  window?: WindowDays;
  force?: boolean;
}, adminToken: string): Promise<AiGenerateResult> {
  const { entityType, entityKey, artifactType, window = "7d", force = false } = options;
  const topics = entityType === "bridge" ? entityKey?.trim().split(/\s+/) : undefined;
  return api(
    `/ai/generate${params({ entity_type: entityType, entity_key: entityKey,
      artifact_type: artifactType, window, force,
      topic_a: topics?.length === 2 ? topics[0] : undefined,
      topic_b: topics?.length === 2 ? topics[1] : undefined })}`,
    { method: "POST", credentials: "omit", cache: "no-store", redirect: "error",
      headers: { Authorization: `Bearer ${adminToken}` } },
  );
}

export function status(): Promise<StatusBody> {
  return api("/status");
}

export function explore(window: WindowDays = "7d", limit = 10): Promise<ExploreBody> {
  return api(`/explore${params({ window, limit })}`);
}

export function bridges(options: {
  window?: WindowDays;
  topic?: string;
  minConfidence?: number;
  limit?: number;
  offset?: number;
}): Promise<Page<DeveloperBridge>> {
  const { window = "7d", topic, minConfidence, limit = 25, offset = 0 } = options;
  return api(`/bridges${params({ window, topic, minConfidence, limit, offset })}`);
}

export function repositoryBridges(options: {
  window?: WindowDays;
  minConfidence?: number;
  limit?: number;
  offset?: number;
}): Promise<Page<RepositoryBridge>> {
  const { window = "7d", minConfidence, limit = 25, offset = 0 } = options;
  return api(`/bridges/repos${params({ window, minConfidence, limit, offset })}`);
}

export function crossTopicBridges(options: {
  topicA: string;
  topicB: string;
  window?: WindowDays;
  limit?: number;
}): Promise<{
  topic_a: string;
  topic_b: string;
  bridge_count: number;
  distinct_bridging_developers: number;
  average_bridge_score: number | null;
  shared_tracked_repositories: string[];
  top_bridges: CrossTopicBridge[];
}> {
  const { topicA, topicB, window = "7d", limit = 25 } = options;
  return api(
    `/bridges/cross-topic${params({ topic_a: topicA, topic_b: topicB, window, limit })}`,
  );
}
