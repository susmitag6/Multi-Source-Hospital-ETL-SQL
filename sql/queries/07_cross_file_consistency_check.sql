-- =============================================================================
-- Q7: Data-quality QA check -- since Hospital Type, Ownership, Beds, Lat/Lon
--     and State are repeated in all 6 of the 2022 topic files, do they ever
--     disagree for the same hospital? This query reproduces the check that
--     scripts/clean_and_load.py runs during ETL (result: hospitals.
--     has_ownership_conflict_2022 flag) so it's auditable directly in SQL,
--     not just trusted from a Python print statement.
--     Demonstrates: why denormalized source data needs a consistency check
--     before you trust a "clean" merged dimension table.
-- =============================================================================

SELECT
    hospital_name,
    state,
    has_ownership_conflict_2022
FROM hospitals
WHERE has_ownership_conflict_2022 = 1;

-- On this dataset this returns zero rows: the 6 CMS topic files agree on
-- ownership for every hospital. That is itself worth stating explicitly
-- rather than assuming it -- see docs/data_cleaning_notes.md.
