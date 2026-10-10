import { describe, expect, it } from "vitest";

import {
  byScoreDesc,
  nodeKey,
  projectGraph,
  truncateLabel,
  type GraphProjection,
} from "../graph";
import type { GraphEdge, GraphNode } from "../types";

function repo(id: number, label: string, weighted: number, degree = 1): GraphNode {
  return { node_type: "REPOSITORY", node_id: id, label, degree, weighted_degree: weighted };
}

function dev(id: number, label: string, weighted: number, degree = 1): GraphNode {
  return { node_type: "DEVELOPER", node_id: id, label, degree, weighted_degree: weighted };
}

function edge(a: GraphNode, b: GraphNode): GraphEdge {
  return {
    source: { node_type: a.node_type, node_id: a.node_id, label: a.label, degree: a.degree, weighted_degree: a.weighted_degree },
    target: { node_type: b.node_type, node_id: b.node_id, label: b.label, degree: b.degree, weighted_degree: b.weighted_degree },
    edge_type: "CONTRIBUTES_TO",
  };
}

function keys(p: GraphProjection): string[] {
  return p.nodes.map((n) => nodeKey(n));
}

describe("nodeKey / truncateLabel / byScoreDesc", () => {
  it("keys nodes by type and id", () => {
    expect(nodeKey({ node_type: "TOPIC", node_id: 9 })).toBe("TOPIC:9");
  });

  it("clips labels and keeps the full string short", () => {
    expect(truncateLabel("short")).toBe("short");
    expect(truncateLabel("a-very-long-repository-name")).toBe("a-very-long-rep…");
  });

  it("orders by weighted degree, then degree, then label", () => {
    const ordered = [
      repo(1, "b", 1, 5),
      repo(2, "a", 1, 5),
      repo(3, "c", 2, 1),
    ].sort(byScoreDesc);
    expect(ordered.map((n) => n.node_id)).toEqual([3, 2, 1]);
  });
});

describe("projectGraph", () => {
  // A path repo-01---repo-02---...---repo-40. Each node has a distinct
  // weighted centrality (== id), middles are degree 2.
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  for (let id = 1; id <= 40; id += 1) {
    nodes.push(repo(id, `repo-${String(id).padStart(2, "0")}`, id, id === 1 || id === 40 ? 1 : 2));
  }
  for (let id = 1; id < 40; id += 1) {
    edges.push(edge(nodes[id - 1]!, nodes[id]!));
  }
  const keyOf = (weighted: number) => nodeKey(nodes[weighted - 1]!);

  it("bounds rows per type and reports truncation", () => {
    const p = projectGraph(nodes, edges, null, 10);
    expect(p.nodes.length).toBe(10);
    expect(p.counts.REPOSITORY).toEqual({ total: 40, shown: 10 });
    expect(p.truncated).toBe(true);
    // The budget is spent on the highest-weighted nodes.
    expect(keys(p)).toContain(keyOf(40));
    expect(keys(p)).not.toContain(keyOf(1));
  });

  it("keeps the focused node and its neighbours visible", () => {
    const focus = keyOf(35); // neighbours: repo-34 and repo-36
    const p = projectGraph(nodes, edges, focus, 10);
    expect(keys(p)).toContain(focus);
    expect(p.neighborKeys.has(keyOf(34))).toBe(true);
    expect(p.neighborKeys.has(keyOf(36))).toBe(true);
    expect(keys(p)).toContain(keyOf(34));
    expect(keys(p)).toContain(keyOf(36));
  });

  it("only projects edges whose endpoints are visible", () => {
    const p = projectGraph(nodes, edges, null, 10);
    // repo-40..31 are visible; the path keeps 9 edges among them.
    expect(p.visibleEdgeCount).toBe(9);
    expect(p.visibleEdgeCount).toBeLessThan(p.totalEdgeCount);
    for (const edge of p.edges) {
      expect(keys(p)).toContain(nodeKey(edge.source));
      expect(keys(p)).toContain(nodeKey(edge.target));
    }
  });

  it("ignores an unknown focus key gracefully", () => {
    const p = projectGraph(nodes, edges, "TOPIC:999", 10);
    expect(p.selectedKey).toBeNull();
    expect(p.nodes.length).toBe(10);
  });

  it("mixes types by the same budget", () => {
    const mixed = [...nodes];
    for (let id = 1; id <= 12; id += 1) mixed.push(dev(id, `dev-${id}`, id));
    const p = projectGraph(mixed, edges, null, 5);
    expect(p.counts.REPOSITORY.shown).toBe(5);
    expect(p.counts.DEVELOPER.shown).toBe(5);
    expect(p.nodes.length).toBe(10);
    expect(p.truncated).toBe(true);
  });
});