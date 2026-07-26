-- =============================================================================
-- Q2: "Then vs. now" -- for hospitals present in BOTH the 2011 and 2022
--     snapshots, compare their AMI (heart attack) mortality rate across the
--     11-year gap. This is only possible because both eras were unified into
--     one fact table keyed the same way.
--     Demonstrates: self-join of the fact table across two measure_ids
--     representing the same underlying condition in different source eras.
-- =============================================================================

SELECT
    h.hospital_name,
    h.state,
    m2011.value  AS mortality_rate_2011,
    m2022.value  AS mortality_rate_2022,
    ROUND(m2022.value - m2011.value, 2) AS change_in_rate
FROM hospitals h
JOIN hospital_measures m2011
    ON m2011.facility_id = h.facility_id
   AND m2011.measure_id = 'LEGACY2011_MORTALITY_HEART_ATTACK'
JOIN hospital_measures m2022
    ON m2022.facility_id = h.facility_id
   AND m2022.measure_id = 'COMPLICATIONS_AND_DEATHS__AMI_30_DAY_MORTALITY_RATE_MORT_30_AMI'
WHERE m2011.value IS NOT NULL
  AND m2022.value IS NOT NULL
ORDER BY change_in_rate DESC
LIMIT 20;
