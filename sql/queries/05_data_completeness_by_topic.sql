-- =============================================================================
-- Q5: Data quality audit -- what share of each TOPIC's measures is actually
--     reported vs. missing (blank in source / suppressed for small sample
--     size)? Run before trusting any aggregate above.
--     Demonstrates: conditional aggregation, NULL handling, grouping across
--     the unified fact table by topic rather than by individual measure.
-- =============================================================================

SELECT
    m.topic,
    m.file_year,
    COUNT(*)                                              AS total_cells,
    SUM(CASE WHEN hm.value IS NOT NULL THEN 1 ELSE 0 END) AS reported,
    SUM(CASE WHEN hm.value IS NULL THEN 1 ELSE 0 END)     AS missing,
    ROUND(100.0 * SUM(CASE WHEN hm.value IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_missing
FROM hospital_measures hm
JOIN measures m ON m.measure_id = hm.measure_id
GROUP BY m.topic, m.file_year
ORDER BY pct_missing DESC;
