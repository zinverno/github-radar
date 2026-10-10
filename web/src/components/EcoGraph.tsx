import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  NODE_TYPES,
  nodeKey,
  projectGraph,
  truncateLabel,
  type NodeKey,
} from "../lib/graph";
import {
  IDENTITY_VIEW,
  defaultLabelLength,
  labelBand,
  panBy,
  zoomAt,
  type GraphView,
} from "../lib/graphView";
import { plain } from "../lib/format";
import type { GraphEdge, GraphNode } from "../lib/types";

const TYPE_ROW: Record<GraphNode["node_type"], number> = {
  DEVELOPER: 1,
  REPOSITORY: 2,
  TOPIC: 3,
};

const TYPE_LABEL: Record<GraphNode["node_type"], string> = {
  DEVELOPER: "developer",
  REPOSITORY: "repository",
  TOPIC: "topic",
};

const EDGE_COLOR: Record<string, string> = {
  owns: "#8b949e",
  contributes_to: "#58a6ff",
  tagged_with: "#3fb950",
};

const NODE_COLOR: Record<GraphNode["node_type"], string> = {
  DEVELOPER: "#d29922",
  REPOSITORY: "#58a6ff",
  TOPIC: "#3fb950",
};

const ROW_LABEL: Record<number, string> = { 1: "Developers", 2: "Repositories", 3: "Topics" };

const WIDTH = 960;
const HEIGHT = 480;
const MARGIN_X = 90;
const ROW_Y: Record<number, number> = { 1: 95, 2: 255, 3: 410 };

const PAN_STEP = 40;

function entityPath(node: GraphNode): string {
  if (node.node_type === "REPOSITORY") return `/repositories/${node.label}`;
  if (node.node_type === "TOPIC") return `/topics/${encodeURIComponent(node.label)}`;
  return `/developers/${node.label}`;
}

/**
 * Deterministic bounded layered SVG of the ecosystem graph (see ``projectGraph``
 * for the projection). The viewport supports mouse-wheel zoom, drag-to-pan and
 * a reset; node labels are staggered to reduce overlap and the full label is
 * revealed on hover, keyboard focus or selection while the rest stays bounded.
 * The details panel keeps the focused node's evidence and a link to its page.
 */
export function EcoGraph({ nodes, edges }: { nodes: GraphNode[]; edges: GraphEdge[] }) {
  const [selectedKey, setSelectedKey] = useState<NodeKey | null>(null);
  const [hoveredKey, setHoveredKey] = useState<NodeKey | null>(null);
  const [view, setView] = useState<GraphView>(IDENTITY_VIEW);
  const [dragging, setDragging] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const dragStart = useRef<{
    clientX: number;
    clientY: number;
    view: GraphView;
    moved: boolean;
  } | null>(null);

  const projection = useMemo(
    () => projectGraph(nodes, edges, selectedKey),
    [nodes, edges, selectedKey],
  );

  const selectedNode = projection.selectedKey
    ? projection.byKey.get(projection.selectedKey) ?? null
    : null;

  const neighbourLabels = useMemo(() => {
    if (projection.selectedKey === null) return [] as string[];
    const labels: string[] = [];
    for (const key of projection.neighborKeys) {
      const node = projection.byKey.get(key);
      if (node) labels.push(node.label);
    }
    return labels;
  }, [projection.selectedKey, projection.neighborKeys, projection.byKey]);

  const positioned = useMemo(() => {
    const rows = new Map<GraphNode["node_type"], GraphNode[]>();
    for (const node of projection.nodes) {
      rows.set(node.node_type, [...(rows.get(node.node_type) ?? []), node]);
    }
    const result: Array<{ node: GraphNode; x: number; y: number; index: number; total: number }> = [];
    for (const type of NODE_TYPES) {
      const row = TYPE_ROW[type];
      const typeNodes = rows.get(type) ?? [];
      const span = Math.max(WIDTH - MARGIN_X * 2, 1);
      const total = Math.max(typeNodes.length, 1);
      const step = total === 1 ? 0 : span / (total - 1);
      typeNodes.forEach((node, index) => {
        result.push({
          node,
          x: MARGIN_X + index * step,
          y: ROW_Y[row] ?? 255,
          index,
          total: typeNodes.length,
        });
      });
    }
    return result;
  }, [projection.nodes]);

  const incidentKeys = useMemo(() => {
    if (projection.selectedKey === null) return null;
    return new Set([projection.selectedKey, ...projection.neighborKeys]);
  }, [projection.selectedKey, projection.neighborKeys]);

  const keyOf = (node: Pick<GraphNode, "node_type" | "node_id">): NodeKey => nodeKey(node);

  const shownLabels = `${projection.nodes.length} of ${nodes.length}`;

  // Wheel zoom must run as a native listener so preventDefault actually stops
  // the page from scrolling while zooming the viewport.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      const px = (event.clientX - rect.left) * (WIDTH / rect.width);
      const py = (event.clientY - rect.top) * (HEIGHT / rect.height);
      const factor = Math.pow(1.0015, -event.deltaY);
      setView((current) => zoomAt(current, factor, px, py));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const toView = (clientX: number, clientY: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: (clientX - rect.left) * (WIDTH / rect.width),
      y: (clientY - rect.top) * (HEIGHT / rect.height),
    };
  };

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragStart.current = { clientX: event.clientX, clientY: event.clientY, view, moved: false };
    setDragging(true);
  };

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!dragStart.current) return;
    const delta = toView(event.clientX, event.clientY);
    const start = toView(dragStart.current.clientX, dragStart.current.clientY);
    const dx = delta.x - start.x;
    const dy = delta.y - start.y;
    if (Math.hypot(dx, dy) > 3) dragStart.current.moved = true;
    setView(panBy(dragStart.current.view, dx, dy));
  };

  const onPointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!dragStart.current) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    dragStart.current = null;
    setDragging(false);
  };

  const onSvgKeyDown = (event: React.KeyboardEvent<SVGSVGElement>) => {
    switch (event.key) {
      case "+":
      case "=":
        event.preventDefault();
        setView((current) => zoomAt(current, 1.25, WIDTH / 2, HEIGHT / 2));
        break;
      case "-":
      case "_":
        event.preventDefault();
        setView((current) => zoomAt(current, 0.8, WIDTH / 2, HEIGHT / 2));
        break;
      case "0":
      case "Home":
      case "r":
      case "R":
        event.preventDefault();
        setView(IDENTITY_VIEW);
        break;
      case "ArrowLeft":
        event.preventDefault();
        setView((current) => panBy(current, -PAN_STEP, 0));
        break;
      case "ArrowRight":
        event.preventDefault();
        setView((current) => panBy(current, PAN_STEP, 0));
        break;
      case "ArrowUp":
        event.preventDefault();
        setView((current) => panBy(current, 0, -PAN_STEP));
        break;
      case "ArrowDown":
        event.preventDefault();
        setView((current) => panBy(current, 0, PAN_STEP));
        break;
    }
  };

  return (
    <>
      <figure className="graph-wrap">
        <div className="graph-wrap-header" role="group" aria-label="Graph legend and view controls">
          <div className="graph-controls">
            <button
              className="button"
              type="button"
              onClick={() => setView(zoomAt(view, 1.25, WIDTH / 2, HEIGHT / 2))}
              aria-label="Zoom in"
            >
              +
            </button>
            <button
              className="button"
              type="button"
              onClick={() => setView(zoomAt(view, 0.8, WIDTH / 2, HEIGHT / 2))}
              aria-label="Zoom out"
            >
              −
            </button>
            <button
              className="button"
              type="button"
              onClick={() => setView(IDENTITY_VIEW)}
              disabled={view.x === 0 && view.y === 0 && view.k === 1}
            >
              Reset view
            </button>
          </div>
          <ul className="legend">
            {NODE_TYPES.map((type) => (
              <li key={type}>
                <span className="swatch" style={{ background: NODE_COLOR[type] }} />
                {ROW_LABEL[TYPE_ROW[type]]} {projection.counts[type].shown}/
                {projection.counts[type].total}
              </li>
            ))}
            <li>
              <span className="swatch swatch-line" style={{ background: EDGE_COLOR.contributes_to }} />{" "}
              contributes
            </li>
            <li>
              <span className="swatch swatch-line" style={{ background: EDGE_COLOR.tagged_with }} />{" "}
              tagged with
            </li>
            <li>
              <span className="swatch swatch-line" style={{ background: EDGE_COLOR.owns }} /> owns
            </li>
          </ul>
        </div>
        <svg
          ref={svgRef}
          className={`graph-canvas${dragging ? " is-dragging" : ""}`}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={`Ecosystem graph with ${shownLabels} nodes and ${projection.visibleEdgeCount} visible edges. Wheel to zoom, drag to pan, +/- and arrow keys from the keyboard, press 0 to reset.`}
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={() => {
            dragStart.current = null;
            setDragging(false);
          }}
          onKeyDown={onSvgKeyDown}
        >
          <g transform={`translate(${view.x}, ${view.y}) scale(${view.k})`}>
            {([1, 2, 3] as const).map((row) => (
              <text key={row} x={8} y={ROW_Y[row] ?? 255} className="graph-row-label">
                {ROW_LABEL[row]}
              </text>
            ))}
            {projection.edges.map((edge, index) => {
              const source = positioned.find((item) => keyOf(item.node) === keyOf(edge.source));
              const target = positioned.find((item) => keyOf(item.node) === keyOf(edge.target));
              if (!source || !target) return null;
              const focusEdge =
                projection.selectedKey !== null &&
                (keyOf(edge.source) === projection.selectedKey ||
                  keyOf(edge.target) === projection.selectedKey);
              const betweenNeighbours =
                incidentKeys !== null &&
                incidentKeys.has(keyOf(edge.source)) &&
                incidentKeys.has(keyOf(edge.target));
              return (
                <line
                  key={`${keyOf(edge.source)}->${keyOf(edge.target)}-${edge.edge_type}-${index}`}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={EDGE_COLOR[edge.edge_type] ?? "#8b949e"}
                  strokeOpacity={focusEdge ? 0.95 : betweenNeighbours ? 0.5 : 0.12}
                />
              );
            })}
            {positioned.map(({ node, x, y, index, total }) => {
              const key = keyOf(node);
              const selected = key === projection.selectedKey;
              const neighbour = projection.neighborKeys.has(key);
              const dim = projection.selectedKey !== null && !selected && !neighbour;
              const revealed = selected || neighbour || hoveredKey === key;
              const label = truncateLabel(node.label, defaultLabelLength(total));
              return (
                <g
                  key={key}
                  transform={`translate(${x}, ${y})`}
                  className={
                    selected
                      ? "graph-node is-selected"
                      : neighbour
                        ? "graph-node is-neighbour"
                        : "graph-node"
                  }
                  opacity={dim ? 0.3 : 1}
                  onClick={() => {
                    if (dragStart.current?.moved) return;
                    setSelectedKey(key);
                  }}
                  onMouseEnter={() => setHoveredKey(key)}
                  onMouseLeave={() => setHoveredKey((current) => (current === key ? null : current))}
                  onFocus={() => setHoveredKey(key)}
                  onBlur={() => setHoveredKey((current) => (current === key ? null : current))}
                  role="button"
                  aria-label={node.label}
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedKey(key);
                    }
                  }}
                >
                  <title>
                    {node.label} — {TYPE_LABEL[node.node_type]}, degree {plain(node.degree)},
                    weighted {plain(node.weighted_degree)}
                    {selected ? " (focused)" : neighbour ? " (neighbour)" : ""}
                  </title>
                  <circle
                    r={node.node_type === "REPOSITORY" ? 9 : 7}
                    fill={NODE_COLOR[node.node_type]}
                  />
                  <text
                    y={labelBand(index, total)}
                    textAnchor="middle"
                    className={`graph-label${revealed ? " graph-label-revealed" : ""}`}
                  >
                    {revealed ? node.label : label}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
        <figcaption className="muted graph-caption">
          {shownLabels} nodes · {projection.visibleEdgeCount} of {projection.totalEdgeCount} edges ·
          wheel zoom · drag to pan · press 0 to reset
          {projection.truncated
            ? " — click a node to focus its neighbourhood and reveal more."
            : " — click a node to focus it."}
        </figcaption>
      </figure>

      {selectedNode && (
        <section className="card">
          <header className="card-header">
            <h2>Focused node: {selectedNode.label}</h2>
            <button className="button" onClick={() => setSelectedKey(null)}>
              Clear focus
            </button>
          </header>
          <div className="card-body">
            <dl className="property">
              <dt>Type</dt>
              <dd>{TYPE_LABEL[selectedNode.node_type]}</dd>
              <dt>Degree</dt>
              <dd>{plain(selectedNode.degree)}</dd>
              <dt>Weighted degree</dt>
              <dd>{plain(selectedNode.weighted_degree)}</dd>
              <dt>Neighbours</dt>
              <dd>
                {neighbourLabels.length > 0
                  ? `${neighbourLabels.length} (${neighbourLabels.slice(0, 3).join(", ")}${
                      neighbourLabels.length > 3 ? ", …" : ""
                    })`
                  : "none"}
              </dd>
            </dl>
            <Link className="button button-primary" to={entityPath(selectedNode)}>
              Open {TYPE_LABEL[selectedNode.node_type]} page →
            </Link>
          </div>
        </section>
      )}
    </>
  );
}