-- The Council of Me - Initial schema
-- Run with: psql -U postgres -d council_of_me -f 001_initial.sql

-- 1. users (optional; for anonymous session user_id can be a placeholder UUID)
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    framing_preference VARCHAR(50),
    locale VARCHAR(10) DEFAULT 'zh-CN',
    safety_consent_at TIMESTAMPTZ,
    last_disclaimer_at TIMESTAMPTZ
);

-- 2. debate_sessions (core_dilemma filled in elicitation phase)
CREATE TABLE IF NOT EXISTS debate_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,

    core_dilemma TEXT DEFAULT '',
    dilemma_type VARCHAR(50),
    complexity_score FLOAT,
    debate_level VARCHAR(10),
    max_rounds INT,
    agent_count INT,

    total_rounds INT,
    total_duration_seconds INT,
    user_interventions_count INT DEFAULT 0,

    status VARCHAR(30) NOT NULL DEFAULT 'created',
    framing_preference VARCHAR(50),
    elicitation_history JSONB,
    conflict_profile_snapshot JSONB,
    identity_cards_snapshot JSONB,

    crisis_returned_at TIMESTAMPTZ,
    rounds_since_crisis_return INT DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_debate_sessions_user_created ON debate_sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_debate_sessions_status ON debate_sessions(status);

-- 3. inner_voices
CREATE TABLE IF NOT EXISTS inner_voices (
    voice_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate_sessions(session_id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    core_concern TEXT NOT NULL,
    typical_statement TEXT,
    intensity FLOAT DEFAULT 0.5,
    mapped_agent_id VARCHAR(50),
    source VARCHAR(20) DEFAULT 'explicit'
);
CREATE INDEX IF NOT EXISTS idx_inner_voices_session ON inner_voices(session_id);

-- 4. value_conflicts
CREATE TABLE IF NOT EXISTS value_conflicts (
    conflict_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate_sessions(session_id) ON DELETE CASCADE,
    value_a VARCHAR(100) NOT NULL,
    value_b VARCHAR(100) NOT NULL,
    tension_description TEXT,
    value_a_category VARCHAR(50),
    value_b_category VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS idx_value_conflicts_session ON value_conflicts(session_id);

-- 5. debate_statements
CREATE TABLE IF NOT EXISTS debate_statements (
    statement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate_sessions(session_id) ON DELETE CASCADE,
    agent_id VARCHAR(50) NOT NULL,
    agent_name VARCHAR(100) NOT NULL,
    round_number INT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cited_agent_id VARCHAR(50),
    stance VARCHAR(20),
    consistency_score FLOAT,
    depth_score FLOAT,
    has_concession BOOLEAN,
    is_final BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_debate_statements_session_round ON debate_statements(session_id, round_number);

-- 6. user_annotations
CREATE TABLE IF NOT EXISTS user_annotations (
    annotation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate_sessions(session_id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    round_number INT,
    agent_speaking VARCHAR(50),
    emotion_tag VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS idx_user_annotations_session ON user_annotations(session_id);

-- 7. reflection_records
CREATE TABLE IF NOT EXISTS reflection_records (
    reflection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate_sessions(session_id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    reflection_level INT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reflection_type VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS idx_reflection_records_session ON reflection_records(session_id);

-- 8. user_interventions
CREATE TABLE IF NOT EXISTS user_interventions (
    intervention_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate_sessions(session_id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    intervention_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    round_number INT,
    content TEXT,
    target_agent_id VARCHAR(50),
    agent_response TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_interventions_session ON user_interventions(session_id);

-- 9. user_patterns
CREATE TABLE IF NOT EXISTS user_patterns (
    pattern_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    pattern_type VARCHAR(50) NOT NULL,
    pattern_description TEXT NOT NULL,
    evidence_session_ids UUID[] NOT NULL,
    confidence_score FLOAT,
    first_detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurrence_count INT DEFAULT 1,
    user_confirmed BOOLEAN DEFAULT FALSE,
    user_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_patterns_user ON user_patterns(user_id);

-- 10. review_reminders
CREATE TABLE IF NOT EXISTS review_reminders (
    reminder_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate_sessions(session_id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    reminder_type VARCHAR(50),
    status VARCHAR(20) DEFAULT 'pending',
    user_responded BOOLEAN DEFAULT FALSE,
    response_content TEXT,
    response_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_review_reminders_scheduled ON review_reminders(user_id, scheduled_at);

-- 11. agent_iterations
CREATE TABLE IF NOT EXISTS agent_iterations (
    iteration_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    agent_id VARCHAR(50) NOT NULL,
    version INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    identity_card_snapshot JSONB NOT NULL,
    learned_from_sessions UUID[] NOT NULL,
    learning_summary TEXT,
    effectiveness_score FLOAT,
    user_satisfaction_score FLOAT
);
CREATE INDEX IF NOT EXISTS idx_agent_iterations_user_agent ON agent_iterations(user_id, agent_id);
