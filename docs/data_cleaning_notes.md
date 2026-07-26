# Data Cleaning Notes

Source: **CMS Hospital Compare / Care Compare**, official U.S. government data
comparing quality of care across Medicare-certified hospitals. Two eras and
8 raw files were combined:

**2011 snapshot** (2 files):
- `hospital-data.csv` — hospital directory (4,826 rows, 13 columns)
- `outcome-of-care-measures.csv` — AMI/HF/pneumonia mortality & readmission (4,706 rows, 46 columns)

**2022 snapshot** (6 topic files, ~3,000–4,400 hospitals each):
- `Complications_and_Deaths_2022.csv`
- `HAIs_2022.csv` (healthcare-associated infections)
- `Hospital_Readmissions_Reduction_Program_2022.csv`
- `Payment_and_Value_of_Care_2022.csv`
- `Timely_and_Effective_Care_2022.csv`
- `Hospital_Acquired_Conditions_Reduction_Program_2022.csv` (HAC penalty program)

The 2022 files are pulled from a third-party archive
([klocey/hospitals-data-archive](https://github.com/klocey/hospitals-data-archive))
that curates CMS's own historical Hospital Compare releases — CMS itself only
keeps the current quarter's data easily downloadable, not a browsable
year-by-year archive. For current data, pull fresh files from
[data.cms.gov/provider-data](https://data.cms.gov/provider-data/topics/hospitals).

## Issues found and how they were handled

| # | Issue | Where | Fix |
|---|-------|-------|-----|
| 1 | Numeric rate/score columns stored as text with the sentinel `"Not Available"` in the 2011 file, vs. a **plain blank cell** for the same kind of suppressed/small-sample data in the 2022 files — two different missing-value conventions across eras | All measure files | Both normalized to a real SQL `NULL` via a single `to_float()` helper that treats blank, `"Not Available"`, and `"Not Applicable"` identically. Reconciling this was necessary before the two eras could share one fact table. |
| 2 | The 2022 "Hospital" column mashes the name and facility ID into one string, e.g. `"SOUTHEAST HEALTH MEDICAL CENTER (010001)"` | All 6 2022 topic files | Split with a regex (`^(.*)\s\((\w+)\)\s*$`) into `hospital_name` + `facility_id` before any joins could happen. |
| 3 | Every one of the 6 2022 topic files repeats the same dimension columns (Hospital Type, Ownership, Beds, Lat, Lon, State) for every hospital — 6x duplication of the same facts, with no guarantee they agree | All 6 2022 topic files | Extracted once into a single `hospitals` dimension. **Checked, not assumed**, that the 6 files agree: `scripts/clean_and_load.py` computes `hospitals.has_ownership_conflict_2022`, and `sql/queries/07_cross_file_consistency_check.sql` reproduces the check in pure SQL. Result on this dataset: 0 conflicts — worth stating explicitly rather than trusting silently. |
| 4 | `Hospital Ownership` category written 3+ inconsistent ways for the same real category (e.g. `"Government - Local"` vs `"Government-Local"`; `"Voluntary non-profit - Private"` vs `"Voluntary non-profit-Private"`) | 2011 directory + all 2022 files | Explicit canonical mapping (not a blind regex — `"non-profit"` is a legitimate hyphenated word a naive dash-normalizer would wrongly split). Applied consistently across both eras. |
| 5 | Wide/denormalized layout: the 2011 outcome file has 46 columns (6 repeating groups), and the 6 2022 topic files individually have 6–39 measure columns each — 6 different wide tables that can't be queried together as-is | All measure files | **All** measures from **all 8 files** were melted into one tidy long-format fact table (`hospital_measures`, one row per hospital × measure), with a `measures` dimension carrying `topic` and `file_year`. This is what makes cross-topic queries (see `sql/queries/03`, `06`, `08`) possible with a single `WHERE m.topic = ...` instead of hand-picking columns from 6 different tables. |
| 6 | Naive column slugging collapsed genuinely different sub-measures into the same ID — e.g. `"Payment for heart attack patients (Denominator)"` and `"... (Payment)"` both became `PAYMENT_FOR_HEART_ATTACK_PATIENTS` after an early version of the cleaning script stripped parenthetical text, causing a primary-key collision on load | `Readmissions_Reduction_Program`, `Payment_and_Value_of_Care`, `Timely_and_Effective_Care` | Fixed by keeping the parenthetical suffix in the generated `measure_id` instead of discarding it — the parenthetical is often the *only* thing distinguishing two sub-measures of the same underlying metric. |
| 7 | The HAC Reduction Program file mixes 6 numeric Z-score columns with one categorical `Payment Reduction` (Yes/No) column | `Hospital_Acquired_Conditions_Reduction_Program_2022.csv` | Numeric columns went into the shared `hospital_measures` fact table; the categorical flag was split into its own small table, `hac_payment_reduction`, since forcing Yes/No into a `REAL` column would either fail or silently coerce to garbage. |
| 8 | Facility IDs only partially overlap between the 2011 and 2022 snapshots — hospitals close, merge, rename, or open over an 11-year gap | 2011 directory vs. 2022 topic files | Merged with an **outer join**, not an inner join, and the result is tagged with `in_2011_data` / `in_2022_data` flags so downstream queries can be explicit about which slice of hospitals they're covering. Of 5,237 total unique facilities, 4,826 appear in 2011 data and 5,237 in 2022 data, with 4,826 present in both. |
| 9 | Leftover pandas index column (`Unnamed: 0`) baked into each 2022 CSV from how the source archive was generated | All 6 2022 topic files | Explicitly excluded during column selection — caught only after it first showed up as a nonsense "measure" named `Unnamed: 0` in the measures dimension. |
| 10 | `Provider Number` / `Facility ID` silently corrupts if read as an integer (drops leading zeros, e.g. `"010001"` → `10001`) | All files | Forced to string dtype on read and zero-padded — it's an identifier, not a quantity. |

## Design decision: one fact table across 8 files, not 8 separate tables

Each of the 8 source files is individually a reasonable "wide" table on its
own. The reason to melt all of them into **one** long fact table rather than
loading 8 separate wide tables is that the interesting questions are
cross-topic: *"do hospitals penalized for hospital-acquired conditions also
have worse infection rates?"* (query 06) or *"how did the same hospital's
mortality change from 2011 to 2022?"* (query 02) require joining across
topics/eras on a shared key. With 8 separate wide tables, each cross-topic
question needs a bespoke multi-way join hand-written against differently
shaped tables. With one fact table keyed by `(facility_id, measure_id)`,
every cross-topic question is the same shape of query: filter `measures` by
topic/year, join to `hospital_measures`, aggregate.
