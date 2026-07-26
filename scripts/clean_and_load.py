"""
CMS Hospital Compare — Clean & Load (multi-source)
========================================================
Combines 8 raw CMS files spanning two eras into one normalized SQLite
database:

  2011 era (Coursera/Hospital-Quality snapshot):
    - hospital-data.csv                 hospital directory
    - outcome-of-care-measures.csv      AMI/HF/pneumonia mortality & readmission

  2022 era (CMS Hospital Compare archive, 6 topic files):
    - Complications_and_Deaths_2022.csv
    - HAIs_2022.csv                     healthcare-associated infections
    - Hospital_Readmissions_Reduction_Program_2022.csv
    - Payment_and_Value_of_Care_2022.csv
    - Timely_and_Effective_Care_2022.csv
    - Hospital_Acquired_Conditions_Reduction_Program_2022.csv (HAC penalty program)

Data-quality issues handled (full detail in docs/data_cleaning_notes.md):
  1. Sentinel strings ("Not Available") in the 2011 file vs. plain blank
     cells for suppressed data in the 2022 files -- two different missing-
     value conventions across eras that must be reconciled to a single NULL.
  2. The 2022 "Hospital" column mashes name and ID together, e.g.
     "SOUTHEAST HEALTH MEDICAL CENTER (010001)" -- must be split with regex.
  3. Every 2022 topic file repeats the same dimension columns (Hospital
     Type, Ownership, Beds, Lat, Lon, State) -- de-duplicated into one
     hospitals dimension, WITH a cross-file consistency check (see query 07).
  4. Inconsistent Hospital Ownership category spelling.
  5. 6 wide topic files (14-39 columns each) melted into one tidy
     long-format fact table so they can be queried uniformly.
  6. The HAC program file mixes numeric Z-scores with a categorical
     "Payment Reduction" (Y/N) column -- split into a fact table (numeric)
     and a small dedicated table (categorical).
  7. Facility IDs only partially overlap between the 2011 and 2022 files
     (hospitals close, merge, and open over 11 years) -- handled as a
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

NA_TOKENS = {"Not Available", "Not Applicable", ""}

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
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.upper()


# =============================================================================
# 1. 2011-era files (hospitals directory + AMI/HF/pneumonia outcomes)
#The datasets include:
#-Hospital directory information
#-Mortality rates
#-Readmission rates
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
hosp["Provider Number"] = hosp["Provider Number"].str.zfill(6)
hosp = hosp.rename(columns={
    "Provider Number": "facility_id", "Hospital Name": "hospital_name_2011",
    "Address 1": "address", "City": "city", "State": "state_2011", "ZIP Code": "zip_code",
    "County": "county", "Phone Number": "phone_number", "Hospital Type": "hospital_type_2011",
    "Hospital Ownership": "hospital_ownership_2011", "Emergency Services": "emergency_services",
})
hosp["emergency_services"] = hosp["emergency_services"].map({"Yes": 1, "No": 0})
directory_2011 = hosp[[
    "facility_id", "hospital_name_2011", "address", "city", "state_2011", "zip_code",
    "county", "phone_number", "hospital_type_2011", "hospital_ownership_2011", "emergency_services",
]].drop_duplicates(subset="facility_id")

outcome = outcome_raw.copy()
outcome.columns = [c.strip() for c in outcome.columns]
outcome["Provider Number"] = outcome["Provider Number"].str.zfill(6)

#*****************************WORKFLOW*****************************************
"""legacy_measure_defs
       │
       ▼
For each measure
       │
       ├── Create measure metadata
       │       │
       │       ▼
       │   measure_rows
       │
       ▼
Loop through every hospital (outcome)
       │
       ▼
Read hospital's rate
       │
       ▼
Create fact record
       │
       ▼
Append to fact_rows"""

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
# 2. 2022-era topic files
# =============================================================================||
#*****************************WORKFLOW*****************************************
# =============================================================================||
"""START
  │
  ▼
  ▼
Print:
"Loading 2022-era topic files..."
  │
  ▼
Define TOPIC_FILES
  │
  ├── Complications_and_Deaths
  ├── HAIs (Healthcare-Associated Infection)
  ├── Readmissions_Reduction_Program
  ├── Payment_and_Value_of_Care
  ├── Timely_and_Effective_Care
  └── HAC_Reduction_Program
  │
  ▼
Initialize storage
  │
  ├── hospital_dim_rows = []
  │       (Hospital dimension table)
  │
  └── hac_payment_reduction_rows = []
          (Special HAC payment reduction table)
  │
  ▼
FOR EACH TOPIC FILE
  │
  ▼
Read CSV file
  │
  ├── pd.read_csv()
  │
  └── Clean column names
          │
          ▼
     Remove extra spaces
     from column headers
  │
  ▼
Extract Hospital Name + Facility ID
  │
  │  Input:
  │  "ABC Medical Center (123456)"
  │
  │  Regex:
  │  ^(.*)\s\((\w+)\)\s*$
  │
  ▼
Create new columns:
  │
  ├── hospital_name = "ABC Medical Center"
  │
  └── facility_id = "123456"
  │
  ▼
Loop through every hospital row
  │
  ▼
Create hospital dimension record
  │
  └── Append to hospital_dim_rows
          │
          Example:
          {
            facility_id,
            hospital_name,
            hospital_type,
            ownership,
            beds,
            latitude,
            longitude,
            state,
            source_topic
          }
  │
  ▼
Is topic == HAC_Reduction_Program?
  │
  ├── YES
  │    │
  │    ▼
  │  Extract Payment Reduction
  │    │
  │    ▼
  │  Append to:
  │  hac_payment_reduction_rows
  │
  │
  └── NO
       │
       ▼
       Continue normally
  │
  ▼
Determine measure columns
  │
  │
  ├── Remove dimension columns:
  │      Hospital Type
  │      Ownership
  │      Beds
  │      Lat
  │      Lon
  │      State
  │      Hospital
  │      Facility ID
  │
  ├── Remove metadata columns
  │
  └── Remove unnamed columns
  │
  ▼
Remaining columns = Quality Measures
  │
  ▼
FOR EACH MEASURE COLUMN
  │
  ▼
Create measure ID
  │
  Example:
  Complications_and_Deaths
  + Mortality Rate
  │
  ▼
  COMPLICATIONS_AND_DEATHS__MORTALITY_RATE
  │
  ▼
Check if measure exists
  │
  ├── New measure?
  │       │
  │       ▼
  │    Add to measure_rows
  │
  └── Existing?
          │
          ▼
       Skip metadata creation
  │
  ▼
Loop through every hospital
  │
  ▼
Extract measure value
  │
  └── to_float(column value)
  │
  ▼
Create fact record
  │
  └── Append to fact_rows
          {
            facility_id,
            measure_id,
            file_year: 2022,
            value
          }
  │
  ▼
Print topic summary
  │
  └── Topic:
      X hospitals × Y measures
  │
  ▼
NEXT TOPIC FILE
  │
  ▼
END"""
# =============================================================================
print("\nLoading 2022-era topic files...")

TOPIC_FILES = {
    "Complications_and_Deaths": "Complications_and_Deaths_2022.csv",
    "HAIs": "HAIs_2022.csv",
    "Readmissions_Reduction_Program": "Hospital_Readmissions_Reduction_Program_2022.csv",
    "Payment_and_Value_of_Care": "Payment_and_Value_of_Care_2022.csv",
    "Timely_and_Effective_Care": "Timely_and_Effective_Care_2022.csv",
    "HAC_Reduction_Program": "Hospital_Acquired_Conditions_Reduction_Program_2022.csv",
}

DIM_COLS = ["Hospital Type", "Hospital Ownership", "Beds", "Lat", "Lon", "State"]

hospital_dim_rows = []
hac_payment_reduction_rows = []

for topic, fname in TOPIC_FILES.items():
    df = pd.read_csv(TOPICS_DIR / fname)
    df.columns = [c.strip() for c in df.columns]

    # Split "NAME (ID)" into hospital_name + facility_id
    extracted = df["Hospital"].str.extract(r"^(.*)\s\((\w+)\)\s*$")
    df["hospital_name"] = extracted[0].str.strip()
    df["facility_id"] = extracted[1].str.strip()

    for _, r in df.iterrows():
        hospital_dim_rows.append({
            "facility_id": r["facility_id"], "hospital_name": r["hospital_name"],
            "hospital_type": r.get("Hospital Type"),
            "hospital_ownership": normalize_ownership(r.get("Hospital Ownership")),
            "beds": to_float(r.get("Beds")), "lat": to_float(r.get("Lat")), "lon": to_float(r.get("Lon")),
            "state": r.get("State"), "source_topic": topic,
        })

    if topic == "HAC_Reduction_Program":
        for _, r in df.iterrows():
            hac_payment_reduction_rows.append({
                "facility_id": r["facility_id"], "file_year": 2022,
                "payment_reduction": r.get("Payment Reduction"),
            })
        exclude = DIM_COLS + ["Hospital", "file_year", "hospital_name", "facility_id", "Payment Reduction"]
    else:
        exclude = DIM_COLS + ["Hospital", "file_year", "hospital_name", "facility_id"]

    measure_cols = [c for c in df.columns if c not in exclude and not c.startswith("Unnamed")]

    for col in measure_cols:
        measure_id = f"{topic.upper()}__{slugify(col)}"
        if measure_id not in measure_rows:
            measure_rows[measure_id] = {
                "measure_id": measure_id, "measure_name": col, "topic": topic, "file_year": 2022,
            }
        for _, r in df.iterrows():
            val = to_float(r.get(col))
            fact_rows.append({
                "facility_id": r["facility_id"], "measure_id": measure_id,
                "file_year": 2022, "value": val,
            })

    print(f"  {topic}: {len(df)} hospitals x {len(measure_cols)} measures")

# =============================================================================
# 3. Build unified hospitals dimension
# =============================================================================||
#*****************************WORKFLOW*****************************************
# =============================================================================||

"""START
  │
  ▼
Build DataFrame from hospital_dim_rows
  │
  ▼
2022 Hospital Data from Multiple Topic Files
  │
  ├── HAIs
  ├── Complications & Deaths
  ├── Readmissions
  ├── Payment & Value of Care
  ├── Timely & Effective Care
  └── Hospital-Acquired-Condition (HAC) Reduction Program
  │
  ▼
Create unified 2022 hospital dimension
  │
  ├── Check ownership consistency
  │
  ├── Select best hospital record
  │
  └── Remove duplicate facilities
  │
  ▼
Merge with 2011 hospital directory
  │
  ▼
Create final hospitals table
  │
  ▼
Create measures dimension
  │
  ▼
Create hospital measures fact table
  │
  ▼
Create HAC payment reduction table
  │
  ▼
Validate row counts and missing values
  │
  ▼
END"""
# =============================================================================
print("\nBuilding unified hospitals dimension...")
# =============================================================================||

dim_df = pd.DataFrame(hospital_dim_rows)

# Cross-file consistency check: does ownership label agree across topic files
# for the same hospital? Flagged rather than silently resolved (see query 07).
consistency = dim_df.groupby("facility_id")["hospital_ownership"].nunique()
inconsistent_ids = set(consistency[consistency > 1].index)
print(f"  Hospitals with inconsistent ownership label across 2022 topic files: {len(inconsistent_ids)}")

# Resolve to one row per facility_id: prefer the most complete row (has beds/lat)
dim_df["_completeness"] = dim_df["beds"].notna().astype(int) + dim_df["lat"].notna().astype(int)
hospitals_2022 = (
    dim_df.sort_values(["facility_id", "_completeness"])
    .drop_duplicates("facility_id", keep="last")
    .drop(columns=["source_topic", "_completeness"])
)
hospitals_2022["has_ownership_conflict_2022"] = hospitals_2022["facility_id"].isin(inconsistent_ids).astype(int)

hospitals = hospitals_2022.merge(directory_2011, on="facility_id", how="outer")
hospitals["hospital_name"] = hospitals["hospital_name"].fillna(hospitals["hospital_name_2011"])
hospitals["hospital_type"] = hospitals["hospital_type"].fillna(hospitals["hospital_type_2011"])
hospitals["hospital_ownership"] = hospitals["hospital_ownership"].fillna(hospitals["hospital_ownership_2011"])
hospitals["state"] = hospitals["state"].fillna(hospitals["state_2011"])
hospitals["in_2011_data"] = hospitals["hospital_name_2011"].notna().astype(int)
hospitals["in_2022_data"] = (hospitals["beds"].notna() | hospitals["hospital_type"].notna()).astype(int)
hospitals["has_ownership_conflict_2022"] = hospitals["has_ownership_conflict_2022"].fillna(0).astype(int)

hospitals_final = hospitals[[
    "facility_id", "hospital_name", "address", "city", "state", "zip_code", "county",
    "phone_number", "hospital_type", "hospital_ownership", "emergency_services",
    "beds", "lat", "lon", "in_2011_data", "in_2022_data", "has_ownership_conflict_2022",
]].drop_duplicates(subset="facility_id")

both = ((hospitals_final["in_2011_data"] == 1) & (hospitals_final["in_2022_data"] == 1)).sum()
print(f"  Unified hospitals: {len(hospitals_final)} "
      f"({hospitals_final['in_2011_data'].sum()} in 2011 data, "
      f"{hospitals_final['in_2022_data'].sum()} in 2022 data, {both} in both)")

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
