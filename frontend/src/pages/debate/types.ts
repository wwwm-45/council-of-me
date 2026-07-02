export interface AgentVisual {
  emoji: string;
  bg: string;
  text: string;
  bubble: string;
  bubblePointer: string;
}

/** Keys are snake_case to match backend identity_card.py agent_ids. */
export const AGENT_VISUAL: Record<string, AgentVisual> = {
  empathic_listener: {
    emoji: '🥰',
    bg: 'bg-rose-200',
    text: 'text-rose-700',
    bubble: 'bg-rose-50 border-rose-200',
    bubblePointer: 'bg-rose-50 border-rose-200',
  },
  rational_analyst: {
    emoji: '🧠',
    bg: 'bg-sky-200',
    text: 'text-sky-700',
    bubble: 'bg-sky-50 border-sky-200',
    bubblePointer: 'bg-sky-50 border-sky-200',
  },
  critical_examiner: {
    emoji: '⚖️',
    bg: 'bg-amber-200',
    text: 'text-amber-700',
    bubble: 'bg-amber-50 border-amber-200',
    bubblePointer: 'bg-amber-50 border-amber-200',
  },
  creative_explorer: {
    emoji: '🧭',
    bg: 'bg-violet-200',
    text: 'text-violet-700',
    bubble: 'bg-violet-50 border-violet-200',
    bubblePointer: 'bg-violet-50 border-violet-200',
  },
  synthesizer: {
    emoji: '🪢',
    bg: 'bg-teal-200',
    text: 'text-teal-700',
    bubble: 'bg-teal-50 border-teal-200',
    bubblePointer: 'bg-teal-50 border-teal-200',
  },
};

const DEFAULT_VISUAL: AgentVisual = {
  emoji: '💬',
  bg: 'bg-slate-200',
  text: 'text-slate-600',
  bubble: 'bg-slate-50 border-slate-200',
  bubblePointer: 'bg-slate-50 border-slate-200',
};

export function getVisual(agentId: string): AgentVisual {
  return AGENT_VISUAL[agentId] ?? DEFAULT_VISUAL;
}

export interface ChatMsg {
  id: string;
  agentId: string;
  agentName: string;
  round: number;
  content: string;
  streaming: boolean;
  replyTo?: string;
  isIntervention?: boolean;
  interventionType?: string;
  isUserTurn?: boolean;
  speakerType?: 'agent' | 'user';
  /** True for r4_final speeches — rendered under the standalone 终章 closing act. */
  closingAct?: boolean;
}

export type AgentShiftType = 'none' | 'expansion' | 'revision' | 'reversal';

export interface AgentEvolutionDto {
  agent_id: string;
  r1_position: string;
  current_position: string;
  shift_type: AgentShiftType;
  shift_trigger: string | null;
  emotional_state: string;
}

export const SHIFT_LABEL: Record<AgentShiftType, string> = {
  none: '',
  expansion: '扩展',
  revision: '修正',
  reversal: '反转',
};

export const SHIFT_BADGE_CLASS: Record<AgentShiftType, string> = {
  none: '',
  expansion: 'bg-sky-100 text-sky-700',
  revision: 'bg-amber-100 text-amber-700',
  reversal: 'bg-rose-100 text-rose-700',
};

export type TimelineItem =
  | { kind: 'divider'; round: number; phase: string; exchangeInfo?: string }
  | { kind: 'msg'; msg: ChatMsg };

export const PHASE_LABEL: Record<string, string> = {
  round1_opening: '开场陈述',
  round2_cross: '正面交锋',
  round3_deepen: '高压校准',
  round4_converge: '整合收束',
  r4_reflection: '反思',
  r4_mapping: '共识映射',
  r4_final: '最终定位',
};

export interface RoundSummary {
  current_dispute: string;
  key_change: string;
  unresolved_issue: string;
  language: 'zh-CN';
}

export interface SignificantTurn {
  statement_id: string;
  label: string;
  agent_name: string;
}

export interface ArtifactEvent {
  type: 'position_map' | 'tension_map' | 'engagement_record' | 'convergence_map';
  data: Record<string, unknown> & {
    summary?: RoundSummary;
    significant_turns?: SignificantTurn[];
    low_trust?: boolean;
  };
}

export interface ConvergenceMapData {
  consensus: string[];
  productive_tensions: { description: string; understanding: string }[];
  irreducible_differences: { description: string; why_irreducible: string }[];
  key_insight: string;
  agent_final_positions: Record<string, string>;
}
