import type { SynthesisResponse } from '../../api/client';
import {
  AGENT_COLORS,
  SHIFT_ICONS,
  type GraphEdge,
  type LandscapeDisplayModel,
} from './types';

const FALLBACK_AGENT_COLOR = '#94a3b8';
const AFFINITY_THRESHOLD = 0.45;

type TensionModel = LandscapeDisplayModel['tensions'][number];
type OutcomeModel = LandscapeDisplayModel['outcomes'][number];

function hasValidId(id: string | null | undefined): id is string {
  return typeof id === 'string' && id.trim().length > 0;
}

function pairKey(a: string, b: string): string {
  return a < b ? `${a}~${b}` : `${b}~${a}`;
}

function matrixAffinities(
  matrix: Record<string, Record<string, number>> | undefined,
  voiceIds: string[],
): Map<string, number> {
  const scores = new Map<string, number>();
  if (!matrix) return scores;
  for (const a of voiceIds) {
    for (const b of voiceIds) {
      if (a >= b) continue;
      const raw = Math.max(matrix[a]?.[b] ?? 0, matrix[b]?.[a] ?? 0);
      if (raw > 0) scores.set(pairKey(a, b), Math.min(raw, 1));
    }
  }
  return scores;
}

function jaccardAffinities(
  voiceIds: string[],
  tensions: TensionModel[],
  outcomes: OutcomeModel[],
): Map<string, number> {
  const signatures = new Map<string, Set<string>>(
    voiceIds.map((id) => [id, new Set<string>()]),
  );
  for (const tension of tensions) {
    tension.poleAAgents.forEach((id) => signatures.get(id)?.add(`pole:${tension.id}:a`));
    tension.poleBAgents.forEach((id) => signatures.get(id)?.add(`pole:${tension.id}:b`));
  }
  for (const outcome of outcomes) {
    outcome.supportingAgents.forEach((id) => signatures.get(id)?.add(`support:${outcome.id}`));
  }
  const scores = new Map<string, number>();
  for (const a of voiceIds) {
    for (const b of voiceIds) {
      if (a >= b) continue;
      const setA = signatures.get(a);
      const setB = signatures.get(b);
      if (!setA?.size || !setB?.size) continue;
      const shared = [...setA].filter((item) => setB.has(item)).length;
      if (shared === 0) continue;
      const union = new Set([...setA, ...setB]).size;
      scores.set(pairKey(a, b), shared / union);
    }
  }
  return scores;
}

function buildEdges(
  data: SynthesisResponse,
  voiceIds: string[],
  tensions: TensionModel[],
  outcomes: OutcomeModel[],
): GraphEdge[] {
  const voiceSet = new Set(voiceIds);
  const edges: GraphEdge[] = [];

  const matrixScores = matrixAffinities(data.agent_voice_similarity_matrix, voiceIds);
  const scores = matrixScores.size > 0
    ? matrixScores
    : jaccardAffinities(voiceIds, tensions, outcomes);
  for (const [key, weight] of scores) {
    if (weight < AFFINITY_THRESHOLD) continue;
    const [source, target] = key.split('~');
    edges.push({ id: `affinity-${key}`, type: 'affinity', source, target, weight });
  }

  for (const tension of tensions) {
    for (const [side, agents] of [['a', tension.poleAAgents], ['b', tension.poleBAgents]] as const) {
      for (const agentId of agents) {
        if (!voiceSet.has(agentId)) continue;
        edges.push({
          id: `opposition-${tension.id}-${side}-${agentId}`,
          type: 'opposition',
          source: agentId,
          target: tension.id,
          weight: 1,
          tensionId: tension.id,
          side,
        });
      }
    }
  }

  for (const outcome of outcomes) {
    const supporters = outcome.supportingAgents.filter((id) => voiceSet.has(id));
    for (const agentId of supporters) {
      edges.push({
        id: `support-${outcome.id}-${agentId}`,
        type: 'support',
        source: agentId,
        target: outcome.id,
        weight: 1,
      });
    }
    if (outcome.type !== 'consensus' || supporters.length === 0) {
      edges.push({ id: `outcome-${outcome.id}`, type: 'outcome', source: outcome.id, target: 'center', weight: 1 });
    }
  }

  return edges;
}

export function buildLandscapeModel(data: SynthesisResponse): LandscapeDisplayModel {
  const voicePositions = (data.voice_positions ?? [])
    .filter((voice) => hasValidId(voice.agent_id));
  const evolutionRecords = (data.agent_evolutions ?? [])
    .filter((evolution) => hasValidId(evolution.agent_id));
  const positions = new Map(voicePositions.map((voice) => [voice.agent_id, voice]));
  const evolutions = new Map(
    evolutionRecords.map((evolution) => [evolution.agent_id, evolution]),
  );
  const voiceIds = [...new Set([
    ...voicePositions.map((voice) => voice.agent_id),
    ...evolutionRecords.map((evolution) => evolution.agent_id),
  ])];

  const tensions: TensionModel[] = (data.core_tensions ?? [])
    .filter((tension) => hasValidId(tension.tension_id))
    .map((tension) => ({
      id: tension.tension_id,
      label: tension.name || '',
      intensity: tension.intensity,
      poleAAgents: (tension.pole_a.agents ?? []).filter(hasValidId),
      poleBAgents: (tension.pole_b.agents ?? []).filter(hasValidId),
      poleA: tension.pole_a.label || '',
      poleB: tension.pole_b.label || '',
    }));

  const outcomes: OutcomeModel[] = [
    ...(data.consensus_areas ?? []).map((item, index) => ({
      id: `consensus-${index}`,
      type: 'consensus' as const,
      label: item.description || '',
      supportingAgents: (item.supporting_agents ?? []).filter(hasValidId),
    })),
    ...(data.productive_tensions ?? []).map((item, index) => ({
      id: `productive-${index}`,
      type: 'productive' as const,
      label: item.description || '',
      supportingAgents: [] as string[],
    })),
    ...(data.irreducible_differences ?? []).map((item, index) => ({
      id: `irreducible-${index}`,
      type: 'irreducible' as const,
      label: item.description || '',
      supportingAgents: [] as string[],
    })),
  ];

  return {
    center: {
      id: 'center',
      label: data.dilemma_text || '',
      insight: data.key_insight || '',
    },
    narrative: data.narrative || '',
    tensions,
    voices: voiceIds.map((agentId) => {
      const voice = positions.get(agentId);
      const evolution = evolutions.get(agentId);

      return {
        id: agentId,
        label: voice?.agent_name || evolution?.agent_name || agentId,
        stance: voice?.core_stance || evolution?.current_position || '',
        color: AGENT_COLORS[agentId] ?? FALLBACK_AGENT_COLOR,
        shiftIcon: SHIFT_ICONS[evolution?.shift_type ?? 'none'] ?? SHIFT_ICONS.none,
      };
    }),
    outcomes,
    edges: buildEdges(data, voiceIds, tensions, outcomes),
  };
}
