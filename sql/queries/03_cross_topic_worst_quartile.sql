-- =============================================================================
-- Q3: Hospitals that rank in the WORST national quartile on BOTH a hospital-
--     acquired infection measure (MRSA) and a complications measure (AMI
--     mortality) at the same time -- i.e. consistently poor performers
--     across two entirely different topic files, not just one bad number.
--     Demonstrates: two independent CTEs each using NTILE(), joined on
--     facility_id -- something only possible because both topics live in
--     the same fact table under a shared key.
-- =============================================================================

WITH mrsa_quartile AS (
    SELECT facility_id, value,
           NTILE(4) OVER (ORDER BY value DESC) AS quartile   -- DESC: quartile 1 = worst (highest SIR)
    FROM hospital_measures
    WHERE measure_id = 'HAIS__MRSA_SIR' AND value IS NOT NULL
),
ami_quartile AS (
    SELECT facility_id, value,
           NTILE(4) OVER (ORDER BY value DESC) AS quartile
    FROM hospital_measures
    WHERE measure_id = 'COMPLICATIONS_AND_DEATHS__AMI_30_DAY_MORTALITY_RATE_MORT_30_AMI'
      AND value IS NOT NULL
)
SELECT
    h.hospital_name, h.state,
    m.value AS mrsa_sir, a.value AS ami_mortality_rate
FROM mrsa_quartile m
JOIN ami_quartile a ON a.facility_id = m.facility_id
JOIN hospitals h ON h.facility_id = m.facility_id
WHERE m.quartile = 1 AND a.quartile = 1
ORDER BY m.value DESC;
