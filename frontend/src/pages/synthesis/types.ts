import type {
  FeelingId,
  ReflectionDialogueTurn,
  ReflectionNodeExploration,
} from '../../api/client';

export type {
  FeelingId,
  ReflectionPathId,
  ReflectionViewMode,
} from '../../api/client';

/** Agent color mapping — reuse from debate phase */
export const AGENT_COLORS: Record<string, string> = {
  empathic_listener: '#fb7185',   // rose-400
  rational_analyst: '#38bdf8',    // sky-400
  critical_examiner: '#fbbf24',   // amber-400
  creative_explorer: '#a78bfa',   // violet-400
  synthesizer: '#34d399',         // emerald-400
};

export const AGENT_NAMES: Record<string, string> = {
  empathic_listener: '共情倾听者',
  rational_analyst: '理性分析者',
  critical_examiner: '批判审视者',
  creative_explorer: '创意探索者',
  synthesizer: '整合者',
};

export const SHIFT_ICONS: Record<string, string> = {
  none: '—',
  expansion: '↗',
  revision: '↺',
  reversal: '⇄',
};

export type NodeType = 'center' | 'tension' | 'voice' | 'consensus' | 'irreducible' | 'productive';

export type ImmersivePanelMode = 'closed' | 'explore' | 'dialogue' | 'trace';

export interface LandscapeDisplayModel {
  center: {
    id: 'center';
    label: string;
    insight: string;
  };
  narrative: string;
  tensions: Array<{
    id: string;
    label: string;
    intensity: number;
    agentIds: string[];
    poleA: string;
    poleB: string;
  }>;
  voices: Array<{
    id: string;
    label: string;
    stance: string;
    color: string;
    shiftIcon: string;
  }>;
  outcomes: Array<{
    id: string;
    type: 'consensus' | 'productive' | 'irreducible';
    label: string;
  }>;
}

export const FEELING_LABELS: Record<FeelingId, string> = {
  resonance: '共鸣',
  unease: '不安',
  surprise: '意外',
  seen: '被看见',
  push_back: '想反驳',
  wordless: '说不清',
};

export interface ReflectionEchoCard {
  card_id: string;
  title: string;
  body: string;
  path_id: string;
  created_at: string;
}

export type ReflectionDialogueTurnWithEcho = ReflectionDialogueTurn & {
  echo_cards?: ReflectionEchoCard[];
};

export type ReflectionExplorationView = Omit<ReflectionNodeExploration, 'dialogue'> & {
  dialogue: ReflectionDialogueTurnWithEcho[];
};

export interface SelectedNode {
  id: string;
  type: NodeType;
}
