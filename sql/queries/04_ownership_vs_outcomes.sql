-- =============================================================================
-- Q4: Does hospital ownership type correlate with 2022 mortality outcomes
--     across all 4 Complications & Deaths conditions at once?
--     Demonstrates: conditional aggregation (pivot pattern) across a long
--     fact table filtered to one topic.
-- =============================================================================

SELECT
    h.hospital_ownership,
    COUNT(DISTINCT h.facility_id) AS num_hospitals,
    ROUND(AVG(CASE WHEN m.measure_name LIKE 'AMI%'          THEN hm.value END), 2) AS avg_ami_mortality,
    ROUND(AVG(CASE WHEN m.measure_name LIKE 'Heart failure%' THEN hm.value END), 2) AS avg_hf_mortality,
    ROUND(AVG(CASE WHEN m.measure_name LIKE '%COPD%'         THEN hm.value END), 2) AS avg_copd_mortality,
    ROUND(AVG(CASE WHEN m.measure_name LIKE '%stroke%'       THEN hm.value END), 2) AS avg_stroke_mortality
FROM hospitals h
JOIN hospital_measures hm ON hm.facility_id = h.facility_id
JOIN measures m ON m.measure_id = hm.measure_id
WHERE m.topic = 'Complications_and_Deaths'
  AND h.hospital_ownership IS NOT NULL
GROUP BY h.hospital_ownership
HAVING COUNT(DISTINCT h.facility_id) >= 20
ORDER BY avg_ami_mortality DESC;
