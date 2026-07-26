-- =============================================================================
-- CMS Hospital Compare — Normalized Schema (v2, multi-source)
-- =============================================================================
-- Combines 8 raw CMS files across two eras:
--   2011: hospital-data.csv, outcome-of-care-measures.csv
--   2022: 6 topic files (Complications & Deaths, HAIs, Readmissions Reduction
--         Program, Payment & Value of Care, Timely & Effective Care,
--         HAC Reduction Program)
--
-- All numeric measures across both eras and all 6 current topics are unified
-- into ONE tidy long-format fact table (hospital_measures), keyed by
-- (facility_id, measure_id), with a measures lookup dimension carrying the
-- topic and year of each measure. This is what makes it possible to query
-- "every mortality-related measure" or "everything from 2022" with a single
-- WHERE clause instead of hand-picking columns from 6 different wide tables.
--
-- Written for SQLite (portable to PostgreSQL: swap AUTOINCREMENT usage is not
-- needed here .
-- =============================================================================

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS hac_payment_reduction;
DROP TABLE IF EXISTS hospital_measures;
DROP TABLE IF EXISTS measures;
DROP TABLE IF EXISTS hospitals;

-- -----------------------------------------------------------------------------
-- Dimension: hospitals (unified across the 2011 directory and 2022 topic files)
-- -----------------------------------------------------------------------------
CREATE TABLE hospitals (
    facility_id                 TEXT PRIMARY KEY,   -- 6-char CMS facility/provider ID
    hospital_name                TEXT,
    address                       TEXT,               -- from 2011 directory only
    city                          TEXT,               -- from 2011 directory only
    state                         TEXT,
    zip_code                      TEXT,               -- from 2011 directory only
    county                        TEXT,               -- from 2011 directory only
    phone_number                  TEXT,               -- from 2011 directory only
    hospital_type                 TEXT,
    hospital_ownership             TEXT,
    emergency_services              INTEGER,            -- 1 = yes, 0 = no (2011 only)
    beds                             REAL,               -- from 2022 topic files
    lat                               REAL,
    lon                               REAL,
    in_2011_data                       INTEGER NOT NULL,   -- 1 if present in the 2011 snapshot
    in_2022_data                       INTEGER NOT NULL,   -- 1 if present in any 2022 topic file
    has_ownership_conflict_2022         INTEGER NOT NULL    -- 1 if the 6 2022 files disagreed on ownership
);

CREATE INDEX idx_hospitals_state  ON hospitals (state);
CREATE INDEX idx_hospitals_county ON hospitals (county);

-- -----------------------------------------------------------------------------
-- Dimension: measures (every measure across all 8 source files)
-- -----------------------------------------------------------------------------
CREATE TABLE measures (
    measure_id     TEXT PRIMARY KEY,   -- e.g. 'HAIS__CAUTI_SIR', 'LEGACY2011_MORTALITY_HEART_ATTACK'
    measure_name   TEXT NOT NULL,      -- original human-readable column name from the source file
    topic          TEXT NOT NULL,      -- Legacy_2011 / Complications_and_Deaths / HAIs / etc.
    file_year      INTEGER NOT NULL    -- 2011 or 2022
);

CREATE INDEX idx_measures_topic ON measures (topic);

-- -----------------------------------------------------------------------------
-- Fact: hospital_measures (one row per hospital x measure, ALL topics/years)
-- -----------------------------------------------------------------------------
CREATE TABLE hospital_measures (
    facility_id       TEXT NOT NULL REFERENCES hospitals (facility_id),
    measure_id        TEXT NOT NULL REFERENCES measures (measure_id),
    file_year         INTEGER NOT NULL,
    value             REAL,             -- NULL where source was blank / "Not Available"
    PRIMARY KEY (facility_id, measure_id)
);

CREATE INDEX idx_hm_measure ON hospital_measures (measure_id);
CREATE INDEX idx_hm_value   ON hospital_measures (value);

-- -----------------------------------------------------------------------------
-- Small dimension: HAC Reduction Program payment penalty (categorical, 2022)
-- Split out from hospital_measures because it's Yes/No, not numeric.
-- -----------------------------------------------------------------------------
CREATE TABLE hac_payment_reduction (
    facility_id         TEXT NOT NULL REFERENCES hospitals (facility_id),
    file_year           INTEGER NOT NULL,
    payment_reduction   TEXT,            -- 'Yes' / 'No' / NULL
    PRIMARY KEY (facility_id, file_year)
);
