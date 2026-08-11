# Data Cleaning Notes (Simple Version)

Source: CMS Hospital Compare / Care Compare. This is official U.S. government data. It compares the quality of care across hospitals that work with Medicare. Two time periods and 8 raw files were combined.

**2011 snapshot** (2 files):
- `hospital-data.csv`: list of hospitals (4,826 rows, 13 columns)
- `outcome-of-care-measures.csv`: death and readmission rates for heart attack, heart failure, and pneumonia (4,706 rows, 46 columns)

**2022 snapshot** (6 topic files, about 3,000 to 4,400 hospitals each):
- `Complications_and_Deaths_2022.csv`
- `HAIs_2022.csv` (infections caught in the hospital)
- `Hospital_Readmissions_Reduction_Program_2022.csv`
- `Payment_and_Value_of_Care_2022.csv`
- `Timely_and_Effective_Care_2022.csv`
- `Hospital_Acquired_Conditions_Reduction_Program_2022.csv` (penalty program for hospital acquired conditions)

The 2022 files come from a third party archive (klocey/hospitals-data-archive on GitHub). This archive collects CMS's own past Hospital Compare releases. CMS only keeps the current quarter's data easy to download, not a full year by year archive. For current data, get fresh files from data.cms.gov/provider-data/topics/hospitals.

## Problems found and how they were fixed

| # | Problem | Where it happened | How it was fixed |
|---|-------|-------|-----|
| 1 | Number columns were stored as text with the word "Not Available" in the 2011 file. But in the 2022 files, the same kind of missing data was just a blank cell. So there were two different ways of marking missing values. | All measure files | Both were changed into a real, empty (NULL) value using one helper function called `to_float()`. This function treats blank, "Not Available," and "Not Applicable" the same way. This had to be fixed before the two time periods could be combined into one table. |
| 2 | In the 2022 files, the "Hospital" column mixed the name and the ID number into one piece of text, like "SOUTHEAST HEALTH MEDICAL CENTER (010001)" | All 6 2022 topic files | Split into two separate pieces, `hospital_name` and `facility_id`, using a regex pattern, before doing any joins. |
| 3 | Each of the 6 2022 topic files repeated the same basic hospital facts (type, ownership, number of beds, latitude, longitude, state) for every hospital. That is the same information copied 6 times, with no guarantee all 6 copies matched. | All 6 2022 topic files | Pulled out once into a single `hospitals` table. This was checked, not assumed. A script (`scripts/clean_and_load.py`) checks for disagreement and creates a flag called `hospitals.has_ownership_conflict_2022`. A SQL file (`sql/queries/07_cross_file_consistency_check.sql`) does the same check again in plain SQL. Result: 0 conflicts found. This is worth saying clearly instead of just trusting it silently. |
| 4 | The "Hospital Ownership" category was written in 3 or more different ways for the same real category. For example: "Government - Local" versus "Government-Local," or "Voluntary non-profit - Private" versus "Voluntary non-profit-Private." | 2011 directory and all 2022 files | Fixed with a clear mapping list, not a simple find and replace. A simple dash remover would have wrongly broken up the word "non-profit," since that word is supposed to have a dash. The mapping was applied the same way across both time periods. |
| 5 | The data was laid out wide instead of long. The 2011 file had 46 columns (6 repeated groups). The 6 2022 files each had 6 to 39 measure columns. That is 6 different wide tables that could not be queried together as they were. | All measure files | All measures from all 8 files were reshaped into one long table called `hospital_measures`. Each row is one hospital paired with one measure. A separate `measures` table stores the topic and year for each measure. This makes it possible to ask questions across topics using one simple `WHERE m.topic = ...` line, instead of picking columns by hand from 6 different tables. |
| 6 | A basic, naive way of turning column names into IDs accidentally combined two different measures into one. For example, "Payment for heart attack patients (Denominator)" and "Payment for heart attack patients (Payment)" both turned into the same ID, `PAYMENT_FOR_HEART_ATTACK_PATIENTS`. This happened because an early version of the script removed the text in parentheses. It caused a duplicate ID error when loading the data. | Readmissions Reduction Program, Payment and Value of Care, Timely and Effective Care files | Fixed by keeping the text in parentheses as part of the ID. That text is often the only thing that tells two similar measures apart. |
| 7 | The HAC Reduction Program file mixed 6 number columns (Z scores) with one Yes/No column called "Payment Reduction." | Hospital_Acquired_Conditions_Reduction_Program_2022.csv | The number columns went into the shared `hospital_measures` table. The Yes/No column was moved into its own small table called `hac_payment_reduction`. Putting Yes/No into a number column would have either caused an error or turned into garbage data. |
| 8 | Hospital ID numbers only partly matched between the 2011 and 2022 files. Over 11 years, hospitals close, merge, rename, or open. | 2011 directory versus 2022 topic files | Combined using an outer join, not an inner join, so no hospital was dropped. The result includes two flags, `in_2011_data` and `in_2022_data`, so anyone using the data later can tell exactly which hospitals show up in which year. Out of 5,237 total hospitals, 4,826 appear in the 2011 data, all 5,237 appear in the 2022 data, and 4,826 appear in both. |
| 9 | Each 2022 CSV file had a leftover column called "Unnamed: 0." This was just a pandas index number left over from how the source archive was made. | All 6 2022 topic files | Removed on purpose when picking columns. This was only caught after it first showed up as a fake "measure" named `Unnamed: 0`. |
| 10 | The hospital ID number ("Provider Number" or "Facility ID") breaks quietly if read as a regular number. It drops leading zeros, so "010001" turns into "10001." | All files | Forced to be read as text and padded back to the right length with zeros. It is an ID, not a quantity, so it should never be treated as a number. |

## Design decision: one combined table instead of 8 separate tables

Each of the 8 original files works fine as its own wide table. But the most interesting questions need to compare across topics. For example: "Do hospitals that got penalized for hospital acquired conditions also have worse infection rates?" or "How did the same hospital's death rate change from 2011 to 2022?" These questions need to join across topics and years using a shared key.

If the data stayed as 8 separate wide tables, every one of these cross topic questions would need its own custom, multi step join, written by hand, against tables that are all shaped differently.

With one combined table, keyed by hospital ID and measure ID, every cross topic question follows the same basic pattern: filter the measures table by topic or year, join it to the main table, then add up the results.
