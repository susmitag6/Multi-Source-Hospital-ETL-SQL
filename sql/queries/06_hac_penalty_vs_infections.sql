-- =============================================================================
-- Q6: Do hospitals that got hit with a Medicare payment REDUCTION under the
--     Hospital-Acquired Conditions program actually have worse infection
--     rates (HAIs) than hospitals that didn't? This joins the small
--     categorical hac_payment_reduction table against the HAIs topic in the
--     main fact table.
--     Demonstrates: joining a dedicated small table to the main fact table,
--     conditional aggregation, and a real "does the penalty match the data"
--     sanity check.
-- =============================================================================

SELECT
    pr.payment_reduction,
    COUNT(DISTINCT pr.facility_id)                                            AS num_hospitals,
    ROUND(AVG(CASE WHEN hm.measure_id = 'HAIS__CLABSI_SIR' THEN hm.value END), 3) AS avg_clabsi_sir,
    ROUND(AVG(CASE WHEN hm.measure_id = 'HAIS__CAUTI_SIR'  THEN hm.value END), 3) AS avg_cauti_sir,
    ROUND(AVG(CASE WHEN hm.measure_id = 'HAIS__MRSA_SIR'   THEN hm.value END), 3) AS avg_mrsa_sir
FROM hac_payment_reduction pr
JOIN hospital_measures hm ON hm.facility_id = pr.facility_id
WHERE pr.payment_reduction IS NOT NULL
GROUP BY pr.payment_reduction;
