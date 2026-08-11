"""
CMS Hospital Compare — Clean & Load (multi-source)
========================================================
Combines 8 raw CMS files spanning two eras into one normalized SQLite
database:

  2011 era (Coursera/Hospital-Quality snapshot):
    - hospital-data.csv                 hospital directory
    - outcome-of-care-measures.csv      AMI/HF/pneumonia mortality & readmission

  2026 era (CMS Hospital Compare / Care Compare, 6 topic files):
    - Complications_and_Deaths-Hospital_2026.csv
    - Healthcare_Associated_Infections-Hospita_2026.csv        (HAIs)
    - FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv
    - Medicare_Hospital_Spending_Per_Patient-Hospital_2026.csv (replaces "Payment_and_Value_of_Care")
    - Timely_and_Effective_Care-Hospital_2026.csv
    - FY_2026_HAC_Reduction_Program_Hospital.csv                (HAC penalty program)

Data-quality issues handled (full detail in docs/data_cleaning_notes.md):
  1. Sentinel strings ("Not Available") in the 2011 file vs. plain blank
     cells for suppressed data in the 2026 files -- two different missing-
     value conventions across eras that must be reconciled to a single NULL.
  2. CMS changed the topic-file SHAPE between the 2022 archive and the 2026
     downloads. In 2022 every topic file was WIDE (one column per measure,
     with "Hospital" mashing name + ID together, e.g. "NAME (010001)").
     In 2026, most topic files are already LONG/tidy -- one row per
     hospital x measure, with separate "Facility ID" / "Facility Name"
     columns and a "Measure ID" + "Score" pair -- but not all of them:
       - Complications_and_Deaths, HAIs, Timely_and_Effective_Care, and
         Medicare_Hospital_Spending_Per_Patient are LONG (Measure ID + Score,
         sometimes with extra numeric columns like Denominator/Lower
         Estimate/Higher Estimate/Sample).
       - Hospital_Readmissions_Reduction_Program is LONG by measure, but
         has no "Measure ID" column -- "Measure Name" (e.g.
         "READM-30-HIP-KNEE-HRRP") IS the code, and each row spreads
         several numeric sub-metrics across separate columns
         (Excess Readmission Ratio, Predicted/Expected Readmission Rate,
         Number of Readmissions, Number of Discharges) instead of one
         "Score" column.
       - HAC_Reduction_Program is still WIDE: one row per hospital with
         ~13 named Z-score/SIR columns plus a categorical Payment
         Reduction (Yes/No) column.
     Because three different shapes exist in the same 2026 drop, this
     script uses a per-topic parsing strategy instead of one generic
     wide-melt loop (contrast with the 2022 version of this script, which
     could melt all 6 files the same way).
  3. The 2026 files no longer carry a "Hospital" column that mashes name
     and ID together -- Facility ID and Facility Name are already separate
     columns -- so the regex-split step from the 2022 version of this
     script is no longer needed.
  4. The 2026 topic files ALSO no longer carry the Hospital Type /
     Ownership / Beds / Lat / Lon dimension columns that the 2022 files
     repeated in all 6 files (old issue #3). Instead most 2026 files carry
     Address / City / State / ZIP / County / Phone. Practical effect: the
     hospitals dimension can be enriched with contact/address info for
     2026-only facilities, but NOT with ownership, bed count, or
     lat/lon -- those fields will be NULL for any hospital that only
     appears in the 2026 data and not in the 2011 snapshot. This is a
     real limitation of the source data, not a bug, and is reflected in a
     new `in_2026_data` flag (replacing the old `in_2022_data` flag).
  5. Facility ID is a real column in the 2026 files (not extracted via
     regex), so it must be forced to string dtype on read and zero-padded
     just like the 2011 Provider Number (old issue #10) -- otherwise
     pandas silently drops leading zeros, e.g. "010001" -> 10001.
  6. Inconsistent Hospital Ownership category spelling (2011 data only, in
     the 2026 era -- see #4 above for why 2026 has no ownership field at
     all).
  7. Wide topic files melted into one tidy long-format fact table so every
     era/shape can be queried the same way downstream.
  8. The HAC program file mixes numeric Z-score/SIR columns with a
     categorical "Payment Reduction" (Y/N) column -- split into a fact
     table (numeric) and a small dedicated table (categorical), same as
     before. Per-metric Footnote/Start Date/End Date columns are excluded
     by suffix pattern rather than a hardcoded list, since 2026 added
     several new per-metric date/footnote columns not present in 2022.
  9. Facility IDs only partially overlap between the 2011 and 2026 files
     (hospitals close, merge, and open over 15 years) -- handled as a
     left/outer join, not assumed to fully match.

Run:
    python3 clean_and_load.py
"""

import re
import sqlite3
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
TOPICS_DIR = RAW_DIR / "topics"
CLEAN_DIR = BASE_DIR / "data" / "clean"
DB_PATH = BASE_DIR / "hospital_compare.db"

CLEAN_DIR.mkdir(parents=True, exist_ok=True)

NA_TOKENS = {"Not Available", "Not Applicable", "N/A", "Too Few to Report", ""}

OWNERSHIP_MAP = {
    "Government - Hospital District or Authority": "Government - Hospital District or Authority",
    "Government-Hospital District or Authority": "Government - Hospital District or Authority",
    "Voluntary non-profit - Private": "Voluntary Non-Profit - Private",
    "Voluntary non-profit-Private": "Voluntary Non-Profit - Private",
    "Voluntary non-profit - Church": "Voluntary Non-Profit - Church",
    "Voluntary non-profit-Church": "Voluntary Non-Profit - Church",
    "Voluntary non-profit - Other": "Voluntary Non-Profit - Other",
    "Voluntary non-profit-Other": "Voluntary Non-Profit - Other",
    "Government - Local": "Government - Local",
    "Government-Local": "Government - Local",
    "Government - State": "Government - State",
    "Government-State": "Government - State",
    "Government - Federal": "Government - Federal",
    "Government-Federal": "Government - Federal",
    "Government Federal": "Government - Federal",
    "Proprietary": "Proprietary",
    "Physician": "Physician",
    "Tribal": "Tribal",
    "Department of Defense": "Department of Defense",
    "Veterans Health Administration": "Veterans Health Administration",
}


def normalize_ownership(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    val = re.sub(r"\s+", " ", str(val)).strip()
    return OWNERSHIP_MAP.get(val, val)


def to_float(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, str) and v.strip() in NA_TOKENS:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def slugify(text):
    """Turn a human-readable measure name into a short SCREAMING_SNAKE_CASE code.

    Note: parenthetical suffixes are KEPT (not stripped) because for several
    source columns the parenthetical is the only thing that distinguishes
    otherwise-identical sub-measures, e.g. "Payment for heart attack patients
    (Denominator)" vs "... (Payment)", or "READM-30-AMI (Excess Readmission
    Ratio)" vs "... (Number of Discharges)".
    """
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(text))
    text = re.sub(r"_+", "_", text).strip("_")
    return text.upper()


def zero_pad_id(series, width=6):
    return series.astype(str).str.strip().str.zfill(width)


# =============================================================================
# 1. 2011-era files (hospitals directory + AMI/HF/pneumonia outcomes)
# The datasets include:
#  - Hospital directory information
#  - Mortality rates
#  - Readmission rates
# =============================================================================
print("Loading 2011-era files...")
hosp_raw = pd.read_csv(RAW_DIR / "hospital-data.csv", dtype=str)
outcome_raw = pd.read_csv(RAW_DIR / "outcome-of-care-measures.csv", dtype=str)

hosp = hosp_raw.copy()
hosp.columns = [c.strip() for c in hosp.columns]
hosp = hosp.drop(columns=["Address 2", "Address 3"], errors="ignore")
for c in hosp.columns:
    hosp[c] = hosp[c].astype(str).str.strip()
    hosp[c] = hosp[c].replace({"nan": None, "": None})
hosp["Hospital Ownership"] = hosp["Hospital Ownership"].apply(normalize_ownership)
hosp["County"] = hosp["County"].apply(lambda x: x.upper() if isinstance(x, str) and x else None)
hosp["Provider Number"] = zero_pad_id(hosp["Provider Number"])
hosp = hosp.rename(columns={
    "Provider Number": "facility_id", "Hospital Name": "hospital_name_2011",
    "Address 1": "address_2011", "City": "city_2011", "State": "state_2011", "ZIP Code": "zip_code_2011",
    "County": "county_2011", "Phone Number": "phone_number_2011", "Hospital Type": "hospital_type_2011",
    "Hospital Ownership": "hospital_ownership_2011", "Emergency Services": "emergency_services",
})
hosp["emergency_services"] = hosp["emergency_services"].map({"Yes": 1, "No": 0})
# NOTE: address/city/zip_code/county/phone_number are suffixed "_2011" here
# (unlike the 2022 version of this script, which left them unsuffixed)
# because the 2026 topic files ALSO now carry address/city/zip/county/phone
# columns (they didn't in 2022 -- see issue #4), so both eras have to be
# reconciled the same way hospital_name/hospital_type/ownership already were.
directory_2011 = hosp[[
    "facility_id", "hospital_name_2011", "address_2011", "city_2011", "state_2011", "zip_code_2011",
    "county_2011", "phone_number_2011", "hospital_type_2011", "hospital_ownership_2011", "emergency_services",
]].drop_duplicates(subset="facility_id")

outcome = outcome_raw.copy()
outcome.columns = [c.strip() for c in outcome.columns]
outcome["Provider Number"] = zero_pad_id(outcome["Provider Number"])

legacy_measure_defs = [
    ("Heart Attack", "Mortality", "Hospital 30-Day Death (Mortality) Rates from Heart Attack"),
    ("Heart Failure", "Mortality", "Hospital 30-Day Death (Mortality) Rates from Heart Failure"),
    ("Pneumonia", "Mortality", "Hospital 30-Day Death (Mortality) Rates from Pneumonia"),
    ("Heart Attack", "Readmission", "Hospital 30-Day Readmission Rates from Heart Attack"),
    ("Heart Failure", "Readmission", "Hospital 30-Day Readmission Rates from Heart Failure"),
    ("Pneumonia", "Readmission", "Hospital 30-Day Readmission Rates from Pneumonia"),
]

fact_rows = []
measure_rows = {}

for condition, category, rate_col in legacy_measure_defs:
    measure_id = f"LEGACY2011_{category.upper()}_{condition.upper().replace(' ', '_')}"
    measure_rows[measure_id] = {
        "measure_id": measure_id,
        "measure_name": f"{category} Rate - {condition} (2011)",
        "topic": "Legacy_2011",
        "file_year": 2011,
    }
    for _, r in outcome.iterrows():
        rate = to_float(r.get(rate_col))
        fact_rows.append({
            "facility_id": r["Provider Number"], "measure_id": measure_id,
            "file_year": 2011, "value": rate,
        })

print(f"  2011 directory: {len(directory_2011)} hospitals")
print(f"  2011 legacy measures: {len(legacy_measure_defs)} measures x {len(outcome)} hospitals")

# =============================================================================
# 2. 2026-era topic files
#
# Three different shapes are present in this drop (see issue #2 above), so
# each topic is handled by one of three code paths below:
#   (a) LONG_SCORE_TOPICS  -- already tidy, "Measure ID" + "Score" (+ extras)
#   (b) READMISSIONS       -- tidy by measure, but no "Measure ID" column and
#                              several numeric sub-metrics per row
#   (c) WIDE_TOPICS         -- still one row per hospital, many named columns
# =============================================================================
print("\nLoading 2026-era topic files...")

FILE_YEAR = 2026

TOPIC_FILES = {
    "Complications_and_Deaths": "Complications_and_Deaths-Hospital_2026.csv",
    "HAIs": "Healthcare_Associated_Infections-Hospita_2026.csv",
    "Readmissions_Reduction_Program": "FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv",
    "Payment_and_Value_of_Care": "Medicare_Hospital_Spending_Per_Patient-Hospital_2026.csv",
    "Timely_and_Effective_Care": "Timely_and_Effective_Care-Hospital_2026.csv",
    "HAC_Reduction_Program": "FY_2026_HAC_Reduction_Program_Hospital.csv",
}

# Topics that arrive already long/tidy, keyed by an explicit "Measure ID"
# column, with "Score" as the primary value (plus sometimes extra numeric
# columns like Denominator / Lower Estimate / Higher Estimate / Sample).
LONG_SCORE_TOPICS = {
    "Complications_and_Deaths", "HAIs", "Timely_and_Effective_Care", "Payment_and_Value_of_Care",
}

# Columns that are metadata/dimension info, never a numeric measure value,
# across the "long" 2026 files. (Not every file has every column.)
METADATA_COLS = {
    "Facility ID", "Facility Name", "Address", "City/Town", "State", "ZIP Code",
    "County/Parish", "Telephone Number", "Measure ID", "Measure Name", "Condition",
    "Compared to National", "Footnote", "Start Date", "End Date",
}

hospital_dim_rows = []
hac_payment_reduction_rows = []


def add_dimension_rows(df, topic):
    """Pull one dimension row per unique facility_id out of a topic file.

    2026 files carry address/city/state/zip/county/phone but (unlike the
    2022 files) no Hospital Type / Ownership / Beds / Lat / Lon -- those
    columns simply don't exist anymore (issue #4), so they're left as None
    here and can only be filled in later from the 2011 directory, if the
    hospital happens to appear there too.
    """
    dim = df.drop_duplicates(subset="facility_id")
    for _, r in dim.iterrows():
        hospital_dim_rows.append({
            "facility_id": r["facility_id"],
            "hospital_name": r.get("Facility Name"),
            "hospital_type": None,
            "hospital_ownership": None,
            "beds": None, "lat": None, "lon": None,
            "address": r.get("Address"), "city": r.get("City/Town"),
            "zip_code": r.get("ZIP Code"), "county": r.get("County/Parish"),
            "phone_number": r.get("Telephone Number"),
            "state": r.get("State"), "source_topic": topic,
        })


for topic, fname in TOPIC_FILES.items():
    df = pd.read_csv(TOPICS_DIR / fname, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df["facility_id"] = zero_pad_id(df["Facility ID"])

    if topic in LONG_SCORE_TOPICS:
        # ---- (a) Already long: one row per hospital x measure ----------
        add_dimension_rows(df, topic)

        metric_cols = [c for c in df.columns if c not in METADATA_COLS and c != "facility_id"
                       and not c.startswith("Unnamed")]

        n_measures = df["Measure ID"].nunique()
        for _, r in df.iterrows():
            code = r["Measure ID"]
            for metric_col in metric_cols:
                # Keep the primary "Score" under the plain measure code, and
                # suffix any secondary numeric columns (Denominator, Lower/
                # Higher Estimate, Sample) so they don't collide with Score
                # -- same principle as issue #6 in the 2022 version of this
                # script: the suffix is often the only thing distinguishing
                # two sub-measures of the same underlying metric.
                if metric_col == "Score":
                    measure_id = f"{topic.upper()}__{slugify(code)}"
                else:
                    measure_id = f"{topic.upper()}__{slugify(code)}__{slugify(metric_col)}"
                if measure_id not in measure_rows:
                    measure_rows[measure_id] = {
                        "measure_id": measure_id,
                        "measure_name": f"{r.get('Measure Name', code)} ({metric_col})" if metric_col != "Score"
                                         else r.get("Measure Name", code),
                        "topic": topic, "file_year": FILE_YEAR,
                    }
                fact_rows.append({
                    "facility_id": r["facility_id"], "measure_id": measure_id,
                    "file_year": FILE_YEAR, "value": to_float(r.get(metric_col)),
                })

        print(f"  {topic}: {df['facility_id'].nunique()} hospitals x {n_measures} measures "
              f"({len(metric_cols)} numeric column(s)/measure)")

    elif topic == "Readmissions_Reduction_Program":
        # ---- (b) Long by measure, but code lives in "Measure Name" and --
        # ---- several numeric sub-metrics are spread across columns -----
        add_dimension_rows(df, topic)

        metric_cols = [
            "Number of Discharges", "Excess Readmission Ratio",
            "Predicted Readmission Rate", "Expected Readmission Rate", "Number of Readmissions",
        ]
        n_measures = df["Measure Name"].nunique()
        for _, r in df.iterrows():
            code = r["Measure Name"]
            for metric_col in metric_cols:
                measure_id = f"{topic.upper()}__{slugify(code)}__{slugify(metric_col)}"
                if measure_id not in measure_rows:
                    measure_rows[measure_id] = {
                        "measure_id": measure_id,
                        "measure_name": f"{code} ({metric_col})",
                        "topic": topic, "file_year": FILE_YEAR,
                    }
                fact_rows.append({
                    "facility_id": r["facility_id"], "measure_id": measure_id,
                    "file_year": FILE_YEAR, "value": to_float(r.get(metric_col)),
                })

        print(f"  {topic}: {df['facility_id'].nunique()} hospitals x {n_measures} measures "
              f"({len(metric_cols)} numeric column(s)/measure)")

    else:
        # ---- (c) Still wide: HAC_Reduction_Program ----------------------
        # One row per hospital. Exclude per-metric Footnote/Start Date/End
        # Date columns by suffix pattern (2026 added several new ones vs.
        # 2022), plus Fiscal Year and the categorical Payment Reduction
        # columns, which get their own small table instead.
        add_dimension_rows(df, topic)

        hac_payment_reduction_rows.extend(df.apply(
            lambda r: {
                "facility_id": r["facility_id"], "file_year": FILE_YEAR,
                "payment_reduction": r.get("Payment Reduction"),
            }, axis=1,
        ).tolist())

        exclude_suffixes = ("Footnote", "Start Date", "End Date")
        exclude_exact = {
            "Facility Name", "Facility ID", "State", "Fiscal Year",
            "Payment Reduction", "facility_id",
        }
        measure_cols = [
            c for c in df.columns
            if c not in exclude_exact and not c.endswith(exclude_suffixes) and not c.startswith("Unnamed")
        ]

        for col in measure_cols:
            measure_id = f"{topic.upper()}__{slugify(col)}"
            if measure_id not in measure_rows:
                measure_rows[measure_id] = {
                    "measure_id": measure_id, "measure_name": col, "topic": topic, "file_year": FILE_YEAR,
                }
            for _, r in df.iterrows():
                fact_rows.append({
                    "facility_id": r["facility_id"], "measure_id": measure_id,
                    "file_year": FILE_YEAR, "value": to_float(r.get(col)),
                })

        print(f"  {topic}: {df['facility_id'].nunique()} hospitals x {len(measure_cols)} measures")

# =============================================================================
# 3. Build unified hospitals dimension
# =============================================================================
print("\nBuilding unified hospitals dimension...")

dim_df = pd.DataFrame(hospital_dim_rows)

# Cross-file consistency check: does the hospital name agree across topic
# files for the same facility_id? (Ownership isn't checkable anymore since
# 2026 files don't carry an ownership column at all -- see issue #4.)
consistency = dim_df.groupby("facility_id")["hospital_name"].nunique()
inconsistent_ids = set(consistency[consistency > 1].index)
print(f"  Hospitals with inconsistent name across 2026 topic files: {len(inconsistent_ids)}")

# Resolve to one row per facility_id: prefer the row with the most complete
# address info (still meaningful even without beds/lat/lon).
dim_df["_completeness"] = dim_df["address"].notna().astype(int) + dim_df["zip_code"].notna().astype(int)
hospitals_2026 = (
    dim_df.sort_values(["facility_id", "_completeness"])
    .drop_duplicates("facility_id", keep="last")
    .drop(columns=["source_topic", "_completeness"])
)
hospitals_2026["has_name_conflict_2026"] = hospitals_2026["facility_id"].isin(inconsistent_ids).astype(int)

hospitals = hospitals_2026.merge(directory_2011, on="facility_id", how="outer")

# hospitals_2026 has NO ownership/beds/lat/lon at all (issue #4), so those
# three fields end up NULL unless the facility also appears in 2011.
# Everything else prefers the 2026 value and falls back to the 2011 value.
hospitals["hospital_name"] = hospitals["hospital_name"].fillna(hospitals["hospital_name_2011"])
hospitals["hospital_type"] = hospitals["hospital_type"].fillna(hospitals["hospital_type_2011"])
hospitals["hospital_ownership"] = hospitals["hospital_ownership"].fillna(hospitals["hospital_ownership_2011"])
hospitals["state"] = hospitals["state"].fillna(hospitals["state_2011"])
hospitals["address"] = hospitals["address"].fillna(hospitals["address_2011"])
hospitals["city"] = hospitals["city"].fillna(hospitals["city_2011"])
hospitals["zip_code"] = hospitals["zip_code"].fillna(hospitals["zip_code_2011"])
hospitals["county"] = hospitals["county"].fillna(hospitals["county_2011"])
hospitals["phone_number"] = hospitals["phone_number"].fillna(hospitals["phone_number_2011"])

# "in_2026_data" is tracked via has_name_conflict_2026, which only ever gets
# set (to 0 or 1, never NaN) for facility_ids that came from the 2026 loop
# in the first place -- so its presence, rather than its value, is what
# tells us a hospital was seen in 2026. Track that explicitly instead of
# relying on a fragile name comparison.
hospitals["in_2011_data"] = hospitals["hospital_name_2011"].notna().astype(int)
hospitals["in_2026_data"] = hospitals["has_name_conflict_2026"].notna().astype(int)
hospitals["has_name_conflict_2026"] = hospitals["has_name_conflict_2026"].fillna(0).astype(int)

hospitals_final = hospitals[[
    "facility_id", "hospital_name", "address", "city", "state", "zip_code", "county",
    "phone_number", "hospital_type", "hospital_ownership", "emergency_services",
    "beds", "lat", "lon", "in_2011_data", "in_2026_data", "has_name_conflict_2026",
]].drop_duplicates(subset="facility_id")

both = ((hospitals_final["in_2011_data"] == 1) & (hospitals_final["in_2026_data"] == 1)).sum()
print(f"  Unified hospitals: {len(hospitals_final)} "
      f"({hospitals_final['in_2011_data'].sum()} in 2011 data, "
      f"{hospitals_final['in_2026_data'].sum()} in 2026 data, {both} in both)")
print("  NOTE: beds/lat/lon are NULL for any hospital that only appears in "
      "the 2026 data -- those columns no longer exist in the 2026 files (issue #4).")

measures_df = pd.DataFrame(measure_rows.values())
fact_df = pd.DataFrame(fact_rows)
hac_pr_df = pd.DataFrame(hac_payment_reduction_rows)

print(f"  measures dimension: {len(measures_df)} distinct measures")
print(f"  hospital_measures fact table: {len(fact_df)} rows")
print(f"  NULL values in fact table: {fact_df['value'].isna().sum()} ({fact_df['value'].isna().mean():.1%})")

# =============================================================================
# 4. Persist cleaned CSVs + load into SQLite
# =============================================================================
hospitals_final.to_csv(CLEAN_DIR / "hospitals.csv", index=False)
measures_df.to_csv(CLEAN_DIR / "measures.csv", index=False)
fact_df.to_csv(CLEAN_DIR / "hospital_measures.csv", index=False)
hac_pr_df.to_csv(CLEAN_DIR / "hac_payment_reduction.csv", index=False)
print(f"\nWrote cleaned CSVs to {CLEAN_DIR}")

print(f"\nBuilding database at {DB_PATH} ...")
if DB_PATH.exists():
    DB_PATH.unlink()

conn = sqlite3.connect(DB_PATH)
schema_sql = (BASE_DIR / "sql" / "schema.sql").read_text()
conn.executescript(schema_sql)

hospitals_final.to_sql("hospitals", conn, if_exists="append", index=False)
measures_df.to_sql("measures", conn, if_exists="append", index=False)
fact_df.to_sql("hospital_measures", conn, if_exists="append", index=False)
hac_pr_df.to_sql("hac_payment_reduction", conn, if_exists="append", index=False)
conn.commit()

for tbl in ["hospitals", "measures", "hospital_measures", "hac_payment_reduction"]:
    n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"  {tbl}: {n} rows loaded")

conn.close()
print("\nDone.")
