-- Views for Council of Me

CREATE OR REPLACE VIEW user_session_overview AS
SELECT
    ds.session_id,
    ds.user_id,
    ds.created_at,
    ds.core_dilemma,
    ds.dilemma_type,
    ds.debate_level,
    ds.total_rounds,
    ds.status,
    (SELECT COUNT(*) FROM user_annotations ua WHERE ua.session_id = ds.session_id) AS annotation_count,
    (SELECT COUNT(*) FROM user_interventions ui WHERE ui.session_id = ds.session_id) AS intervention_count,
    (SELECT COUNT(*) FROM reflection_records rr WHERE rr.session_id = ds.session_id) AS reflection_count
FROM debate_sessions ds;

CREATE OR REPLACE VIEW user_value_conflict_frequency AS
SELECT
    ds.user_id,
    vc.value_a,
    vc.value_b,
    COUNT(*) AS occurrence_count,
    AVG(ds.complexity_score) AS avg_complexity
FROM value_conflicts vc
JOIN debate_sessions ds ON vc.session_id = ds.session_id
GROUP BY ds.user_id, vc.value_a, vc.value_b
HAVING COUNT(*) >= 2
ORDER BY occurrence_count DESC;
