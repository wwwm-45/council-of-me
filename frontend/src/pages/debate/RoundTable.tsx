import type { AgentEvolutionData, AgentOrderEntry } from '../../api/client';
import CouncilChamber from './CouncilChamber';
import type { ArtifactEvent, ChatMsg, ConvergenceMapData } from './types';

interface RoundTableProps {
  agents: AgentOrderEntry[];
  userDisplayName?: string | null;
  speakingAgentId: string | null;
  currentRound: number;
  currentPhase: string;
  paused: boolean;
  streaming: boolean;
  hasMessages: boolean;
  latestContent: string;
  subtitleAnchorId?: string | null;
  subtitleAnchorName?: string | null;
  replyTo: string | null;
  onUserSeatClick: () => void;
  onPause: () => void;
  onResume: () => void;
  exchangeProgress: { seq: number; min: number; max: number } | null;
  messages?: ChatMsg[];
  roundArtifacts?: Map<number, ArtifactEvent>;
  convergenceMap?: ConvergenceMapData | null;
  agentEvolutions?: Map<string, AgentEvolutionData[]>;
}

export default function RoundTable(props: RoundTableProps) {
  return <CouncilChamber {...props} />;
}
