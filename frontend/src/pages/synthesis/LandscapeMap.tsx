import { useMemo } from 'react';
import type { SynthesisResponse } from '../../api/client';
import {
  CenterNode,
  LandscapeDefs,
  MapLegend,
  OuterNode,
  TensionNode,
  VoiceNode,
  type LandscapeNodeState,
} from './LandscapeNodes';
import { buildLandscapeModel } from './landscapeModel';
import { computeGraphLayout, type LayoutPoint } from './forceLayout';
import type { GraphEdge, LandscapeDisplayModel, NodeType, ReflectionViewMode, SelectedNode } from './types';

interface CommonProps {
  selectedNodeId: string | null;
  onNodeSelect: (node: SelectedNode | null) => void;
  viewedNodeIds?: string[];
  exploredNodeIds?: string[];
  traceOrder?: string[];
  showInsight?: boolean;
  showVoiceLabels?: boolean;
  mode?: ReflectionViewMode;
  onNodeFocus?: (nodeId: string) => void;
}

type Props = CommonProps & (
  | { model: LandscapeDisplayModel; data?: never }
  /** Compatibility for call sites that have not yet adopted Task 1's display model. */
  | { model?: never; data: SynthesisResponse }
);

const CX = 500;
const CY = 500;
const CENTER_RADIUS = 80;
const TENSION_RADIUS = 48;
const VOICE_BASE_RADIUS = 42;

const EDGE_STYLES: Record<GraphEdge['type'], { stroke: string; dash?: string }> = {
  affinity: { stroke: 'rgba(129,140,248,0.55)' },
  opposition: { stroke: 'rgba(251,191,36,0.5)', dash: '6 5' },
  support: { stroke: 'rgba(52,211,153,0.45)', dash: '2 5' },
  outcome: { stroke: 'rgba(148,163,184,0.25)', dash: '4 6' },
};

function edgeWidth(edge: GraphEdge): number {
  if (edge.type === 'affinity') return 1 + edge.weight * 2.5;
  return edge.type === 'outcome' ? 1 : 1.5;
}

function drawCurve(x1: number, y1: number, x2: number, y2: number, bend = 0.12) {
  const midpointX = (x1 + x2) / 2;
  const midpointY = (y1 + y2) / 2;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const distance = Math.sqrt(dx * dx + dy * dy);
  if (distance === 0) return `M ${x1} ${y1} L ${x2} ${y2}`;
  const controlX = midpointX + (-dy / distance) * distance * bend;
  const controlY = midpointY + (dx / distance) * distance * bend;
  return `M ${x1} ${y1} Q ${controlX} ${controlY} ${x2} ${y2}`;
}

function voiceRadius(model: LandscapeDisplayModel, voiceId: string): number {
  const tensionCount = model.tensions
    .filter((tension) => tension.poleAAgents.includes(voiceId) || tension.poleBAgents.includes(voiceId))
    .length;
  const supportCount = model.outcomes
    .filter((outcome) => outcome.supportingAgents.includes(voiceId))
    .length;
  return VOICE_BASE_RADIUS + Math.min(tensionCount + supportCount, 3) * 6;
}

export default function LandscapeMap({
  model,
  data,
  selectedNodeId,
  onNodeSelect,
  viewedNodeIds = [],
  exploredNodeIds = [],
  traceOrder = [],
  showInsight = true,
  showVoiceLabels = true,
  onNodeFocus,
}: Props) {
  const displayModel = useMemo(() => {
    if (model) return model;
    if (data) return buildLandscapeModel(data);
    throw new Error('LandscapeMap requires a LandscapeDisplayModel');
  }, [data, model]);

  const layout = useMemo(() => computeGraphLayout(displayModel), [displayModel]);

  const viewedSet = useMemo(() => new Set(viewedNodeIds), [viewedNodeIds]);
  const exploredSet = useMemo(() => new Set(exploredNodeIds), [exploredNodeIds]);
  const traceIndex = useMemo(
    () => new Map(traceOrder.map((nodeId, index) => [nodeId, index + 1])),
    [traceOrder],
  );

  function pointFor(id: string): LayoutPoint {
    return layout.get(id) ?? { x: CX, y: CY };
  }

  function stateFor(nodeId: string): LandscapeNodeState {
    return {
      selected: selectedNodeId === nodeId,
      viewed: viewedSet.has(nodeId),
      explored: exploredSet.has(nodeId),
      traceIndex: traceIndex.get(nodeId) ?? null,
    };
  }

  function selectNode(id: string, type: NodeType) {
    if (onNodeFocus) {
      onNodeFocus(id);
      return;
    }
    onNodeSelect(selectedNodeId === id ? null : { id, type });
  }

  const centerPoint = pointFor(displayModel.center.id);

  return (
    <div className="relative flex h-full max-h-full w-full flex-col items-center justify-center overflow-hidden bg-transparent transition-all duration-1000">
      <svg viewBox="0 0 1000 1000" className="h-full max-h-full w-full max-w-full object-contain">
        <LandscapeDefs />

        <circle cx={CX} cy={CY} r={470} fill="url(#reflectionAura)" className="animate-pulse-slow" pointerEvents="none" />

        <g style={{ transformOrigin: `${centerPoint.x}px ${centerPoint.y}px` }} pointerEvents="none">
          {[0, 2.6, 5.3].map((delay) => (
            <circle
              key={delay}
              data-effect="ripple"
              cx={centerPoint.x}
              cy={centerPoint.y}
              r={200}
              stroke="rgba(99,102,241,0.2)"
              fill="none"
              className="animate-[ripple_8s_cubic-bezier(0,0.2,0.8,1)_infinite]"
              style={{
                transformOrigin: `${centerPoint.x}px ${centerPoint.y}px`,
                animation: 'ripple 8s cubic-bezier(0,0.2,0.8,1) infinite',
                animationDelay: `${delay}s`,
              }}
            />
          ))}
        </g>

        <g pointerEvents="none">
          {displayModel.edges.map((edge, index) => {
            const from = pointFor(edge.source);
            const to = pointFor(edge.target);
            const path = drawCurve(from.x, from.y, to.x, to.y);
            const style = EDGE_STYLES[edge.type];
            return (
              <g key={edge.id}>
                <path
                  data-landscape-edge={edge.type}
                  d={path}
                  fill="none"
                  stroke={style.stroke}
                  strokeWidth={edgeWidth(edge)}
                  strokeDasharray={style.dash}
                  className="animate-edgeDraw"
                  style={{ animation: `edgeDraw 0.6s ease-out ${0.3 + index * 0.04}s both` }}
                />
                {edge.type === 'opposition' ? (
                  <path
                    data-effect="flow"
                    d={path}
                    fill="none"
                    stroke="rgba(251,191,36,0.8)"
                    strokeWidth={1.5}
                    strokeDasharray="2 14"
                    style={{ animation: 'flow-outward 3s linear infinite', animationDelay: `${1 + index * 0.05}s` }}
                  />
                ) : null}
              </g>
            );
          })}
        </g>

        <CenterNode
          x={centerPoint.x}
          y={centerPoint.y}
          radius={CENTER_RADIUS}
          dilemma={displayModel.center.label}
          insight={showInsight ? displayModel.center.insight : ''}
          delay={0}
          state={stateFor(displayModel.center.id)}
          onClick={() => selectNode(displayModel.center.id, 'center')}
        />

        {displayModel.tensions.map((tension, index) => {
          const point = pointFor(tension.id);
          return (
            <TensionNode
              key={tension.id}
              x={point.x}
              y={point.y}
              radius={TENSION_RADIUS}
              tension={tension}
              delay={0.2 + index * 0.1}
              state={stateFor(tension.id)}
              onClick={() => selectNode(tension.id, 'tension')}
            />
          );
        })}

        {displayModel.voices.map((voice, index) => {
          const point = pointFor(voice.id);
          return (
            <VoiceNode
              key={voice.id}
              x={point.x}
              y={point.y}
              radius={voiceRadius(displayModel, voice.id)}
              voice={voice}
              delay={0.4 + index * 0.1}
              showLabel={showVoiceLabels}
              state={stateFor(voice.id)}
              onClick={() => selectNode(voice.id, 'voice')}
            />
          );
        })}

        {displayModel.outcomes.map((outcome, index) => {
          const point = pointFor(outcome.id);
          return (
            <OuterNode
              key={outcome.id}
              id={outcome.id}
              x={point.x}
              y={point.y}
              type={outcome.type}
              label={outcome.label}
              delay={0.6 + index * 0.1}
              state={stateFor(outcome.id)}
              onClick={() => selectNode(outcome.id, outcome.type)}
            />
          );
        })}
      </svg>
      <MapLegend />
    </div>
  );
}
