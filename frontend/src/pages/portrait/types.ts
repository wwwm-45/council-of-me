export interface DilemmaLayer {
  description: string;
  depth: string;
  user_language: string;
}

export interface InnerVoice {
  name: string;
  core_concern: string;
  protective_intent: string;
  intensity: number;
}

export interface Tension {
  pole_a: string;
  pole_b: string;
  user_evidence: string;
}

export interface EmotionEntry {
  emotion: string;
  context: string;
  intensity: number;
}

export interface ComplexityAssessment {
  level: string;
  agent_count: number;
  max_rounds: number;
  narrative: string;
  reasoning: string;
  key_factors: string[];
}

export interface AgentAssignment {
  voice_name: string;
  agent_role: string;
  display_name: string;
  mapping_reason: string;
  system_prompt_addon: string;
}

export interface QuotePlacement {
  after_section: 'dilemma' | 'voices';
  quote: string;
  source_emotion: string;
}

export interface PortraitQualityIssue {
  code: string;
  severity: string;
  message: string;
  suggestion: string;
}

export interface PortraitQuality {
  status: string;
  score: number;
  issues: PortraitQualityIssue[];
  can_force_continue: boolean;
}

export interface PortraitDisplayOption {
  label: string;
  description: string;
  evidence: string;
}

export interface PortraitDecisionFocus {
  summary: string;
  option_a: PortraitDisplayOption;
  option_b: PortraitDisplayOption;
  why_hard: string;
}

export interface PortraitDisplayLayer {
  type: 'situation' | 'emotion' | 'value';
  title: string;
  description: string;
  evidence: string;
}

export interface PortraitDisplayVoice {
  name: string;
  concern: string;
  protective_intent: string;
  evidence: string;
  intensity: number;
}

export interface PortraitCouncilRole {
  display_name: string;
  source: 'inner_voice' | 'supplemental';
  represents: string;
  reason: string;
}

export interface PortraitCouncilPreview {
  summary: string;
  level: string;
  agent_count: number;
  roles: PortraitCouncilRole[];
}

export interface PortraitDisplayQuality {
  status: 'ready' | 'thin';
  warnings: string[];
}

export interface PortraitDisplay {
  headline: string;
  decision_focus: PortraitDecisionFocus | null;
  layers: PortraitDisplayLayer[];
  voices: PortraitDisplayVoice[];
  council_preview: PortraitCouncilPreview;
  quality: PortraitDisplayQuality;
}

export interface Portrait {
  core_dilemma: string;
  dilemma_layers: DilemmaLayer[];
  inner_voices: InnerVoice[];
  core_tensions: Tension[];
  emotion_map: EmotionEntry[];
  complexity: ComplexityAssessment;
  agent_assignments: AgentAssignment[];
  quote_placements: QuotePlacement[];
  conversation_depth: number;
  depth_trajectory: number[];
  portrait_quality?: PortraitQuality;
  quality_forced?: boolean;
  display?: PortraitDisplay;
}
