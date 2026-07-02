-- The Council of Me - Synthesis archive table
-- Stores synthesis results for cross-session comparison and history

CREATE TABLE IF NOT EXISTS session_syntheses (
    synthesis_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate_sessions(session_id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    synthesis_type VARCHAR(30) NOT NULL,
    narrative TEXT NOT NULL,
    voice_positions JSONB NOT NULL DEFAULT '[]',
    core_tensions JSONB NOT NULL DEFAULT '[]',
    consensus_areas JSONB NOT NULL DEFAULT '[]',
    protective_intents JSONB NOT NULL DEFAULT '[]',
    convergence_score FLOAT,
    novelty_score FLOAT,
    value_conflict_intensity FLOAT,
    debate_rounds INT,
    termination_mode VARCHAR(30),
    core_dilemma TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_session_syntheses_user ON session_syntheses(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_syntheses_session ON session_syntheses(session_id);
