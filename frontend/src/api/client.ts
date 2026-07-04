/**
 * Council of Me - Unified API Client
 * All backend API calls are organized here, grouped by phase.
 */

import type { Portrait, PortraitQuality } from '../pages/portrait/types';

export type { PortraitQuality, PortraitQualityIssue } from '../pages/portrait/types';

const BASE = '/api';

export interface DebateStatement {
  statement_id?: string;
  agent_id: string;
  agent_name: string;
  round_number: number;
  content: string;
  type?: string;
  is_user_turn?: boolean;
  queued_at?: string;
  inserted_after_statement_id?: string | null;
  is_intervention_response?: boolean;
  intervention_type?: string;
}

export interface LlmModelInfo {
  id: string;
  label: string;
  provider: string;
  family: string;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (res.status === 449) {
    const data = await res.json();
    throw { crisis: true, resources: data.detail?.resources, detail: data.detail };
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw { status: res.status, detail: data.detail ?? data };
  }
  return res.json();
}

export const getLlmModels = () =>
  request<{ current_model: string; wire_api: string; base_url: string | null; models: LlmModelInfo[] }>('/llm/models');

export const selectLlmModel = (model: string) =>
  request<{ ok: boolean; current_model: string }>('/llm/models/select', {
    method: 'POST',
    body: JSON.stringify({ model }),
  });

// ─── Phase 0: Session ──────────────────────────
export const createSession = (displayName?: string) =>
  request<{ session_id: string; status: string }>('/sessions', {
    method: 'POST',
    body: JSON.stringify(displayName ? { display_name: displayName } : {}),
  });

export const debugSkip = () =>
  request<{ session_id: string; status: string; framing_preference: string; conflict_profile: Record<string, unknown>; debate_level: string }>('/sessions/debug-skip', { method: 'POST', body: '{}' });

export const getSession = (id: string) =>
  request<Record<string, unknown>>(`/sessions/${id}`);

export const submitConsent = (id: string) =>
  request<{ ok: boolean }>(`/sessions/${id}/consent`, { method: 'POST', body: JSON.stringify({ accepted: true }) });

export const submitFraming = (id: string, framing: string) =>
  request<{ ok: boolean }>(`/sessions/${id}/framing`, { method: 'POST', body: JSON.stringify({ framing }) });

// ─── Phase 1: Elicitation ──────────────────────
export interface ElicitationApiResponse {
  response: string;
  should_continue: boolean;
  round: number;
  depth: {
    depth_score: number;
    depth_layer: number;
    current_layer?: number;
    recommended_action: string;
    strategy_hint: string;
    emotional_state?: string;
    graduation_ready?: boolean;
  } | null;
  conflict_profile_draft: Record<string, unknown> | null;
  elicitation_outcome: Record<string, unknown> | null;
  tension_cards?: Array<Record<string, unknown>> | null;
  focus_card_id?: string | null;
  safety_warning: string | null;
  portrait_quality?: PortraitQuality | null;
  requires_quality_confirmation?: boolean;
}

export const postElicitation = (id: string, message: string) =>
  request<ElicitationApiResponse>(`/sessions/${id}/elicitation`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });

export const editLastElicitationMessage = (id: string, message: string) =>
  request<ElicitationApiResponse>(`/sessions/${id}/elicitation/last-user-message`, {
    method: 'PUT',
    body: JSON.stringify({ message }),
  });

export interface ElicitationTurnStartEvent {
  round: number;
  current_layer: number;
  focus_tension?: Record<string, unknown> | null;
}

export interface ElicitationStreamEndEvent extends ElicitationApiResponse {
  raw_response?: string;
  correction_applied?: boolean;
  correction_count?: number;
}

export interface ElicitationStreamCorrectionEvent {
  reason?: string;
  discard_prior?: boolean;
}

export async function streamElicitation(
  sessionId: string,
  message: string,
  callbacks: {
    onTurnStart?: (event: ElicitationTurnStartEvent) => void;
    onToken?: (content: string) => void;
    onCorrection?: (event: ElicitationStreamCorrectionEvent) => void;
    onTurnEnd?: (event: ElicitationStreamEndEvent) => void;
    onError?: (message: string) => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${sessionId}/elicitation/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  });
  if (res.status === 449) {
    const data = await res.json();
    throw { crisis: true, resources: data.detail?.resources, detail: data.detail };
  }
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    const messageText = typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail.detail ?? detail);
    callbacks.onError?.(messageText);
    throw { status: res.status, detail: detail.detail ?? detail };
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  const handleBlock = (block: string) => {
    if (!block.trim()) return;
    let evtName = 'message';
    let dataStr = '';
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) evtName = line.slice(7).trim();
      if (line.startsWith('data: ')) dataStr = line.slice(6);
    }
    if (!dataStr) return;
    const data = JSON.parse(dataStr);
    switch (evtName) {
      case 'turn_start':
        callbacks.onTurnStart?.(data as ElicitationTurnStartEvent);
        break;
      case 'assistant_token':
        callbacks.onToken?.(String(data.content ?? ''));
        break;
      case 'assistant_correction':
        callbacks.onCorrection?.(data as ElicitationStreamCorrectionEvent);
        break;
      case 'turn_end':
        callbacks.onTurnEnd?.(data as ElicitationStreamEndEvent);
        break;
      case 'error':
        callbacks.onError?.(data.message ?? 'unknown error');
        throw new Error(data.message ?? 'unknown error');
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split('\n\n');
      buf = blocks.pop() ?? '';
      for (const block of blocks) {
        handleBlock(block);
      }
    }
    handleBlock(buf);
  } finally {
    reader.releaseLock();
  }
}

export const finishElicitation = (id: string, force = false) =>
  request<ElicitationApiResponse>(`/sessions/${id}/elicitation/finish`, {
    method: 'POST',
    body: JSON.stringify({ force }),
  });

// ─── Phase 2: Complexity ───────────────────────
export interface PortraitUpdatePayload {
  core_dilemma?: string;
  inner_voices?: Portrait['inner_voices'];
  debate_level?: string;
}

export const getPortrait = (id: string) =>
  request<Portrait>(`/sessions/${id}/portrait`);

export const updatePortrait = (id: string, payload: PortraitUpdatePayload) =>
  request<Portrait>(`/sessions/${id}/portrait`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });

export const confirmPortrait = (id: string, debateLevel?: string) =>
  request<{ ok: boolean; status: string }>(`/sessions/${id}/portrait/confirm`, {
    method: 'POST',
    body: JSON.stringify(debateLevel ? { debate_level: debateLevel } : {}),
  });

// ─── Phase 4: Debate ──────────────────────────

/**
 * Stream a single debate round via SSE.
 * Reads POST /sessions/{id}/debate/stream-round and fires callbacks for each event.
 */
export interface AgentOrderEntry {
  agentId: string;
  agentName: string;
}

interface AgentWireEntry {
  agent_id?: string;
  agent_name?: string;
}

export interface ConvergenceHighEvent {
  convergence_score: number;
  top_tensions?: Array<Record<string, unknown>>;
  divergence_map?: string;
  irreducible_acknowledgements?: Array<Record<string, unknown>>;
  estimated_remaining_minutes?: number;
  decision_required: boolean;
}

export interface RoundSkipEvent {
  phase: string;
  reason: string;
}

export type EarlyTerminationDecision = 'continue' | 'close';

export type DebatePhaseStatusType = 'phase_evaluating' | 'artifact_start' | 'artifact_end';

export interface DebatePhaseStatus {
  type: DebatePhaseStatusType;
  round: number;
  phase: string;
  stage?: string;
}

export interface FollowupQuestionDto {
  question_id: string;
  target_tension_id: string;
  kind: string;
  text: string;
}

export interface FollowupQuestionsEvent {
  followup_id: string;
  after_phase: string;
  round: number;
  lead_in: string;
  questions: FollowupQuestionDto[];
  timeout_seconds: number | null;
}

export interface FollowupResolvedEvent {
  followup_id: string;
  status: 'recorded' | 'skipped';
  applied_verdicts: Array<Record<string, unknown>>;
  answered_question_ids: string[];
}

export interface FollowupResponseItem {
  question_id: string;
  answer: string;
}

export async function streamDebateRound(
  sessionId: string,
  callbacks: {
    onRoundStart?: (round: number, phase: string, agentOrder: AgentOrderEntry[], expectedExchanges?: [number, number]) => void;
    onAgentStart?: (agentId: string, agentName: string, round: number, replyTo?: string, exchangeSeq?: number) => void;
    onAgentToken?: (agentId: string, content: string) => void;
    onAgentEnd?: (agentId: string, statementId: string) => void;
    onRoundEnd?: (round: number) => void;
    onExchangeMeta?: (exchangeSeq: number, totalMin: number, totalMax: number) => void;
    onRoundArtifact?: (artifactType: string, data: Record<string, unknown>) => void;
    onAgentEvolution?: (evolutions: AgentEvolutionData[]) => void;
    onR4SubPhase?: (subPhase: string, data: Record<string, unknown>) => void;
    onConvergenceHigh?: (payload: ConvergenceHighEvent) => void;
    onRoundSkip?: (payload: RoundSkipEvent) => void;
    onUserTurn?: (statement: DebateStatement) => void;
    onFollowupPreparing?: (round: number) => void;
    onFollowupQuestions?: (payload: FollowupQuestionsEvent) => void;
    onFollowupSkipped?: (round: number) => void;
    onFollowupResolved?: (payload: FollowupResolvedEvent) => void;
    onPhaseStatus?: (status: DebatePhaseStatus) => void;
    onDebateComplete?: (totalRounds: number) => void;
    onError?: (message: string) => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${sessionId}/debate/stream-round`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    callbacks.onError?.(typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail.detail));
    return;
  }
  if (!res.body) return;

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  const handleBlock = (block: string) => {
    if (!block.trim()) return;
    let evtName = 'message';
    let dataStr = '';
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) evtName = line.slice(7).trim();
      if (line.startsWith('data: ')) dataStr = line.slice(6);
    }
    if (!dataStr) return;
    try {
      const data = JSON.parse(dataStr);
      switch (evtName) {
        case 'round_start': {
          const rosterSource = Array.isArray(data.agent_roster)
            ? data.agent_roster
            : Array.isArray(data.agent_order)
              ? data.agent_order
              : [];
          const order: AgentOrderEntry[] = rosterSource.map((agent: unknown) => {
            const entry = agent as AgentWireEntry;
            return {
              agentId: entry.agent_id ?? '',
              agentName: entry.agent_name ?? '',
            };
          });
          const expected: [number, number] | undefined = data.expected_exchanges
            ? [data.expected_exchanges[0], data.expected_exchanges[1]]
            : undefined;
          callbacks.onRoundStart?.(data.round, data.phase ?? '', order, expected);
          break;
        }
        case 'agent_start': callbacks.onAgentStart?.(data.agent_id, data.agent_name, data.round, data.reply_to ?? undefined, data.exchange_seq); break;
        case 'agent_token': callbacks.onAgentToken?.(data.agent_id, data.content); break;
        case 'agent_end': callbacks.onAgentEnd?.(data.agent_id, data.statement_id ?? ''); break;
        case 'round_end': callbacks.onRoundEnd?.(data.round); break;
        case 'exchange_meta': callbacks.onExchangeMeta?.(data.exchange_seq, data.total_min, data.total_max); break;
        case 'round_artifact': callbacks.onRoundArtifact?.(data.type, data.data ?? data); break;
        case 'agent_evolution': callbacks.onAgentEvolution?.((data.agent_evolutions ?? []) as AgentEvolutionData[]); break;
        case 'user_turn':
          callbacks.onUserTurn?.(data as DebateStatement);
          break;
        case 'phase_evaluating':
        case 'artifact_start':
        case 'artifact_end':
          callbacks.onPhaseStatus?.({
            type: evtName as DebatePhaseStatusType,
            round: data.round,
            phase: data.phase,
            stage: data.stage,
          });
          break;
        case 'r4_reflection':
        case 'r4_mapping':
        case 'r4_final':
          callbacks.onR4SubPhase?.(evtName, data);
          break;
        case 'convergence_high':
          callbacks.onConvergenceHigh?.(data as ConvergenceHighEvent);
          break;
        case 'round_skip':
          callbacks.onRoundSkip?.(data as RoundSkipEvent);
          break;
        case 'followup_preparing':
          callbacks.onFollowupPreparing?.(data.round);
          break;
        case 'followup_questions':
          callbacks.onFollowupQuestions?.(data as FollowupQuestionsEvent);
          break;
        case 'followup_skipped':
          callbacks.onFollowupSkipped?.(data.round);
          break;
        case 'followup_resolved':
          callbacks.onFollowupResolved?.(data as FollowupResolvedEvent);
          break;
        case 'debate_complete': callbacks.onDebateComplete?.(data.total_rounds ?? 0); break;
        case 'error': callbacks.onError?.(data.message ?? 'unknown error'); break;
      }
    } catch {
      // ignore JSON parse errors
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split('\n\n');
      buf = blocks.pop() ?? '';
      for (const block of blocks) {
        handleBlock(block);
      }
    }
    handleBlock(buf);
  } finally {
    reader.releaseLock();
  }
}

export const debugSkipDebate = (id: string) =>
  request<{
    ok: boolean;
    status: string;
    source_session_id: string;
    statement_count: number;
    has_synthesis: boolean;
  }>(`/sessions/${id}/debate/debug-skip`, { method: 'POST', body: '{}' });

// ─── Phase 4.5: Interventions ──────────────────
export const debatePause = (id: string) =>
  request<{ ok: boolean }>(`/sessions/${id}/debate/pause`, { method: 'POST', body: '{}' });

export const debateResume = (id: string) =>
  request<{ ok: boolean }>(`/sessions/${id}/debate/resume`, { method: 'POST' });

export const debateEarlyTerminationDecision = (id: string, decision: EarlyTerminationDecision) =>
  request<{ ok: boolean; decision: EarlyTerminationDecision }>(`/sessions/${id}/debate/early-termination-decision`, {
    method: 'POST',
    body: JSON.stringify({ decision }),
  });

export const debateFollowupResponse = (id: string, followupId: string, responses: FollowupResponseItem[]) =>
  request<{ ok: boolean; status: 'recorded' | 'skipped'; accepted: number }>(`/sessions/${id}/debate/followup-response`, {
    method: 'POST',
    body: JSON.stringify({ followup_id: followupId, responses }),
  });

// ─── Phase 5: Synthesis ────────────────────────
export interface VoicePosition {
  agent_id: string;
  agent_name: string;
  core_stance: string;
}

export interface TensionEvidence {
  statement_id: string;
  agent_name: string;
  round_number: number;
  content: string;
  relevance_score: number;
}

export interface TensionPole {
  label: string;
  agents: string[];
  stance: string;
  evidence_statements?: TensionEvidence[];
}

export interface CoreTension {
  tension_id: string;
  name: string;
  pole_a: TensionPole;
  pole_b: TensionPole;
  intensity: number;
  value_conflict?: { value_a: string; value_b: string };
}

export interface ConsensusArea {
  area_id: string;
  description: string;
  supporting_agents: string[];
  evidence: string[];
}

export interface ProtectiveIntent {
  agent_id: string;
  agent_name: string;
  intent: string;
  what_it_protects: string;
  underlying_value: string;
}

export interface AgentEvolutionData {
  agent_id: string;
  agent_name?: string;
  r1_position: string;
  current_position: string;
  shift_type: 'none' | 'expansion' | 'revision' | 'reversal';
  shift_trigger: string | null;
  emotional_state: string;
}

export interface ProductiveTensionData {
  description: string;
  understanding: string;
}

export interface IrreducibleDifferenceData {
  description: string;
  why_irreducible: string;
}

export interface ConcessionData {
  agent_id: string;
  concession: string;
}

export interface UserVerdict {
  tension_id: string;
  tension_name: string;
  status: string; // confirmed | denied | refined
  text: string;
}

export interface SignificantTurnData {
  statement_id: string;
  label: string;
  agent_name: string;
}

export interface RoundProgression {
  round: number;
  agent_count: number;
  statement_count: number;
  agents: string[];
}

export interface SynthesisMeta {
  convergence_score: number;
  novelty_score: number;
  value_conflict_intensity: number;
  debate_rounds: number;
  termination_mode: string;
  round_progression?: RoundProgression[];
}

export interface SynthesisResponse {
  synthesis_type: 'CONSENSUS_MAP' | 'POLYPHONIC_LANDSCAPE' | 'NONE';
  narrative: string;
  voice_positions: VoicePosition[];
  core_tensions: CoreTension[];
  consensus_areas: ConsensusArea[];
  protective_intents: ProtectiveIntent[];
  // Landscape map fields
  agent_evolutions: AgentEvolutionData[];
  key_insight: string;
  productive_tensions: ProductiveTensionData[];
  irreducible_differences: IrreducibleDifferenceData[];
  highlight_moments: string[];
  concessions: ConcessionData[];
  dilemma_text: string;
  significant_turns?: SignificantTurnData[];
  divergence_map?: string;
  user_verdicts?: UserVerdict[];
  agent_voice_similarity_matrix?: Record<string, Record<string, number>>;
  meta: SynthesisMeta;
}

export const getSynthesis = (id: string) =>
  request<SynthesisResponse>(`/sessions/${id}/synthesis`);

export interface SynthesisStageInfo {
  stage: string;
  label: string;
  index: number;
  total: number;
}

/**
 * Stream synthesis generation via SSE with stage-by-stage progress.
 * Falls back to REST getSynthesis if SSE fails.
 */
export async function streamSynthesis(
  sessionId: string,
  callbacks: {
    onStageStart?: (info: SynthesisStageInfo) => void;
    onStageEnd?: (stage: string) => void;
    onComplete?: (data: SynthesisResponse) => void;
    onCached?: (data: SynthesisResponse) => void;
    onError?: (message: string) => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${sessionId}/synthesis/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
  if (!res.ok || !res.body) {
    callbacks.onError?.(`HTTP ${res.status}`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split('\n\n');
      buf = blocks.pop() ?? '';
      for (const block of blocks) {
        if (!block.trim()) continue;
        let evtName = 'message';
        let dataStr = '';
        for (const line of block.split('\n')) {
          if (line.startsWith('event: ')) evtName = line.slice(7).trim();
          if (line.startsWith('data: ')) dataStr = line.slice(6);
        }
        if (!dataStr) continue;
        try {
          const data = JSON.parse(dataStr);
          switch (evtName) {
            case 'synthesis_stage_start':
              callbacks.onStageStart?.(data as SynthesisStageInfo);
              break;
            case 'synthesis_stage_end':
              callbacks.onStageEnd?.(data.stage);
              break;
            case 'synthesis_complete':
              callbacks.onComplete?.(data as SynthesisResponse);
              break;
            case 'synthesis_cached':
              callbacks.onCached?.(data as SynthesisResponse);
              break;
            case 'error':
              callbacks.onError?.(data.message ?? 'unknown error');
              break;
          }
        } catch { /* ignore JSON parse errors */ }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ─── Phase 6: Reflection ───────────────────────

export type ReflectionViewMode = 'landscape' | 'explore' | 'dialogue' | 'trace';
export type FeelingId = 'resonance' | 'unease' | 'surprise' | 'seen' | 'push_back' | 'wordless';
export type ReflectionPathId = 'emotional' | 'assumption' | 'protective' | 'action';

export interface ReflectionNodeCatalogItem {
  node_id: string;
  node_type: string;
  node_label: string;
  recommendation_reason?: string | null;
}

export interface ReflectionDialogueTurn {
  turn_id: string;
  role: 'user' | 'assistant';
  content: string;
  layer: number;
  path: ReflectionPathId | '';
  created_at: string;
  client_turn_id: string;
}

export interface ReflectionNodeExploration {
  exploration_id: string;
  node_id: string;
  node_type: string;
  node_label: string;
  feelings: FeelingId[];
  selected_path: ReflectionPathId | '';
  current_layer: number;
  max_layer: number;
  explicit_insight: string;
  status: string;
  dialogue: ReflectionDialogueTurn[];
}

export interface ReflectionServerState {
  session_id: string;
  phase: Exclude<ReflectionViewMode, 'landscape'>;
  started_at: string;
  updated_at: string;
  nodes_viewed: string[];
  explorations: ReflectionNodeExploration[];
  current_exploration_id: string;
  current_node_id: string;
  dialogue: ReflectionDialogueTurn[];
  // Compatibility fields for progressive migration.
  exploration_order?: string[];
  current_path?: ReflectionPathId | null;
  current_layer?: number;
  completed_at?: string | null;
}

export interface ReflectionDialoguePayload {
  exploration_id: string;
  assistant_turn: ReflectionDialogueTurn;
  current_layer: number;
  selected_path: ReflectionPathId;
  exploration_status: 'in_progress' | 'ready_for_trace';
  recommended_next_actions: string[];
  layer_advanced?: boolean;
}

export interface ReflectionTraceInsight {
  exploration_id: string;
  node_id: string;
  node_type: string;
  node_label: string;
  path: ReflectionPathId;
  max_layer: number;
  r_level: string;
  insight: string;
  source: 'explicit' | 'derived';
  feelings: FeelingId[];
}

export interface ReflectionTraceResponse {
  session_id: string;
  phase: 'trace';
  exploration_order: string[];
  nodes_viewed: string[];
  insights: ReflectionTraceInsight[];
  footprint_sentence: string;
  closure_seed: {
    explored_nodes_count: number;
    deepest_path: ReflectionPathId;
    dominant_feelings: FeelingId[];
    insights: string[];
    gentle_commitment: string;
  };
}

export const startReflection = (id: string) =>
  request<{ state: ReflectionServerState; node_catalog: ReflectionNodeCatalogItem[] }>(`/sessions/${id}/reflection/start`, {
    method: 'POST',
  });

export const markReflectionFeeling = (
  id: string,
  body: { node_id: string; node_type: string; node_label: string; feelings: FeelingId[] },
) =>
  request<{ state: ReflectionServerState }>(`/sessions/${id}/reflection/feeling`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const markReflectionViewed = (
  id: string,
  body: { node_id: string; node_type: string; node_label: string },
) =>
  request<{ state: ReflectionServerState }>(`/sessions/${id}/reflection/viewed`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const startReflectionDialogue = (
  id: string,
  body: { node_id: string; node_type: string; node_label: string; path: ReflectionPathId; feelings?: FeelingId[] },
) =>
  request<ReflectionDialoguePayload>(`/sessions/${id}/reflection/dialogue/start`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const respondReflectionDialogue = (
  id: string,
  body: { exploration_id: string; content: string; client_turn_id: string },
) =>
  request<ReflectionDialoguePayload>(`/sessions/${id}/reflection/dialogue/respond`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const saveReflectionInsight = (
  id: string,
  body: { node_id: string; insight: string; node_type?: string; node_label?: string },
) =>
  request<{ state: ReflectionServerState }>(`/sessions/${id}/reflection/insight`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const getReflectionTrace = (id: string) =>
  request<ReflectionTraceResponse>(`/sessions/${id}/reflection/trace`);

export const completeReflection = (id: string) =>
  request<{ ok: boolean; status: string }>(`/sessions/${id}/reflection/complete`, { method: 'POST' });

// ─── Phase 8: Closure ──────────────────────────
export const closeSession = (id: string) =>
  request<{ ok: boolean; status: string; archive: string }>(`/sessions/${id}/close`, { method: 'POST' });

/**
 * Download the session's HTML report. Fetches the backend-generated single-file
 * HTML and triggers a browser download. Throws on non-2xx so callers can show an
 * error state.
 */
export async function downloadReport(id: string): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${id}/report`);
  if (!res.ok) {
    throw { status: res.status };
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const date = new Date().toISOString().slice(0, 10);
  a.download = `内心议会-对话回响-${date}.html`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function previewReport(id: string): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${id}/report`);
  if (!res.ok) {
    throw { status: res.status };
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  window.open(url, '_blank', 'noopener');
  // revoke after the new tab has had time to load the document
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// ─── History ──────────────────────────────────
export interface SynthesisCard {
  synthesis_id: string;
  session_id: string;
  created_at: string;
  synthesis_type: string;
  core_dilemma: string;
  convergence_score: number | null;
  tension_count: number;
  debate_rounds: number | null;
}

export interface ComparisonResult {
  synthesis_a: { id: string; dilemma: string; type: string };
  synthesis_b: { id: string; dilemma: string; type: string };
  shared_tension_themes: { tension_a: string; tension_b: string }[];
  shared_value_conflicts: string[];
  convergence_delta: number;
}

export interface PatternResult {
  pattern_type: string;
  description: string;
  occurrence_count: number;
  session_ids: string[];
}

export const listSyntheses = (userId: string, limit = 20, offset = 0) =>
  request<SynthesisCard[]>(`/users/${userId}/history/syntheses?limit=${limit}&offset=${offset}`);

export const getSynthesisDetail = (userId: string, synthesisId: string) =>
  request<SynthesisResponse>(`/users/${userId}/history/syntheses/${synthesisId}`);

export const compareSyntheses = (userId: string, idA: string, idB: string) =>
  request<ComparisonResult>(`/users/${userId}/history/compare`, {
    method: 'POST',
    body: JSON.stringify({ id_a: idA, id_b: idB }),
  });

export const detectPatterns = (userId: string) =>
  request<PatternResult[]>(`/users/${userId}/history/patterns`);
