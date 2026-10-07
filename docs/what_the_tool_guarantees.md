# What the CellDeep report tool guarantees, and what it does not

Principle: **the tool must never put a wrong number in a report.** Anything it cannot verify is left out,
listed in the staff notes and shown on the confirmation screen before the report is built. Staff review is
always required before a report is released.

## What it checks

| Source | Check | If the check fails |
|---|---|---|
| Digital lab pages | Rows are read by printed column position under a recognized table header (Current/Historical or In Range/Out of Range). Test names must match the marker library exactly (no fuzzy matching). | The page or section is excluded and listed (INCOMPLETE), with the test names it holds. Unknown test names are listed for staff. |
| Scanned lab pages | Each page is read twice by the vision model, and a third time when the two disagree. A result is kept only when at least two reads print exactly the same value, flag and range, and the value passes format and flag-vs-range checks and the printed out-of-range summary. | The row is excluded and listed with every read ("reads disagree: X / Y / Z"). A heading with no value is not a result and is never listed. |
| Duplicate results | The same test and date printed twice with different values. | The report stops (hard stop) for staff to resolve. |
| DEXA pages | Each page is read twice; a measurement is kept only when both reads agree. A page printing another name is always excluded. An unnamed page must print an age close to the other pages' ages (and to the staff-entered age), or, with no age, show only scan dates found on confirmed pages. | The measurement or page is excluded and listed with its dates, printed age or name, and reason. |
| DEXA body fat % | The printed value is used, including values marked "(e)" (shown as estimated). Computed as fat / (fat + lean) only when never printed (shown as computed). | A printed value the reads or pages disagree on is withheld, never replaced by a computed one. |
| Identity | Names are compared with the staff entry (any order, or first name plus last initial). Age comes from the printed date of birth when printed. | A NAME MISMATCH or AGE CHECK notice; disagreeing dates of birth give no age. |
| Provider note | Only the documented `## ` template sections are read. | The note (or each unread line) is listed with the reason and the required headings. |
| Completeness | Every printed result row must reach the report or the staff notes. | A COVERAGE GAP line: do not release. |
| SDK calls | Every call to the AI service is checked in CI against the exact SDK version production installs. | CI fails before deploy. |

The patient report never contains staff QA text (reasons, exclusions, read provenance). Logs never contain
patient names or result values.

## What it excludes, and why

It excludes anything it cannot verify, at the smallest unit it can: a single unreadable result cell (the rest of
its row and page is kept), unknown page layouts, scanned values the reads do not
agree on, DEXA pages it cannot confirm as this patient's, DEXA values the reads disagree on, and test names
not in the library (a readable unknown test with the lab's unit or range is shown as printed, never scored). It
never builds a report whose newest draw has no accepted result. A wrong number is worse than a missing one: a missing number is visible to staff, and a
wrong one is not.

## What it cannot do

- **No clinical interpretation.** It copies, checks and scores against the clinic's thresholds; it does not
  diagnose, advise or judge whether a result matters.
- **It cannot read what it was not given**, and it cannot tell whether a file is complete or the latest one.
- **Vision reads are not perfect.** Two reads agreeing does not make a value certain, but it is required
  before any scanned value is used. Staff should spot-check scanned values against the PDF.
- **Staff review is required for every report.** The STAFF CHECK and the confirmation screen show what to
  check; they do not replace the check.

## Known limits

- A new lab layout is excluded page by page until the parser supports it.
- An unnamed DEXA page from another person whose scan dates match the patient's could be accepted by
  date (its values are still checked against the patient's other pages).
- First name plus last initial cannot tell apart two patients with the same first name and initial.
- Scanned pages with history columns depend on the reads copying the current-draw column.
- The full list of open clinic decisions and known risks, each with a suggested test, is in
  `docs/open_decisions.md`.

## Before a wider rollout (outside the code)

- **Data-handling agreements and a compliance review** (HIPAA business associate agreements with every
  vendor that sees patient files, including the hosting provider and the AI service; data retention).
- **Individual staff logins and an audit trail.** Today there is one shared staff password and no record of
  who generated, reviewed or released a report.
- **A named person who owns each report release**, recorded with the report, and a written review procedure
  (`docs/staff_guide.md`).
- Clinic sign-off on the open decisions in `docs/open_decisions.md`, especially thresholds and labels.
- A periodic check of real reports against their source PDFs, with any difference turned into a synthetic
  test before it is fixed.
