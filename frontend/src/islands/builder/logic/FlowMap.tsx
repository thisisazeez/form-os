// ============================================================================
// OpenSurvey — Logic FLOW MAP (lightweight node-graph preview).
//
// A dependency-free SVG/CSS preview of the survey's flow (matching the mockup's
// right column): question nodes stacked top-to-bottom + ending nodes, with edges:
//   • the LINEAR fall-through path (muted dashed line) question→next,
//   • CONDITIONAL branches from logic rules (brand-colored solid curves):
//       jumpTo → target question/ending, skip → skipped question(s),
//       endSurvey → the (default/named) ending.
//
// No graph library — nodes are absolutely positioned in a fixed grid and edges are
// SVG <path>/<line> drawn from measured node centers. Purely derived from `survey`
// (no local state), so it re-renders as rules change. Read-only.
// ============================================================================
import { useLayoutEffect, useRef, useState } from "react";

import { resolveI18n } from "../../../lib/i18n";
import type { Survey } from "../../../lib/types";
import { flattenRules } from "./logicModel";

// Layout constants (px).
const NODE_W = 200;
const NODE_H = 64;
const ROW_GAP = 44; // vertical gap between stacked nodes
const PAD_TOP = 16;

interface FlowNode {
  id: string;
  kind: "question" | "ending";
  title: string;
  subtitle: string;
  ruleCount: number;
}

interface FlowEdge {
  from: string;
  to: string;
  kind: "linear" | "branch";
}

function buildNodes(survey: Survey): FlowNode[] {
  const lang = (survey.languages ?? []).find((l) => l.default)?.code ?? "default";
  const rulesByHost = new Map<string, number>();
  for (const fr of flattenRules(survey)) {
    rulesByHost.set(fr.hostQuestionId, (rulesByHost.get(fr.hostQuestionId) ?? 0) + 1);
  }
  const qNodes: FlowNode[] = survey.questions.map((q, i) => ({
    id: q.id,
    kind: "question",
    title: `Question ${i + 1}`,
    subtitle: resolveI18n(q.headline, lang, lang) || "(untitled)",
    ruleCount: rulesByHost.get(q.id) ?? 0,
  }));
  const eNodes: FlowNode[] = (survey.endings ?? []).map((e) => ({
    id: e.id,
    kind: "ending",
    title: "Ending",
    subtitle: e.headline?.default || e.id,
    ruleCount: 0,
  }));
  return [...qNodes, ...eNodes];
}

function buildEdges(survey: Survey): FlowEdge[] {
  const edges: FlowEdge[] = [];
  const qIds = survey.questions.map((q) => q.id);

  // Linear fall-through (question i → question i+1).
  for (let i = 0; i < qIds.length - 1; i++) {
    edges.push({ from: qIds[i], to: qIds[i + 1], kind: "linear" });
  }

  // Conditional branches from each rule.
  for (const { rule, hostQuestionId } of flattenRules(survey)) {
    const a = rule.action;
    if (a.type === "jumpTo") {
      edges.push({ from: hostQuestionId, to: a.target, kind: "branch" });
    } else if (a.type === "skip") {
      const targets = Array.isArray(a.target) ? a.target : [a.target];
      for (const t of targets) edges.push({ from: hostQuestionId, to: t, kind: "branch" });
    } else if (a.type === "endSurvey") {
      const endId = a.endingId ?? (survey.endings ?? [])[0]?.id;
      if (endId) edges.push({ from: hostQuestionId, to: endId, kind: "branch" });
    }
    // 'require' is not a navigation edge — omitted from the flow map.
  }
  return edges;
}

export function FlowMap({ survey }: { survey: Survey }) {
  const nodes = buildNodes(survey);
  const edges = buildEdges(survey);

  // Measure node centers for edge drawing. Node positions are deterministic from
  // the grid, but we measure the container width to center the column responsively.
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(NODE_W + 48);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => setWidth(el.clientWidth || NODE_W + 48);
    update();
    // ResizeObserver keeps edges aligned if the panel resizes.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, []);

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <p className="text-center text-sm text-muted">Add questions to see the flow.</p>
      </div>
    );
  }

  // Node center positions (single centered column).
  const cx = width / 2;
  const centerOf = (index: number) => ({
    x: cx,
    y: PAD_TOP + index * (NODE_H + ROW_GAP) + NODE_H / 2,
  });
  const indexOf: Record<string, number> = {};
  nodes.forEach((n, i) => (indexOf[n.id] = i));

  const svgHeight = PAD_TOP * 2 + nodes.length * NODE_H + (nodes.length - 1) * ROW_GAP;

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-auto p-4" data-testid="flow-map">
      <div className="relative mx-auto" style={{ height: svgHeight, maxWidth: NODE_W + 120 }}>
        {/* edges layer */}
        <svg
          className="pointer-events-none absolute inset-0"
          width="100%"
          height={svgHeight}
          aria-hidden="true"
        >
          {edges.map((e, i) => {
            const fi = indexOf[e.from];
            const ti = indexOf[e.to];
            if (fi === undefined || ti === undefined) return null;
            const from = centerOf(fi);
            const to = centerOf(ti);
            return <Edge key={i} from={from} to={to} kind={e.kind} />;
          })}
        </svg>

        {/* nodes layer */}
        {nodes.map((n, i) => {
          const c = centerOf(i);
          return (
            <div
              key={n.id}
              data-testid={n.kind === "ending" ? "flow-ending" : "flow-question"}
              className="absolute"
              style={{ left: c.x - NODE_W / 2, top: c.y - NODE_H / 2, width: NODE_W, height: NODE_H }}
            >
              <NodeCard node={n} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function NodeCard({ node }: { node: FlowNode }) {
  const isEnding = node.kind === "ending";
  return (
    <div
      className={
        "flex h-full flex-col justify-center rounded-xl border px-3 py-2 text-center shadow-sm " +
        (isEnding ? "border-success/40 bg-success/10" : "border-hairline bg-white")
      }
    >
      <span
        className={
          "text-[10px] font-semibold uppercase tracking-wide " +
          (isEnding ? "text-success" : "text-muted")
        }
      >
        {node.title}
      </span>
      <span className="truncate text-sm font-medium text-ink">{node.subtitle}</span>
      {node.ruleCount > 0 && (
        <span className="mt-0.5 text-[10px] font-medium text-brand">
          {node.ruleCount} rule{node.ruleCount === 1 ? "" : "s"} attached
        </span>
      )}
    </div>
  );
}

function Edge({
  from,
  to,
  kind,
}: {
  from: { x: number; y: number };
  to: { x: number; y: number };
  kind: "linear" | "branch";
}) {
  if (kind === "linear") {
    // straight muted dashed line, drawn between the node edges
    return (
      <line
        x1={from.x}
        y1={from.y + NODE_H / 2}
        x2={to.x}
        y2={to.y - NODE_H / 2}
        className="stroke-hairline"
        strokeWidth={2}
        strokeDasharray="4 4"
      />
    );
  }
  // Conditional branch: a brand-colored curve bowing to the right so it doesn't
  // overlap the linear spine. Works for both forward and (rare) same-column jumps.
  const midY = (from.y + to.y) / 2;
  const bow = Math.min(80, 32 + Math.abs(to.y - from.y) * 0.18);
  const path = `M ${from.x + NODE_W / 2 - 8} ${from.y} C ${from.x + bow + NODE_W / 2} ${from.y}, ${
    to.x + bow + NODE_W / 2
  } ${to.y}, ${to.x + NODE_W / 2 - 8} ${to.y} `;
  return (
    <path
      d={path}
      fill="none"
      className="stroke-brand"
      strokeWidth={2}
      strokeLinecap="round"
      opacity={0.85}
      data-testid="flow-branch-edge"
      // midY referenced so future label placement has an anchor; keeps lint quiet.
      data-mid-y={midY}
    />
  );
}
