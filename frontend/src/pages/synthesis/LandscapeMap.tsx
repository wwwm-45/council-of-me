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
import type { LandscapeDisplayModel, NodeType, ReflectionViewMode, SelectedNode } from './types';

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

interface Positioned {
  x: number;
  y: number;
  angle: number;
}

const CX = 500;
const CY = 500;
const TENSION_RADIUS = 120;
const VOICE_RADIUS = 260;
const OUTCOME_RADIUS = 370;

function circleLayout(radius: number, count: number, offset = -Math.PI / 2): Positioned[] {
  if (count === 0) return [];
  return Array.from({ length: count }, (_, index) => {
    const angle = offset + (index / count) * Math.PI * 2;
    return {
      x: CX + radius * Math.cos(angle) * 1.1,
      y: CY + radius * Math.sin(angle) * 0.9,
      angle,
    };
  });
}

function drawCurve(x1: number, y1: number, x2: number, y2: number, bend = 0.2) {
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

  const tensions = useMemo(() => {
    const positions = circleLayout(TENSION_RADIUS, displayModel.tensions.length);
    return displayModel.tensions.map((tension, index) => ({ ...tension, ...positions[index] }));
  }, [displayModel.tensions]);

  const voices = useMemo(() => {
    const groups = new Map<number, Array<LandscapeDisplayModel['voices'][number] & { targetAngle: number }>>();
    displayModel.voices.forEach((voice, voiceIndex) => {
      const related = tensions.filter((tension) => tension.agentIds.includes(voice.id));
      const targetAngle = related.length > 0
        ? Math.atan2(
            related.reduce((sum, tension) => sum + Math.sin(tension.angle), 0),
            related.reduce((sum, tension) => sum + Math.cos(tension.angle), 0),
          )
        : -Math.PI / 2 + (voiceIndex / Math.max(displayModel.voices.length, 1)) * Math.PI * 2;
      const groupKey = Math.round(targetAngle * 10);
      const group = groups.get(groupKey) ?? [];
      group.push({ ...voice, targetAngle });
      groups.set(groupKey, group);
    });

    return Array.from(groups.values()).flatMap((group) => group.map((voice, index) => {
      const angle = voice.targetAngle + (index - (group.length - 1) / 2) * 0.35;
      return {
        ...voice,
        x: CX + VOICE_RADIUS * Math.cos(angle) * 1.15,
        y: CY + VOICE_RADIUS * Math.sin(angle) * 0.85,
        angle,
      };
    }));
  }, [displayModel.voices, tensions]);

  const outcomes = useMemo(() => {
    const groups = [
      displayModel.outcomes.filter((outcome) => outcome.type === 'consensus'),
      displayModel.outcomes.filter((outcome) => outcome.type === 'productive'),
      displayModel.outcomes.filter((outcome) => outcome.type === 'irreducible'),
    ];
    return groups.flatMap((group, groupIndex) => {
      const baseAngle = -Math.PI / 2 + groupIndex * (Math.PI * 2 / 3);
      return group.map((outcome, index) => {
        const angle = baseAngle + (index - (group.length - 1) / 2) * 0.25;
        return {
          ...outcome,
          x: CX + OUTCOME_RADIUS * Math.cos(angle) * 1.2,
          y: CY + OUTCOME_RADIUS * Math.sin(angle) * 0.8,
          angle,
        };
      });
    });
  }, [displayModel.outcomes]);

  const viewedSet = useMemo(() => new Set(viewedNodeIds), [viewedNodeIds]);
  const exploredSet = useMemo(() => new Set(exploredNodeIds), [exploredNodeIds]);
  const traceIndex = useMemo(
    () => new Map(traceOrder.map((nodeId, index) => [nodeId, index + 1])),
    [traceOrder],
  );

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

  return (
    <div className="relative flex h-full max-h-full w-full flex-col items-center justify-center overflow-hidden bg-transparent transition-all duration-1000">
      <svg viewBox="0 0 1000 1000" className="h-full max-h-full w-full max-w-full object-contain">
        <LandscapeDefs />

        <circle cx={CX} cy={CY} r={OUTCOME_RADIUS + 100} fill="url(#reflectionAura)" className="animate-pulse-slow" pointerEvents="none" />

        <g style={{ transformOrigin: `${CX}px ${CY}px` }} pointerEvents="none">
          {[0, 2.6, 5.3].map((delay) => (
            <circle
              key={delay}
              data-effect="ripple"
              cx={CX}
              cy={CY}
              r={200}
              stroke="rgba(99,102,241,0.2)"
              fill="none"
              className="animate-[ripple_8s_cubic-bezier(0,0.2,0.8,1)_infinite]"
              style={{
                transformOrigin: `${CX}px ${CY}px`,
                animation: 'ripple 8s cubic-bezier(0,0.2,0.8,1) infinite',
                animationDelay: `${delay}s`,
              }}
            />
          ))}
        </g>

        <g stroke="rgba(255,255,255,0.04)" fill="none" strokeWidth={1} pointerEvents="none">
          {[TENSION_RADIUS, VOICE_RADIUS, OUTCOME_RADIUS].map((radius) => (
            <circle key={radius} data-orbit-radius={radius} cx={CX} cy={CY} r={radius} />
          ))}
        </g>

        <g pointerEvents="none">
          {tensions.length > 1 ? tensions.map((tension, index) => {
            const next = tensions[(index + 1) % tensions.length];
            return (
              <path
                key={`tension-ring-${tension.id}`}
                data-landscape-edge="tension-ring"
                d={drawCurve(tension.x, tension.y, next.x, next.y, 0.1)}
                fill="none"
                stroke="rgba(165,180,252,0.2)"
                strokeWidth={1}
                strokeDasharray="4 4"
                className="animate-edgeDraw"
                style={{ animation: `edgeDraw 0.6s ease-out ${0.5 + index * 0.1}s both` }}
              />
            );
          }) : null}

          {tensions.map((tension, index) => {
            const path = drawCurve(CX, CY, tension.x, tension.y, 0.15);
            return (
              <g key={`center-tension-${tension.id}`}>
                <path
                  data-landscape-edge="center-tension"
                  d={path}
                  fill="none"
                  stroke="rgba(165,180,252,0.4)"
                  strokeWidth={1.5}
                  className="animate-edgeDraw"
                  style={{ animation: `edgeDraw 0.6s ease-out ${index * 0.05}s both` }}
                />
                <path
                  data-effect="flow"
                  d={path}
                  fill="none"
                  stroke="rgba(255,255,255,0.8)"
                  strokeWidth={2}
                  strokeDasharray="2 12"
                  style={{ animation: 'flow-outward 2s linear infinite', animationDelay: `${1 + index * 0.05}s` }}
                />
              </g>
            );
          })}

          {voices.length > 1 ? voices.map((voice, index) => {
            const next = voices[(index + 1) % voices.length];
            const distance = Math.hypot(voice.x - next.x, voice.y - next.y);
            if (distance > 300) return null;
            return (
              <path
                key={`voice-ring-${voice.id}`}
                data-landscape-edge="voice-ring"
                d={drawCurve(voice.x, voice.y, next.x, next.y, 0.08)}
                fill="none"
                stroke="rgba(252,211,77,0.15)"
                strokeWidth={1}
                strokeDasharray="2 4"
                className="animate-edgeDraw"
                style={{ animation: `edgeDraw 0.6s ease-out ${1 + index * 0.1}s both` }}
              />
            );
          }) : null}

          {tensions.flatMap((tension, tensionIndex) => voices
            .filter((voice) => tension.agentIds.includes(voice.id))
            .map((voice) => {
              const path = drawCurve(tension.x, tension.y, voice.x, voice.y, 0.1);
              return (
                <g key={`tension-voice-${tension.id}-${voice.id}`}>
                  <path
                    data-landscape-edge="tension-voice"
                    d={path}
                    fill="none"
                    stroke="hsla(40,60%,50%,0.3)"
                    strokeWidth={1.5}
                    className="animate-edgeDraw"
                    style={{ animation: `edgeDraw 0.6s ease-out ${0.3 + tensionIndex * 0.05}s both` }}
                  />
                  <path
                    data-effect="flow"
                    d={path}
                    fill="none"
                    stroke="rgba(252,211,77,0.8)"
                    strokeWidth={1.5}
                    strokeDasharray="2 15"
                    style={{ animation: 'flow-outward 3s linear infinite', animationDelay: `${1.5 + tensionIndex * 0.05}s` }}
                  />
                </g>
              );
            }))}

          {voices.length > 0 ? outcomes.map((outcome, index) => {
            const closestVoice = voices.reduce((closest, voice) => {
              const closestDistance = (closest.x - outcome.x) ** 2 + (closest.y - outcome.y) ** 2;
              const distance = (voice.x - outcome.x) ** 2 + (voice.y - outcome.y) ** 2;
              return distance < closestDistance ? voice : closest;
            }, voices[0]);
            return (
              <path
                key={`voice-outcome-${outcome.id}`}
                data-landscape-edge="voice-outcome"
                d={drawCurve(closestVoice.x, closestVoice.y, outcome.x, outcome.y, 0.1)}
                fill="none"
                stroke="rgba(148,163,184,0.3)"
                strokeWidth={1}
                strokeDasharray="4 6"
                className="animate-edgeDraw"
                style={{ animation: `edgeDraw 0.6s ease-out ${0.8 + index * 0.05}s both` }}
              />
            );
          }) : null}
        </g>

        <CenterNode
          x={CX}
          y={CY}
          dilemma={displayModel.center.label}
          insight={showInsight ? displayModel.center.insight : ''}
          delay={0}
          state={stateFor(displayModel.center.id)}
          onClick={() => selectNode(displayModel.center.id, 'center')}
        />

        {tensions.map((tension, index) => (
          <TensionNode
            key={tension.id}
            x={tension.x}
            y={tension.y}
            tension={tension}
            delay={0.2 + index * 0.1}
            state={stateFor(tension.id)}
            onClick={() => selectNode(tension.id, 'tension')}
          />
        ))}

        {voices.map((voice, index) => (
          <VoiceNode
            key={voice.id}
            x={voice.x}
            y={voice.y}
            voice={voice}
            delay={0.4 + index * 0.1}
            showLabel={showVoiceLabels}
            state={stateFor(voice.id)}
            onClick={() => selectNode(voice.id, 'voice')}
          />
        ))}

        {outcomes.map((outcome, index) => (
          <OuterNode
            key={outcome.id}
            id={outcome.id}
            x={outcome.x}
            y={outcome.y}
            type={outcome.type}
            label={outcome.label}
            delay={0.6 + index * 0.1}
            state={stateFor(outcome.id)}
            onClick={() => selectNode(outcome.id, outcome.type)}
          />
        ))}
      </svg>
      <MapLegend />
    </div>
  );
}
