-- =============================================================================
-- CMS Hospital Compare. Normalized Schema (v3, multi source)
-- =============================================================================
-- This combines 8 raw CMS files from two time periods:
--   2011: hospital-data.csv, outcome-of-care-measures.csv
--   2026: 6 topic files (Complications and Deaths, HAIs, Readmissions
--         Reduction Program, Payment and Value of Care, Timely and
--         Effective Care, HAC Reduction Program)
--
-- Every number based measure from both years and all 6 current topics is
-- put into ONE tidy long table, called hospital_measures. Each row is one
-- hospital paired with one measure. A separate measures table keeps track
-- of the topic and year for each measure. This is what makes it possible
-- to look up "every mortality measure" or "everything from 2026" with one
-- simple WHERE clause, instead of picking columns by hand from 6 different
-- wide tables.
--
-- Written for SQLite. It should also work in PostgreSQL with no changes,
-- since this file does not use AUTOINCREMENT.
-- =============================================================================

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS hac_payment_reduction;
DROP TABLE IF EXISTS hospital_measures;
DROP TABLE IF EXISTS measures;
DROP TABLE IF EXISTS hospitals;

-- -----------------------------------------------------------------------------
-- Dimension: hospitals (one row per hospital, combining the 2011 directory
-- and the 2026 topic files)
-- -----------------------------------------------------------------------------
CREATE TABLE hospitals (
    facility_id             TEXT PRIMARY KEY,   -- 6 character CMS facility ID
    hospital_name           TEXT,
    address                 TEXT,
    city                    TEXT,
    state                   TEXT,
    zip_code                TEXT,
    county                  TEXT,
    phone_number            TEXT,
    hospital_type           TEXT,               -- from the 2011 directory only
    hospital_ownership      TEXT,                -- from the 2011 directory only
    emergency_services      INTEGER,             -- 1 = yes, 0 = no (2011 only)
    beds                    REAL,                -- from the 2011 directory only
    lat                     REAL,                -- from the 2011 directory only
    lon                     REAL,                -- from the 2011 directory only
    in_2011_data            INTEGER NOT NULL,    -- 1 if this hospital is in the 2011 snapshot
    in_2026_data            INTEGER NOT NULL,    -- 1 if this hospital is in any 2026 topic file
    has_name_conflict_2026  INTEGER NOT NULL     -- 1 if the 2026 files disagreed on the hospital name
);

CREATE INDEX idx_hospitals_state  ON hospitals (state);
CREATE INDEX idx_hospitals_county ON hospitals (county);

-- Note: beds, lat, and lon come only from the 2011 directory now. The 2026
-- topic files do not include these fields at all, so they will be blank
-- (NULL) for any hospital that only shows up in the 2026 data.

-- -----------------------------------------------------------------------------
-- Dimension: measures (every measure across all 8 source files)
-- -----------------------------------------------------------------------------
CREATE TABLE measures (
    measure_id     TEXT PRIMARY KEY,   -- e.g. 'HAIS__HAI_1_SIR', 'LEGACY2011_MORTALITY_HEART_ATTACK'
    measure_name   TEXT NOT NULL,      -- human readable name from the source file
    topic          TEXT NOT NULL,      -- Legacy_2011 / Complications_and_Deaths / HAIs / etc.
    file_year      INTEGER NOT NULL    -- 2011 or 2026
);

CREATE INDEX idx_measures_topic ON measures (topic);

-- -----------------------------------------------------------------------------
-- Fact: hospital_measures (one row per hospital and per measure, every
-- topic and year together)
-- -----------------------------------------------------------------------------
CREATE TABLE hospital_measures (
    facility_id   TEXT NOT NULL REFERENCES hospitals (facility_id),
    measure_id    TEXT NOT NULL REFERENCES measures (measure_id),
    file_year     INTEGER NOT NULL,
    value         REAL,   -- NULL if the source value was blank, "Not Available", "N/A", etc.
    PRIMARY KEY (facility_id, measure_id)
);

CREATE INDEX idx_hm_measure ON hospital_measures (measure_id);
CREATE INDEX idx_hm_value   ON hospital_measures (value);

-- -----------------------------------------------------------------------------
-- Small dimension: HAC Reduction Program payment penalty (a yes/no flag,
-- not a number, so it is kept separate from hospital_measures)
-- -----------------------------------------------------------------------------
CREATE TABLE hac_payment_reduction (
    facility_id         TEXT NOT NULL REFERENCES hospitals (facility_id),
    file_year           INTEGER NOT NULL,
    payment_reduction   TEXT,   -- 'Yes', 'No', or NULL
    PRIMARY KEY (facility_id, file_year)
);
