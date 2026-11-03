/** Entirely fictional, authored fixtures. Never sourced from a database or GitHub. */
import { ApiError } from "../lib/api";
import type {
  EcosystemReport, ExploreBody, FieldDelta, GraphNode, Page,
  RepositoryDetail, RepositorySummary, TopicAggregate, TopicDetail,
} from "../lib/types";

const END = "2026-10-01T12:00:00Z";
const START = "2026-09-01T12:00:00Z";
const seeds = [
  { name: "signal-kit", language: "TypeScript", stars: 1000, delta: 100, topics: ["developer-tools", "observability"] },
  { name: "query-garden", language: "Python", stars: 240, delta: 48, topics: ["developer-tools", "data"] },
  { name: "trace-board", language: "Go", stars: 1600, delta: 0, topics: ["developer-tools", "observability"] },
  { name: "quiet-archive", language: "Rust", stars: 600, delta: -30, topics: ["developer-tools"], archived: true },
  { name: "signal-kit-fork", language: "TypeScript", stars: 88, delta: 2, topics: ["developer-tools"], fork: true },
  { name: "new-spark", language: "Go", stars: 12, delta: null, topics: ["developer-tools"] },
];

function delta(stars: number, change: number | null, days: number): FieldDelta | null {
  if (change === null) return null;
  return {
    field: "stars", window_days: days, current_value: stars, base_value: stars - change,
    delta: change, per_day: change / days, growth_pct: change / (stars - change) * 100,
    base_gap_hours: 0, available: true,
    window: { span_days: days, complete: true, end: END,
      start: new Date(Date.parse(END) - days * 86400000).toISOString() },
  };
}

const details: RepositoryDetail[] = seeds.map((seed, index) => {
  const week = delta(seed.stars, seed.delta, 7);
  const month = delta(seed.stars, seed.delta === null ? null : seed.delta * 2, 30);
  const trend = seed.delta === null ? "new" : seed.delta > 0 ? "rising" : seed.delta < 0 ? "declining" : "steady";
  return {
    id: index + 1, github_id: index + 1, full_name: `demo-lab/${seed.name}`,
    name: seed.name, owner_login: "demo-lab", owner_id: null,
    description: `DEMO DATA: ${seed.name} is a fictional ${seed.language} project for exploring the interface.`,
    html_url: "", homepage: null, default_branch: "main", primary_language: seed.language,
    visibility: "public", flags: { fork: !!seed.fork, archived: !!seed.archived, disabled: false, template: false },
    created_at: START, updated_at: END, pushed_at: END,
    first_seen_at: seed.delta === null ? END : START, last_seen_at: END, stale_days: null,
    topics: seed.topics, snapshot_count: week ? 31 : 1,
    metrics: { stars_7d: week, stars_30d: month, latest_captured_at: END, latest_pushed_at: END,
      snapshot_count: week ? 31 : 1, history_days: week ? 30 : 0,
      latest_counts: { stars: seed.stars, forks: 10 + index, watchers: 4 + index, open_issues: index } },
    momentum: week ? { score: Math.max(0, 60 - index * 12), max_score: 100,
      stars_growth_pct: week.growth_pct, forks_growth_pct: 0, recency: 1, within_bounds: true, components: {} } : null,
    trend: { trend, label: trend, explanation: "Illustrative demo classification; not a live GitHub signal.",
      growth_pct: week?.growth_pct ?? null, recency: 1, window_days: 7, window: week?.window ?? null },
    confidence: { level: week ? "MEDIUM" : "LOW", score: week ? 0.6 : 0.1,
      snapshot_count: week ? 31 : 1, history_days: week ? 30 : 0 },
    stars_7d: week?.delta ?? null, stars_30d: month?.delta ?? null,
    contributors: [], graph: null, bridge: null,
  };
});

const repositories: RepositorySummary[] = details.map(repo => ({
  ...repo, is_archived: repo.flags.archived, is_fork: repo.flags.fork,
  stars: repo.metrics.latest_counts.stars!, forks: repo.metrics.latest_counts.forks!,
  watchers: repo.metrics.latest_counts.watchers!, open_issues: repo.metrics.latest_counts.open_issues!,
  confidence: repo.confidence!,
}));
const topicNames = ["developer-tools", "data", "observability", "demo-empty"];
const topics = topicNames.map(name => ({ name, repository_count: repositories.filter(r => r.topics.includes(name)).length }));
const ranked = repositories.map(repo => ({ ...repo, score: repo.momentum?.score ?? null,
  stars_growth_pct: repo.trend?.growth_pct ?? null, recency: 1 }));
const aggregates: TopicAggregate[] = topics.map(topic => {
  const scores = repositories.filter(r => r.topics.includes(topic.name) && r.momentum).map(r => r.momentum!.score);
  const total = scores.reduce((a, b) => a + b, 0);
  return { ...topic, total_momentum: scores.length ? total : null,
    avg_momentum: scores.length ? total / scores.length : null,
    share_with_momentum: topic.repository_count ? scores.length / topic.repository_count : null,
    confidence: { level: "LOW", score: 0.3 } };
});
const nodes: GraphNode[] = [
  ...repositories.map(r => ({ node_type: "REPOSITORY" as const, node_id: r.id, label: r.full_name,
    degree: r.topics.length, weighted_degree: r.topics.length })),
  ...topics.map((t, i) => ({ node_type: "TOPIC" as const, node_id: i + 1, label: t.name,
    degree: t.repository_count, weighted_degree: t.repository_count })),
];
const edges = repositories.flatMap(r => r.topics.map(topic => ({
  source: ["REPOSITORY", r.id], target: ["TOPIC", topicNames.indexOf(topic) + 1], edge_type: "TAGGED_WITH",
})));
const ecosystem: EcosystemReport = {
  developer_count: 0, repository_count: repositories.length, topic_count: topics.length,
  node_counts: { developers: 0, repositories: repositories.length, topics: topics.length },
  edge_counts: { owns: 0, contributes_to: 0, tagged_with: edges.length }, component_count: 2,
  largest_component: null, top_developer_centrality: [], top_repository_centrality: [], top_topic_centrality: [],
  top_developer_bridges: [], top_repository_bridges: [], top_cross_topic_bridges: [],
};
const explore: ExploreBody = {
  reference_time: END, freshness: { observed_at: END, newest_observation_at: END, stale_days: null },
  window_days: 7, tracked_repositories: repositories.length,
  repositories_with_momentum: repositories.filter(r => r.momentum).length,
  repositories: ranked.filter(r => r.momentum), topics: aggregates,
  developers: { tracked: 0, emerging_count: 0, emerging: [] },
  ecosystem: { ...ecosystem.node_counts, edges: ecosystem.edge_counts }, bridges: [],
};

function page<T>(items: T[], query: URLSearchParams): Page<T> {
  const limit = Math.max(1, Math.min(100, Number(query.get("limit")) || 25));
  const offset = Math.max(0, Number(query.get("offset")) || 0);
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset,
    has_more: offset + limit < items.length };
}

/** Only these read routes exist. Unknown routes and mutations never fall through to fetch. */
export function previewResponse(path: string, init?: RequestInit): unknown {
  if (init?.method && init.method.toUpperCase() !== "GET") {
    throw new ApiError(405, { error: { code: "preview_read_only", message: "This demo has no live backend or write operations.", details: {} } });
  }
  const [route = "", search] = path.split("?");
  const query = new URLSearchParams(search);
  if (route === "/capabilities") return { mode: "public" };
  if (route === "/explore") return structuredClone(explore);
  if (route === "/repositories") {
    const items = repositories.filter(r =>
      (!query.get("language") || r.primary_language?.toLowerCase().includes(query.get("language")!.toLowerCase())) &&
      (!query.get("trend") || r.trend?.trend === query.get("trend")) &&
      (!query.has("minStars") || (r.stars ?? 0) >= Number(query.get("minStars"))) &&
      (!query.has("maxStars") || (r.stars ?? 0) <= Number(query.get("maxStars"))) &&
      (query.get("archived") !== "false" || !r.is_archived) && (query.get("fork") !== "false" || !r.is_fork));
    const value = (r: RepositorySummary) => {
      switch (query.get("sort")) {
        case "name": return r.full_name;
        case "stars": return r.stars;
        case "forks": return r.forks;
        case "momentum": return r.momentum?.score;
        case "stars_7d": return details.find(d => d.id === r.id)?.stars_7d;
        case "trend": return r.trend?.trend;
        default: return r.last_seen_at;
      }
    };
    items.sort((a, b) => {
      const av = value(a), bv = value(b);
      if (av == null) return bv == null ? 0 : 1;
      if (bv == null) return -1;
      const order = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return order * (query.get("order") === "asc" ? 1 : -1);
    });
    return structuredClone(page(items, query));
  }
  const repo = details.find(r => route === `/repositories/${encodeURIComponent(r.full_name)}`);
  if (repo) return structuredClone(repo);
  if (route === "/topics") return page(topics, query);
  const topic = aggregates.find(t => route === `/topics/${encodeURIComponent(t.name)}`);
  if (topic) return structuredClone({ ...topic, id: topicNames.indexOf(topic.name) + 1,
    window_days: 7, aggregate: topic, repositories: ranked.filter(r => r.topics.includes(topic.name)), graph: null } satisfies TopicDetail);
  if (["/developers", "/developers/emerging", "/bridges", "/bridges/repos"].includes(route)) return page([], query);
  if (route === "/bridges/cross-topic") return { topic_a: query.get("topic_a"), topic_b: query.get("topic_b"),
    bridge_count: 0, distinct_bridging_developers: 0, average_bridge_score: null, shared_tracked_repositories: [], top_bridges: [] };
  if (route === "/ecosystem") return structuredClone(ecosystem);
  if (route === "/ecosystem/nodes") return page(nodes.filter(n => !query.get("node_type") || n.node_type === query.get("node_type")), query);
  if (route === "/ecosystem/edges") return page(edges, query);
  throw new ApiError(404, { error: { code: "preview_not_found", message: "This page or record is not included in the demo dataset.", details: {} } });
}
