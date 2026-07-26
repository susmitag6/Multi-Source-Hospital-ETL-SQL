-- =============================================================================
-- Q8: How many of the 6 current (2022) topics does each hospital actually
--     report data for? A hospital could exist in the directory but only
--     report, say, HAIs and nothing else. Ranks states by average topic
--     coverage -- a proxy for how "data-complete" hospitals in that state
--     tend to be.
--     Demonstrates: COUNT(DISTINCT ...) across a joined dimension, a second
--     level of aggregation (per-hospital coverage rolled up to per-state).
-- =============================================================================

WITH hospital_topic_coverage AS (
    SELECT
        hm.facility_id,
        COUNT(DISTINCT m.topic) AS topics_with_data
    FROM hospital_measures hm
    JOIN measures m ON m.measure_id = hm.measure_id
    WHERE m.file_year = 2022 AND hm.value IS NOT NULL
    GROUP BY hm.facility_id
)
SELECT
    h.state,
    ROUND(AVG(cov.topics_with_data), 2) AS avg_topics_reported,
    COUNT(*)                             AS num_hospitals
FROM hospital_topic_coverage cov
JOIN hospitals h ON h.facility_id = cov.facility_id
GROUP BY h.state
HAVING COUNT(*) >= 10
ORDER BY avg_topics_reported DESC
LIMIT 15;
