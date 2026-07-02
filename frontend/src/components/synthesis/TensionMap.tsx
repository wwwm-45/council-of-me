import { useState, useMemo } from 'react';
import type { CoreTension } from '../../api/client';

interface Props {
  tensions: CoreTension[];
}

// ─── Constants ──────────────────────────────────────────────────────────────

const VB = 400;         // viewBox size
const CENTER = VB / 2;  // center point
const RADIUS = 140;     // agent circle radius
const NODE_R = 22;      // agent node radius

const TENSION_COLORS = ['#f97316', '#8b5cf6', '#06b6d4', '#e11d48', '#16a34a'];

/** Agent name fragment → fill color */
const AGENT_FILLS: Record<string, string> = {
  '共情': '#fda4af', Empathic: '#fda4af',
  '理性': '#7dd3fc', Rational: '#7dd3fc',
  '批判': '#fcd34d', Critical: '#fcd34d',
  '创意': '#c4b5fd', Creative: '#c4b5fd',
  '综合': '#5eead4', Synthesizer: '#5eead4',
};

function agentFill(name: string): string {
  for (const [k, v] of Object.entries(AGENT_FILLS)) {
    if (name.includes(k)) return v;
  }
  return '#cbd5e1';
}

/** Shorten agent name for display under node (max 4 chars) */
function shortName(name: string): string {
  // Chinese names: take last 2-4 chars after role pattern
  // e.g. "共情倾听者" → "共情", "理性分析师" → "理性"
  for (const k of Object.keys(AGENT_FILLS)) {
    if (name.includes(k)) return k;
  }
  return name.slice(0, 4);
}

// ─── Types ──────────────────────────────────────────────────────────────────

interface AgentNode {
  name: string;
  x: number;
  y: number;
  color: string;
}

interface TensionArc {
  tensionId: string;
  tensionName: string;
  fromAgent: string;
  toAgent: string;
  intensity: number;
  color: string;
  poleALabel: string;
  poleBLabel: string;
  tensionIndex: number;
  arcIndex: number;  // disambiguate multiple arcs for same tension
}

// ─── Layout helpers ─────────────────────────────────────────────────────────

function computeAgentPositions(agentNames: string[]): Map<string, AgentNode> {
  const map = new Map<string, AgentNode>();
  const n = agentNames.length;
  for (let i = 0; i < n; i++) {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
    map.set(agentNames[i], {
      name: agentNames[i],
      x: CENTER + RADIUS * Math.cos(angle),
      y: CENTER + RADIUS * Math.sin(angle),
      color: agentFill(agentNames[i]),
    });
  }
  return map;
}

function buildArcs(
  tensions: CoreTension[],
  positions: Map<string, AgentNode>,
): TensionArc[] {
  const arcs: TensionArc[] = [];
  tensions.forEach((t, ti) => {
    let arcIdx = 0;
    for (const aName of t.pole_a.agents) {
      for (const bName of t.pole_b.agents) {
        if (!positions.has(aName) || !positions.has(bName)) continue;
        arcs.push({
          tensionId: t.tension_id,
          tensionName: t.name,
          fromAgent: aName,
          toAgent: bName,
          intensity: t.intensity,
          color: TENSION_COLORS[ti % TENSION_COLORS.length],
          poleALabel: t.pole_a.label,
          poleBLabel: t.pole_b.label,
          tensionIndex: ti,
          arcIndex: arcIdx++,
        });
      }
    }
  });
  return arcs;
}

/** Quadratic Bezier path between two agent nodes, pulled toward center */
function arcPath(
  from: AgentNode,
  to: AgentNode,
  tensionIndex: number,
  arcIndex: number,
): string {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  // Pull midpoint toward center; offset per tension + arc to avoid overlap
  const pull = 0.35 + tensionIndex * 0.12 + arcIndex * 0.06;
  const cx = mx + (CENTER - mx) * pull;
  const cy = my + (CENTER - my) * pull;
  return `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`;
}

// ─── Component ──────────────────────────────────────────────────────────────

export default function TensionMap({ tensions }: Props) {
  const [hoveredAgent, setHoveredAgent] = useState<string | null>(null);
  const [hoveredTension, setHoveredTension] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{
    x: number; y: number; arc: TensionArc;
  } | null>(null);

  // Collect all unique agents across all tensions
  const allAgentNames = useMemo(() => {
    const set = new Set<string>();
    tensions.forEach(t => {
      t.pole_a.agents.forEach(a => set.add(a));
      t.pole_b.agents.forEach(a => set.add(a));
    });
    return Array.from(set);
  }, [tensions]);

  const positions = useMemo(() => computeAgentPositions(allAgentNames), [allAgentNames]);
  const arcs = useMemo(() => buildArcs(tensions, positions), [tensions, positions]);

  if (!tensions.length) return null;

  function isArcHighlighted(arc: TensionArc): boolean {
    if (hoveredTension) return arc.tensionId === hoveredTension;
    if (hoveredAgent) return arc.fromAgent === hoveredAgent || arc.toAgent === hoveredAgent;
    return true;  // nothing hovered — show all
  }

  function handleArcHover(arc: TensionArc, from: AgentNode, to: AgentNode) {
    setHoveredTension(arc.tensionId);
    // Tooltip at midpoint of arc (approximate)
    const tx = (from.x + to.x) / 2;
    const ty = (from.y + to.y) / 2;
    setTooltip({ x: tx, y: ty, arc });
  }

  function handleArcLeave() {
    setHoveredTension(null);
    setTooltip(null);
  }

  return (
    <section className="mb-6">
      <h3 className="text-sm font-medium text-slate-500 mb-2">张力关系图</h3>
      <div className="relative">
        <svg
          viewBox={`0 0 ${VB} ${VB}`}
          className="w-full max-w-[500px] mx-auto"
          role="img"
          aria-label="Agent张力星座图"
        >
          {/* Background circle (table surface) */}
          <circle
            cx={CENTER} cy={CENTER} r={RADIUS + 30}
            fill="#f8fafc" stroke="#e2e8f0" strokeWidth={1}
          />

          {/* Tension arcs */}
          {arcs.map((arc, i) => {
            const from = positions.get(arc.fromAgent)!;
            const to = positions.get(arc.toAgent)!;
            const highlighted = isArcHighlighted(arc);
            return (
              <path
                key={`arc-${i}`}
                d={arcPath(from, to, arc.tensionIndex, arc.arcIndex)}
                stroke={arc.color}
                strokeWidth={highlighted && hoveredTension === arc.tensionId
                  ? 2 + arc.intensity * 5
                  : 1 + arc.intensity * 4}
                fill="none"
                opacity={highlighted ? 0.75 : 0.12}
                strokeLinecap="round"
                style={{ cursor: 'pointer', transition: 'opacity 0.2s, stroke-width 0.2s' }}
                onMouseEnter={() => handleArcHover(arc, from, to)}
                onMouseLeave={handleArcLeave}
              />
            );
          })}

          {/* Agent nodes */}
          {allAgentNames.map(name => {
            const node = positions.get(name)!;
            const isActive = !hoveredAgent || hoveredAgent === name;
            return (
              <g
                key={name}
                onMouseEnter={() => { setHoveredAgent(name); setHoveredTension(null); setTooltip(null); }}
                onMouseLeave={() => setHoveredAgent(null)}
                style={{ cursor: 'pointer' }}
                opacity={isActive ? 1 : 0.4}
              >
                <circle
                  cx={node.x} cy={node.y} r={NODE_R}
                  fill={node.color} stroke="white" strokeWidth={2.5}
                />
                <text
                  x={node.x} y={node.y + 1}
                  textAnchor="middle" dominantBaseline="middle"
                  fill="white" fontSize={12} fontWeight="bold"
                >
                  {name.charAt(0)}
                </text>
                <text
                  x={node.x} y={node.y + NODE_R + 14}
                  textAnchor="middle" fill="#64748b" fontSize={10}
                >
                  {shortName(name)}
                </text>
              </g>
            );
          })}

          {/* Center label */}
          <text
            x={CENTER} y={CENTER}
            textAnchor="middle" dominantBaseline="middle"
            fill="#cbd5e1" fontSize={11}
          >
            张力关系
          </text>
        </svg>

        {/* Tooltip (HTML overlay) */}
        {tooltip && (
          <div
            className="absolute pointer-events-none bg-white rounded-xl shadow-lg border border-slate-100 px-3 py-2 text-xs z-10"
            style={{
              left: `${(tooltip.x / VB) * 100}%`,
              top: `${(tooltip.y / VB) * 100}%`,
              transform: 'translate(-50%, -120%)',
              maxWidth: 200,
            }}
          >
            <p className="font-semibold text-slate-700 mb-0.5">{tooltip.arc.tensionName}</p>
            <p className="text-slate-500">
              <span style={{ color: '#b45309' }}>{tooltip.arc.poleALabel}</span>
              {' ↔ '}
              <span style={{ color: '#0369a1' }}>{tooltip.arc.poleBLabel}</span>
            </p>
            <p className="text-slate-400 mt-0.5">
              强度 {Math.round(tooltip.arc.intensity * 100)}%
            </p>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mt-2 justify-center">
        {tensions.map((t, i) => (
          <div
            key={t.tension_id}
            className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer"
            onMouseEnter={() => setHoveredTension(t.tension_id)}
            onMouseLeave={() => { setHoveredTension(null); setTooltip(null); }}
          >
            <span
              className="inline-block w-4 h-1 rounded-full"
              style={{ backgroundColor: TENSION_COLORS[i % TENSION_COLORS.length] }}
            />
            {t.name}
            <span className="text-slate-400">{Math.round(t.intensity * 100)}%</span>
          </div>
        ))}
      </div>
    </section>
  );
}
