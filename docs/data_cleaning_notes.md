# Data Cleaning Notes (Simple Version, 2011 and 2026)

Source: CMS Hospital Compare / Care Compare. This is official U.S. government data. It compares the quality of care across hospitals that work with Medicare. Two time periods and 8 raw files were combined.

**2011 snapshot** (2 files):
- `hospital-data.csv`: list of hospitals (4,826 rows, 13 columns)
- `outcome-of-care-measures.csv`: death and readmission rates for heart attack, heart failure, and pneumonia (4,706 rows, 46 columns)

**2026 snapshot** (6 topic files, pulled straight from CMS's own current download page):
- `Complications_and_Deaths-Hospital_2026.csv`
- `Healthcare_Associated_Infections-Hospita_2026.csv` (infections caught in the hospital)
- `FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv`
- `Medicare_Hospital_Spending_Per_Patient-Hospital_2026.csv` (this replaced the old "Payment and Value of Care" file)
- `Timely_and_Effective_Care-Hospital_2026.csv`
- `FY_2026_HAC_Reduction_Program_Hospital.csv` (penalty program for hospital acquired conditions)

For current data, always get fresh files from data.cms.gov/provider-data/topics/hospitals.

## Problems found and how they were fixed

<table>
<colgroup>
<col style="width:6%">
<col style="width:37%">
<col style="width:15%">
<col style="width:42%">
</colgroup>
<thead>
<tr><th>#</th><th>Problem</th><th>Where it happened</th><th>How it was fixed</th></tr>
</thead>
<tbody>
<tr><td>1</td><td>Number columns were stored as text with the word "Not Available" in the 2011 file. In the 2026 files, missing data can show up as a blank cell, or as "N/A," or as "Too Few to Report." So there were several different ways of marking missing values across the two eras.</td><td>All measure files</td><td>All of these were changed into a real, empty (NULL) value using one helper function called <code>to_float()</code>. This function treats blank, "Not Available," "Not Applicable," "N/A," and "Too Few to Report" the same way.</td></tr>
<tr><td>2</td><td>CMS changed the shape of the files between the old download and the 2026 download. Most files used to be wide (one column per measure). Now, most files are already long (one row per hospital and per measure), with a clear <code>Measure ID</code> column and a <code>Score</code> column. But not all six files changed the same way, so there are now three different shapes mixed together in the same batch of files.</td><td>All 6<br>2026 topic files</td><td>Each file type is now read with its own matching rule instead of one rule for all of them. Files that are already long just get read directly. The Readmissions file is long too, but it has no <code>Measure ID</code> column, so the code uses <code>Measure Name</code> as the code instead, and pulls out 5 separate number columns per row. The HAC file is still wide, so it still gets melted into a long table the same way the 2022 files did.</td></tr>
<tr><td>3</td><td>In the old files, the hospital name and ID number were stuck together in one column, like "SOUTHEAST HEALTH MEDICAL CENTER (010001)."</td><td>Old files<br>only</td><td>Not needed anymore for the 2026 files. CMS now gives <code>Facility ID</code> and <code>Facility Name</code> as two separate columns from the start.</td></tr>
<tr><td>4</td><td>The 2026 files no longer include Hospital Type, Ownership, Number of Beds, Latitude, or Longitude. Earlier files had these columns. Now the 2026 files only include Address, City, State, ZIP Code, and Phone Number.</td><td>All 6<br>2026 topic files</td><td>These fields are simply left blank (NULL) for any hospital that only shows up in the 2026 data and not in the 2011 data. This is a real gap in what CMS now publishes, not a mistake in the cleaning. A note is printed when the script runs, and a flag column called <code>in_2026_data</code> marks which hospitals came from the 2026 files.</td></tr>
<tr><td>5</td><td>The hospital ID number ("Facility ID" or "Provider Number") breaks quietly if read as a regular number. It drops leading zeros, so "010001" turns into "10001."</td><td>All files,<br>both years</td><td>Forced to be read as text and padded back to the right length with zeros, using one shared helper function. This has to happen for both the 2011 files and the 2026 files, since both have this same kind of ID column.</td></tr>
<tr><td>6</td><td>The "Hospital Ownership" category was written in different ways for the same real category in the 2011 file, such as "Government - Local" versus "Government-Local."</td><td>2011 directory<br>only</td><td>Fixed with a clear mapping list, not a simple find and replace, so real hyphenated words like "non-profit" would not get broken apart by mistake.</td></tr>
<tr><td>7</td><td>Six wide topic files in the old data could not be queried together, since each one had its own different set of columns.</td><td>All measure<br>files</td><td>All measures from every file, wide or long, get reshaped into one long table called <code>hospital_measures</code>. Each row is one hospital paired with one measure. A separate <code>measures</code> table stores the topic and year for each measure.</td></tr>
<tr><td>8</td><td>The HAC file mixes number columns (like Z scores and SIR values) with one Yes/No column called "Payment Reduction." The 2026 version of this file also added several new date and footnote columns for each measure that were not in the old file.</td><td><code>FY_2026_HAC_<br>Reduction_Program_<br>Hospital.csv</code></td><td>The number columns went into the shared <code>hospital_measures</code> table. The Yes/No column moved into its own small table called <code>hac_payment_reduction</code>. The extra date and footnote columns are now dropped automatically by checking the end of each column name, instead of listing every column by hand.</td></tr>
<tr><td>9</td><td>Hospital ID numbers only partly match between the 2011 data and the 2026 data. Over 15 years, hospitals close, merge, rename, or open.</td><td>2011 directory<br>versus 2026<br>topic files</td><td>Combined using an outer join, not an inner join, so no hospital gets dropped. Two flags, <code>in_2011_data</code> and <code>in_2026_data</code>, mark exactly which hospitals show up in which year. In one test run: 5,455 hospitals total, 4,826 in the 2011 data, 4,789 in the 2026 data, and 4,160 in both.</td></tr>
</tbody>
</table>

## Design decision: one combined table instead of separate tables per file

Each source file works fine on its own. But the most useful questions need to compare across topics and across years. For example: "Do hospitals penalized for hospital acquired conditions also have worse infection rates?" or "How did the same hospital's death rate change from 2011 to 2026?" These questions need one shared key that connects every file together.

If the data stayed as separate wide tables, every one of these questions would need its own hand built join, written differently for each shape of table.

With one combined table, keyed by hospital ID and measure ID, every question follows the same basic pattern: filter the measures table by topic or year, join it to the main table, then add up the results. This still works even now that CMS has mixed wide and long files together in the same download, because the cleaning step already turns everything into the same shape before it reaches that combined table.
