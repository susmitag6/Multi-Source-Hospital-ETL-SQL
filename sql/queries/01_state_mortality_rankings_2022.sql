-- =============================================================================
-- Q1: Rank U.S. states by average AMI (heart attack) 30-day mortality rate
--     using the CURRENT (2022) Complications & Deaths data, alongside the
--     national average.
--     Demonstrates: CTE, aggregate functions, RANK() window function, HAVING.
-- =============================================================================

WITH state_avg AS (
    SELECT
        h.state,
        ROUND(AVG(hm.value), 2)           AS avg_mortality_rate,
        COUNT(DISTINCT h.facility_id)      AS hospitals_reporting
    FROM hospitals h
    JOIN hospital_measures hm ON hm.facility_id = h.facility_id
    WHERE hm.measure_id = 'COMPLICATIONS_AND_DEATHS__AMI_30_DAY_MORTALITY_RATE_MORT_30_AMI'
      AND hm.value IS NOT NULL
    GROUP BY h.state
    HAVING COUNT(DISTINCT h.facility_id) >= 5
),
national AS (
    SELECT ROUND(AVG(value), 2) AS national_avg
    FROM hospital_measures
    WHERE measure_id = 'COMPLICATIONS_AND_DEATHS__AMI_30_DAY_MORTALITY_RATE_MORT_30_AMI'
      AND value IS NOT NULL
)
SELECT
    s.state,
    s.avg_mortality_rate,
    s.hospitals_reporting,
    n.national_avg,
    ROUND(s.avg_mortality_rate - n.national_avg, 2)   AS diff_vs_national,
    RANK() OVER (ORDER BY s.avg_mortality_rate DESC)  AS worst_to_best_rank
FROM state_avg s
CROSS JOIN national n
ORDER BY s.avg_mortality_rate DESC;
