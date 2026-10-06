# CellDeep report tool: staff guide

The tool builds a patient report from the lab PDF, the DEXA PDF(s) and the provider note. It never guesses.
Anything it cannot read or confirm is **left out** of the report, shown to you before the report is built,
and listed in the staff notes. You check those items and decide whether the report can go to the patient.

## 1. What to upload

- **Bloodwork Lab PDF**: the whole lab report (Quest or Cleveland HeartLab), scanned or digital, as received.
- **DEXA Scan PDF(s)**: the patient's DEXA file(s), as received. Pages with the name redacted are fine.
- **Provider's Consultation Note**: the structured note from the template on the upload page. It must use the
  `## ` headings (`## Consultation Note`, `## Treatment Status`, `## Patient Concerns`, `## Protocol`,
  `## Marker Targets`, `## Vitality Index`). A pasted medication list without headings is not read.

## 2. What to enter

- **Patient Name**: as on the lab report. "First name + last initial" also matches ("Evan W" matches
  "WALKER, EVAN").
- **Bloodwork Collected Date**: the "Collected" date printed on the lab report. It is required when the lab
  PDF has scanned pages, because it identifies those pages.
- **Age**: the patient's age. It is also used to check that unnamed DEXA pages belong to this patient.
- **Sex** and the **Vitality Index** answers. Leave a domain at "Not Assessed" if the patient was not asked; it is
  then taken from the provider note, or shown as not provided. Never copy answers from another patient.

## 3. The confirmation screen

If anything will be left out, the tool stops before building the report and lists each item:
a lab page it could not read, a scanned result the reads did not agree on, a test name it does not
recognize, a DEXA page it could not confirm as this patient's (with its dates and printed age or name),
or a provider note it could not read.

- **Stop, I will fix the files**: choose this if an item should be in the report (wrong file, wrong
  patient's page, missing headings in the note, wrong Collected date or age). Fix it and upload again.
- **Generate the report without them**: choose this only when you have checked every item and the
  report is still correct without it (for example, a test the clinic does not report).

## 4. Reading the STAFF CHECK (top of the staff notes)

Download the staff notes with the report and read the STAFF CHECK block first. Confirm each line:

| Line | What to check |
|---|---|
| Patient name / Collected date (entered) | They match the lab report. |
| DEXA scan dates accepted | These are this patient's scans. "(computed)" or "(estimated)" marks a body fat % that was not printed plainly. |
| DEXA EXCLUDED ... | The page really belongs to someone else, or could not be confirmed. Its scans are not in the report. |
| DEXA vs bloodwork | How old the "Where you are now" scan is. Above the limit: check whether a newer DEXA scan is missing. |
| Lab pages accepted / LAB EXCLUDED | Every lab page is accounted for. An excluded page's results are not in the report. |
| Provider note | "read". "NOT READ" means the protocol and concerns are missing from the report. |
| Vitality Index | Where each answer came from (upload form or provider note). "not provided" means the report shows the section as not provided and does not score it. Check that the answers are this patient's, especially when all seven read "No Concern". |
| Scored markers / rows excluded / unrecognized rows | Excluded and unrecognized results are listed below the block, each with its reason. |
| Name mismatches | Any source that prints a different name. |

## 5. What to do with each notice

- **INCOMPLETE - pages excluded / row excluded**: the result is not in the report. Look it up on the PDF. If the
  patient needs it, add it by hand to your discussion or fix the file and regenerate.
- **reads disagree: X / Y / Z**: the tool read the value differently each time. Read it on the PDF yourself.
- **NAME MISMATCH**: confirm the page is this patient's. If not, remove it from the PDF and regenerate.
- **DEXA SCAN OLDER THAN BLOODWORK**: ask whether there is a newer DEXA scan.
- **DEXA SCORE NOT SHOWN**: the DEXA "% optimized" is missing (no body fat %, VAT area or sex). Check the
  DEXA file and the Sex field.
- **PROVIDER NOTE REJECTED**: put the note under the `## ` headings and regenerate.
- **Unrecognized marker**: the test is not in the CellDeep library. Tell the clinic lead if it should be.
- **COVERAGE GAP**: a printed result reached neither the report nor the notes. Do not release; report it.

## 6. When NOT to release a report

- Any **COVERAGE GAP** line.
- A **NAME MISMATCH** you cannot explain, or any DEXA scan date in the report that is not this patient's.
- The patient name or Collected date in the STAFF CHECK does not match the lab report.
- A result the patient's care depends on is listed as excluded and you have not checked it on the PDF.
- The provider note was **NOT READ** and the protocol matters for this visit.
- Anything in the patient report looks different from the source PDF. The source PDF is always right.

Every report needs a named staff member to review it before it is released. The tool does not interpret
results; it only copies, checks and scores them.
