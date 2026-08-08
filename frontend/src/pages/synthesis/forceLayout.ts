import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceRadial,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force';
import type { GraphEdge, LandscapeDisplayModel } from './types';

export interface LayoutPoint {
  x: number;
  y: number;
}

interface LayoutNode extends SimulationNodeDatum {
  id: string;
}

type LayoutLink = SimulationLinkDatum<LayoutNode> & { edge: GraphEdge };

const CX = 500;
const CY = 500;
const BOUNDS_MIN = 90;
const BOUNDS_MAX = 910;
const TICKS = 300;

/** 由节点 id 哈希出稳定的初始极角，保证布局与数据顺序无关且可复现。 */
function hashAngle(id: string): number {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  }
  return (hash % 360) * (Math.PI / 180);
}

function linkDistance(edge: GraphEdge): number {
  switch (edge.type) {
    case 'affinity': return 200 - 120 * edge.weight;
    case 'opposition': return 150;
    case 'support': return 130;
    case 'outcome': return 240;
  }
}

function linkStrength(edge: GraphEdge): number {
  switch (edge.type) {
    case 'affinity': return 0.3 + 0.5 * edge.weight;
    case 'opposition': return 0.55;
    case 'support': return 0.5;
    case 'outcome': return 0.15;
  }
}

function clamp(value: number): number {
  return Math.min(Math.max(value, BOUNDS_MIN), BOUNDS_MAX);
}

export function computeGraphLayout(model: LandscapeDisplayModel): Map<string, LayoutPoint> {
  const ids = [
    'center',
    ...model.tensions.map((tension) => tension.id),
    ...model.voices.map((voice) => voice.id),
    ...model.outcomes.map((outcome) => outcome.id),
  ];
  const collideRadii = new Map<string, number>();
  collideRadii.set('center', 95);
  model.tensions.forEach((tension) => collideRadii.set(tension.id, 55));
  model.voices.forEach((voice) => collideRadii.set(voice.id, 72));
  model.outcomes.forEach((outcome) => collideRadii.set(outcome.id, 58));

  const nodes: LayoutNode[] = ids.map((id, index) => {
    const angle = hashAngle(id);
    const radius = 160 + (index % 5) * 55;
    return { id, x: CX + radius * Math.cos(angle), y: CY + radius * Math.sin(angle) };
  });
  nodes[0].fx = CX;
  nodes[0].fy = CY;

  const known = new Set(ids);
  const links: LayoutLink[] = model.edges
    .filter((edge) => known.has(edge.source) && known.has(edge.target))
    .map((edge) => ({ source: edge.source, target: edge.target, edge }));

  const simulation = forceSimulation(nodes)
    .force('link', forceLink<LayoutNode, LayoutLink>(links)
      .id((node) => node.id)
      .distance((link) => linkDistance(link.edge))
      .strength((link) => linkStrength(link.edge)))
    .force('charge', forceManyBody<LayoutNode>().strength(-420))
    .force('collide', forceCollide<LayoutNode>().radius((node) => collideRadii.get(node.id) ?? 50).iterations(2))
    // 温和的径向带抑制离心漂移；整体居中由出图前的包围盒平移兜底，
    // 结盟/对立的聚散仍由 link 力主导。
    .force('radial', forceRadial<LayoutNode>(300, CX, CY).strength(0.08))
    .stop();

  for (let i = 0; i < TICKS; i += 1) simulation.tick();

  // 仿真中中心被钉住保证稳定；出图前把包围盒中点平移回画布中心，
  // 避免高度互联的声部团整体沉向一侧。
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    const x = node.fx ?? node.x ?? CX;
    const y = node.fy ?? node.y ?? CY;
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  const offsetX = CX - (minX + maxX) / 2;
  const offsetY = CY - (minY + maxY) / 2;

  return new Map(nodes.map((node) => [node.id, {
    x: clamp((node.fx ?? node.x ?? CX) + offsetX),
    y: clamp((node.fy ?? node.y ?? CY) + offsetY),
  }]));
}
