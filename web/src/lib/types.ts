/** Shared view-model types mirroring the `/api/v1` JSON contract. */

export type IsoString = string;
export type ConfidenceLevel = "LOW" | "MEDIUM" | "HIGH";
export type WindowDays = "1d" | "7d" | "30d";

export interface Page<T> {
  items: T[];
  limit: number;
  offset: number;
  total: number;
  has_more: boolean;
}

export interface RepoNodesPage extends Omit<Page<GraphNode>, "has_more"> {
  returned: number;
  truncated: boolean;
  node_type?: string;
}

export interface Window {
  span_days: number | null;
  complete: boolean;
  start: IsoString | null;
  end: IsoString | null;
}

export interface FieldDelta {
  field: string;
  window_days: number;
  base_value: number | null;
  current_value: number | null;
  delta: number | null;
  per_day: number | null;
  growth_pct: number | null;
  base_gap_hours: number | null;
  window: Window;
  available: boolean;
}

export interface Momentum {
  score: number;
  max_score: number;
  stars_growth_pct: number | null;
  forks_growth_pct: number | null;
  recency: number | null;
  within_bounds: boolean;
  components: Record<string, number>;
}

export interface Trend {
  trend: string;
  label: string;
  explanation: string;
  growth_pct: number | null;
  recency: number | null;
}

export interface ConfidenceBody {
  level: ConfidenceLevel;
  score: number;
  snapshot_count: number;
  history_days: number | null;
  window_span_days?: number | null;
  base_gap_days?: number | null;
}

export interface RecentActivity {
  window_days: number;
  available: boolean;
  delta: number | null;
  complete: boolean;
  observations: number;
}

export interface TopicAggregate {
  name: string;
  repository_count: number;
  total_momentum: number | null;
  avg_momentum: number | null;
  share_with_momentum: number | null;
  confidence: { score: number; level: ConfidenceLevel; level_label?: string };
}

export interface RepositorySummary {
  id: number;
  full_name: string;
  owner_login: string;
  primary_language: string | null;
  description: string | null;
  homepage: string | null;
  html_url: string;
  is_archived: boolean;
  is_fork: boolean;
  pushed_at: IsoString | null;
  first_seen_at: IsoString;
  last_seen_at: IsoString;
  topics: string[];
  stars: number | null;
  forks: number | null;
  watchers: number | null;
  open_issues: number | null;
  stale_days: number | null;
  momentum: Momentum | null;
  trend: Trend | null;
  confidence: { level: ConfidenceLevel; score: number };
}

export interface RepoRanksRow {
  full_name: string;
  id?: number;
  stars: number | null;
  score: number | null;
  stars_growth_pct: number | null;
  forks_growth_pct?: number | null;
  recency: number | null;
  topics?: string[];
  trend?: Trend | null;
  confidence?: { level: ConfidenceLevel; score: number };
}

export interface RepositorySummaryBody {
  window_days: number;
  repository_count: number;
  healthy_count: number;
  average_daily_stars: number | null;
  average_daily_forks: number | null;
  trend_classes: Record<string, number>;
  high_confidence_count: number;
}

export interface RepositoryDetail {
  id: number;
  github_id: number;
  full_name: string;
  owner_login: string;
  owner_id: number | null;
  name: string;
  description: string | null;
  html_url: string;
  homepage: string | null;
  default_branch: string | null;
  primary_language: string | null;
  visibility: string;
  flags: {
    fork: boolean;
    archived: boolean;
    disabled: boolean;
    template: boolean;
  };
  created_at: IsoString | null;
  updated_at: IsoString | null;
  pushed_at: IsoString | null;
  first_seen_at: IsoString;
  last_seen_at: IsoString;
  stale_days: number | null;
  topics: string[];
  snapshot_count: number;
  metrics: {
    latest_captured_at: IsoString | null;
    latest_pushed_at: IsoString | null;
    snapshot_count: number;
    latest_counts: Record<string, number | null>;
    history_days: number | null;
  };
  momentum: Momentum | null;
  trend: Trend | null;
  confidence: ConfidenceBody | null;
  stars_7d: number | null;
  stars_30d: number | null;
  contributors: Array<{
    login: string;
    name: string | null;
    contributions: number;
    last_seen_at: IsoString | null;
  }>;
  graph: {
    reach: RepositoryReach;
    centrality: Centrality | null;
    related_repositories: RepositoryRelationship[];
  } | null;
  bridge: RepositoryBridge | null;
}

export interface TopicRow {
  name: string;
  repository_count: number;
}

export interface TopicDetail {
  name: string;
  id: number | null;
  repository_count: number;
  window_days: number;
  aggregate: TopicAggregate | null;
  repositories: Array<
    RepoRanksRow & {
      momentum: Momentum | null;
      trend: Trend | null;
      confidence: { level: ConfidenceLevel; score: number };
    }
  >;
  graph: {
    reach: TopicReach;
    centrality: Centrality | null;
    related_topics: TopicRelationship[];
  } | null;
}

export interface DeveloperActivity {
  window_days: number;
  score: number | null;
  label: string | null;
  components: Record<string, number>;
  positive_delta: number | null;
  complete: boolean;
  available: boolean;
}

export interface EcosystemScoreBody {
  score: number | null;
  label: string | null;
  components: Record<string, number>;
  positive_delta: number | null;
  breadth: number | null;
  available: boolean;
}

export interface Emerging {
  label: string;
  score: number;
  confidence_score: number;
  confidence_level: ConfidenceLevel;
  explanation: string;
  components: Record<string, number>;
}

export interface DeveloperSummary {
  login: string;
  name: string | null;
  followers: number | null;
  first_seen_at: IsoString;
  associated_repositories: string[];
  languages: string[];
  relevance: number | null;
  activity: DeveloperActivity | null;
  ecosystem: EcosystemScoreBody | null;
  emerging: Emerging;
  best_topic: {
    topic: string;
    score: number | null;
    confidence: { level: ConfidenceLevel; score: number };
  } | null;
}

export interface TopicRelevance {
  topic: string;
  score: number | null;
  components: Record<string, number>;
  confidence_score: number;
  confidence_level: ConfidenceLevel;
  repo_count: number;
  owned_count: number;
  contribution_share: number | null;
  recent_contribution_delta: number | null;
  momentum_mean: number | null;
  evidence: string[];
}

export interface DeveloperDetail extends DeveloperSummary {
  public_contacts: {
    available: string[];
    github: string | null;
    public_email: string | null;
    website: string | null;
    twitter: string | null;
  };
  profile_deltas: {
    window_days: number;
    deltas: Record<string, ProfileDelta | null>;
  } | null;
  topic_relevances: TopicRelevance[];
  graph: {
    reach: DeveloperReach;
    centrality: Centrality | null;
    co_contributors: CoContributorRelationship[];
    bridge: DeveloperBridge | null;
  } | null;
}

export interface ProfileDelta {
  window_days: number;
  base_value: number | null;
  current_value: number | null;
  delta: number | null;
  span_days: number | null;
  base_gap_days: number | null;
}

export interface GraphConfidence {
  score: number;
  level: ConfidenceLevel;
  distinct_repositories: number;
  history_coverage: number | null;
  recent_coverage: number | null;
  sample_size: number;
  terms: Record<string, number>;
  explanation: string | null;
  available: boolean;
}

export interface GraphNode {
  node_type: "DEVELOPER" | "REPOSITORY" | "TOPIC";
  node_id: number;
  label: string;
  degree: number;
  weighted_degree: number;
}

export interface GraphEdge {
  source: GraphNode;
  target: GraphNode;
  edge_type: "OWNS" | "CONTRIBUTES_TO" | "TAGGED_WITH";
  weight?: number;
}

export interface Centrality {
  node_type: string;
  node_id: number;
  label: string;
  degree: number;
  weighted_degree: number;
}

export interface DeveloperReach {
  developer_id: number;
  login: string;
  contributed_repositories: number;
  owned_repositories: number;
  reached_repositories: number;
  reached_developers: number;
  reached_topics: number;
}

export interface RepositoryReach {
  repository_id: number;
  full_name: string;
  contributing_developers: number;
  owning_developers: number;
  topics: number;
  sibling_repositories: number;
}

export interface TopicReach {
  topic_id: number;
  name: string;
  repositories: number;
  contributing_developers: number;
  owning_developers: number;
  associated_developers: number;
}

export interface Overlap {
  intersection_count: number;
  union_count: number;
  jaccard: number;
}

export interface RepositoryRelationship {
  repository_id: number;
  full_name: string;
  shared_developer_count: number;
  shared_topic_count: number;
  developer_overlap: Overlap;
  topic_overlap: Overlap;
  relationship_score: number;
  components: Record<string, number>;
  confidence: GraphConfidence;
}

export interface TopicRelationship {
  topic_id: number;
  topic: string;
  shared_repository_count: number;
  shared_developer_count: number;
  repository_overlap: Overlap;
  developer_overlap: Overlap;
  confidence: GraphConfidence;
}

export interface CoContributorRelationship {
  developer_id: number;
  login: string;
  relationship: string;
  shared_repository_count: number;
  shared_repositories: Array<{
    repository_id: number;
    full_name: string;
    a_contributions: number;
    b_contributions: number;
    a_share: number;
    b_share: number;
    momentum_score: number | null;
  }>;
  strength: number;
  components: Record<string, number>;
  missing_components: string[];
  confidence: GraphConfidence;
}

export interface TopicBridgeEvidence {
  topic: string;
  repository_count: number;
  repositories: string[];
  topic_strength: number;
  total_contributions: number;
  recent_delta: number | null;
  recent_activity_available: boolean;
  history_coverage: number | null;
}

export interface DeveloperBridge {
  developer_id: number;
  login: string;
  bridge_score: number;
  components: Record<string, number>;
  missing_components: string[];
  meaningful_topic_memberships: number;
  distinct_supporting_repositories: number;
  small_sample: boolean;
  topic_evidence: TopicBridgeEvidence[];
  repository_evidence: string[];
  confidence: GraphConfidence;
}

export interface RepositoryBridge {
  repository_id: number;
  full_name: string;
  bridge_score: number;
  components: Record<string, number>;
  missing_components: string[];
  contributor_count: number;
  topic_count: number;
  cross_repository_contributors: number;
  cross_topic_contributors: number;
  small_sample: boolean;
  confidence: GraphConfidence;
}

export interface CrossTopicBridge {
  developer_id: number;
  login: string;
  topic_a: TopicBridgeEvidence;
  topic_b: TopicBridgeEvidence;
  shared_tracked_repositories: string[];
  bridge_score: number;
  components: Record<string, number>;
  missing_components: string[];
  small_sample: boolean;
  confidence: GraphConfidence;
}

export interface EcosystemReport {
  developer_count: number;
  repository_count: number;
  topic_count: number;
  node_counts: { developers: number; repositories: number; topics: number };
  edge_counts: { owns: number; contributes_to: number; tagged_with: number };
  component_count: number;
  largest_component: ConnectedComponent | null;
  top_developer_centrality: Centrality[];
  top_repository_centrality: Centrality[];
  top_topic_centrality: Centrality[];
  top_developer_bridges: DeveloperBridge[];
  top_repository_bridges: RepositoryBridge[];
  top_cross_topic_bridges: CrossTopicBridge[];
}

export interface ConnectedComponent {
  node_count: number;
  developer_count: number;
  repository_count: number;
  topic_count: number;
  developer_logins: string[];
  repository_full_names: string[];
  topic_names: string[];
  canonical_node: [string, number];
}

export interface SearchResult {
  query: string;
  repositories: Array<{ full_name: string; id: number; stars: number | null }>;
  topics: Array<{ name: string }>;
  developers: Array<{ login: string; name: string | null; followers: number | null }>;
}

export interface AiStatus {
  configured: boolean;
  base_url_host: string | null;
  model: string | null;
  per_run_request_cap: number;
  max_output_tokens: number;
  max_evidence_chars: number;
  cached_artifacts: number;
  cached_by_type: Record<string, number>;
  prompt_versions: Record<string, string>;
}

export interface AiArtifact {
  entity_type: string;
  entity_key: string;
  artifact_type: string;
  window_days: number | null;
  prompt_version: string;
  provider: string;
  model: string;
  generated_at: IsoString;
  source_fingerprint: string;
  headline: string;
  summary: string;
  key_points: Array<{ text: string; evidence_ids: string[] }>;
  unknowns: string[];
  confidence: ConfidenceLevel;
  input_tokens: number;
  output_tokens: number;
}

export interface AiArtifactsList {
  items: AiArtifact[];
  limit: number;
  total: number;
  truncated: boolean;
}

export interface AiEntityArtifacts {
  entity_type: string;
  entity_key: string;
  items: AiArtifact[];
  prompt_versions: Record<string, string>;
}

export interface AiGenerateResult {
  cache_hit: boolean;
  generated_at: IsoString;
  artifact: AiArtifact;
}

export interface Freshness {
  observed_at: IsoString;
  newest_observation_at: IsoString | null;
  stale_days: number | null;
}

export interface StatusBody {
  reference_time: IsoString;
  freshness: Freshness;
  dataset: {
    repositories: number;
    developers: number;
    topics: number;
    snapshots: number;
    oldest_snapshot_at: IsoString | null;
    newest_snapshot_at: IsoString | null;
  };
  ai: AiStatus;
}

export interface ExploreBody {
  reference_time: IsoString;
  freshness: Freshness;
  window_days: number;
  tracked_repositories: number;
  repositories_with_momentum: number;
  repositories: RepoRanksRow[];
  topics: TopicAggregate[];
  developers: {
    tracked: number;
    emerging_count: number;
    emerging: Array<{
      login: string;
      name: string | null;
      followers: number | null;
      associated_repositories: string[];
      emerging: Emerging;
    }>;
  };
  ecosystem: {
    developers: number;
    repositories: number;
    topics: number;
    edges: { owns: number; contributes_to: number; tagged_with: number };
  };
  bridges: DeveloperBridge[];
}