import type { SynthesisResponse } from '../../api/client';
import { AGENT_COLORS, SHIFT_ICONS, type LandscapeDisplayModel } from './types';

const FALLBACK_AGENT_COLOR = '#94a3b8';

function hasValidId(id: string | null | undefined): id is string {
  return typeof id === 'string' && id.trim().length > 0;
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

  return {
    center: {
      id: 'center',
      label: data.dilemma_text || '',
      insight: data.key_insight || '',
    },
    narrative: data.narrative || '',
    tensions: (data.core_tensions ?? [])
      .filter((tension) => hasValidId(tension.tension_id))
      .map((tension) => ({
        id: tension.tension_id,
        label: tension.name || '',
        intensity: tension.intensity,
        agentIds: [
          ...new Set([
            ...(tension.pole_a.agents ?? []),
            ...(tension.pole_b.agents ?? []),
          ].filter(hasValidId)),
        ],
        poleA: tension.pole_a.label || '',
        poleB: tension.pole_b.label || '',
      })),
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
    outcomes: [
      ...(data.consensus_areas ?? []).map((item, index) => ({
        id: `consensus-${index}`,
        type: 'consensus' as const,
        label: item.description || '',
      })),
      ...(data.productive_tensions ?? []).map((item, index) => ({
        id: `productive-${index}`,
        type: 'productive' as const,
        label: item.description || '',
      })),
      ...(data.irreducible_differences ?? []).map((item, index) => ({
        id: `irreducible-${index}`,
        type: 'irreducible' as const,
        label: item.description || '',
      })),
    ],
  };
}
