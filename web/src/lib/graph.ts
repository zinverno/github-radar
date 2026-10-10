import type { GraphEdge, GraphNode } from "./types";

export type NodeKey = string;

export const NODE_TYPES: GraphNode["node_type"][] = ["DEVELOPER", "REPOSITORY", "TOPIC"];

/** Bounded per-type node budget for the visualization projection. */
export const MAX_PER_ROW = 18;

/** Cap on direct neighbours pulled into the focused projection. */
export const MAX_NEIGHBORS = 30;

export function nodeKey(node: { node_type: string; node_id: number }): NodeKey {
  return `${node.node_type}:${node.node_id}`;
}

export function scoreOf(node: GraphNode): number {
  return node.weighted_degree;
}

export function byScoreDesc(a: GraphNode, b: GraphNode): number {
  if (b.weighted_degree !== a.weighted_degree) return b.weighted_degree - a.weighted_degree;
  if (b.degree !== a.degree) return b.degree - a.degree;
  return a.label.localeCompare(b.label);
}

export interface GraphProjection {
  nodes: GraphNode[];
  edges: GraphEdge[];
  byKey: Map<NodeKey, GraphNode>;
  visibleEdgeCount: number;
  totalEdgeCount: number;
  counts: Record<GraphNode["node_type"], { total: number; shown: number }>;
  truncated: boolean;
  selectedKey: NodeKey | null;
  neighborKeys: Set<NodeKey>;
  /** Full undirected adjacency over all supplied edges. */
  adjacentKeys: Map<NodeKey, Set<NodeKey>>;
}

/**
 * Deterministic, bounded projection of the full graph for rendering.
 *
 * Rows are capped per type; the focused selection (and its neighbourhood)
 * wins the budget, then the most-connected remaining nodes fill the rest, all
 * ordered by the API's existing centrality scores (weighted degree, degree,
 * label).  Edges are kept only when both endpoints are visible.
 */
export function projectGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
  selectedKey: NodeKey | null,
  maxPerRow: number = MAX_PER_ROW,
): GraphProjection {
  const byKey = new Map<NodeKey, GraphNode>();
  for (const node of nodes) byKey.set(nodeKey(node), node);

  const adjacent = new Map<NodeKey, Set<NodeKey>>();
  for (const edge of edges) {
    const source = nodeKey(edge.source);
    const target = nodeKey(edge.target);
    if (!byKey.has(source) || !byKey.has(target) || source === target) continue;
    const a = adjacent.get(source);
    if (a) a.add(target);
    else adjacent.set(source, new Set([target]));
    const b = adjacent.get(target);
    if (b) b.add(source);
    else adjacent.set(target, new Set([source]));
  }

  const rankedByType: Record<GraphNode["node_type"], GraphNode[]> = {
    DEVELOPER: [],
    REPOSITORY: [],
    TOPIC: [],
  };
  for (const node of nodes) rankedByType[node.node_type].push(node);
  for (const type of NODE_TYPES) rankedByType[type].sort(byScoreDesc);

  const active = selectedKey !== null && byKey.has(selectedKey) ? selectedKey : null;
  let neighborKeys = new Set<NodeKey>();
  if (active !== null) {
    const neighbours = adjacent.get(active) ?? new Set<NodeKey>();
    neighborKeys = new Set([...neighbours].slice(0, MAX_NEIGHBORS));
  }

  const chosen = new Set<NodeKey>();
  for (const type of NODE_TYPES) {
    const ranked = rankedByType[type];
    for (const node of ranked) {
      if (chosen.size >= maxPerRow * 3) break;
      const key = nodeKey(node);
      const isActive = key === active;
      const isNeighbour = neighborKeys.has(key);
      if (!chosen.has(key) && (isActive || isNeighbour)) chosen.add(key);
    }
  }
  for (const type of NODE_TYPES) {
    const ranked = rankedByType[type];
    let taken = 0;
    for (const node of ranked) {
      if (taken >= maxPerRow) break;
      const key = nodeKey(node);
      if (chosen.has(key)) {
        taken += 1;
        continue;
      }
      if (chosen.size >= maxPerRow * 3) break;
      chosen.add(key);
      taken += 1;
    }
  }

  const visible: GraphNode[] = [];
  const order = new Map<NodeKey, number>();
  for (const node of nodes) {
    if (chosen.has(nodeKey(node))) {
      order.set(nodeKey(node), visible.length);
      visible.push(node);
    }
  }

  const visibleEdges: GraphEdge[] = [];
  for (const edge of edges) {
    if (chosen.has(nodeKey(edge.source)) && chosen.has(nodeKey(edge.target))) {
      visibleEdges.push(edge);
    }
  }

  const counts = { DEVELOPER: { total: 0, shown: 0 }, REPOSITORY: { total: 0, shown: 0 }, TOPIC: { total: 0, shown: 0 } };
  for (const node of nodes) counts[node.node_type].total += 1;
  for (const node of visible) counts[node.node_type].shown += 1;
  const truncated = NODE_TYPES.some((type) => counts[type].shown < counts[type].total);

  return {
    nodes: visible,
    edges: visibleEdges,
    byKey,
    visibleEdgeCount: visibleEdges.length,
    totalEdgeCount: edges.length,
    counts,
    truncated,
    selectedKey: active,
    neighborKeys,
    adjacentKeys: adjacent,
  };
}

/** Long labels are clipped for rendering; a tooltip carries the full text. */
export function truncateLabel(label: string, maxLength = 16): string {
  const safe = Math.max(3, maxLength);
  return label.length > safe ? `${label.slice(0, safe - 1)}…` : label;
}