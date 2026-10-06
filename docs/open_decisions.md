# Open clinical and product decisions

Proposals only. Nothing in this document has been implemented; each item needs a clinic or owner
decision first. Evidence cites the repository as of this branch (`celldeep-tool/` paths).

| # | Decision | Owner | Blocks |
|---|---|---|---|
| 1 | Cortisol "(AM)" label for draws not known to be AM | Clinic | Accurate Cortisol label |
| 2 | Troponin T, HS: lab Moderate 6-22 vs report Flagged | Clinic | Troponin tier |
| 3 | DOB, age and "By N" wording | Clinic + owner | Hero and journey copy |
| 4 | Caveat for DEXA "(e)" estimated values | Clinic | DEXA display |
| 5 | DEXA scan-history ordering and run-to-run nondeterminism | Owner | Reproducible DEXA |
| 6 | TSH optimal band vs the clinic's revision | Clinic | TSH tier |
| 7 | Lab H/L flag that disagrees with the CellDeep tier | Clinic | Scored-marker display |
| 8 | Plain "GLUCOSE"/"INSULIN" scored as fasting markers | Clinic | Glucose/Insulin labels |
| 9 | Missing thresholds (male and female) | Clinic | Scoring coverage |
| 10 | Real patient data in git history and the DEXA prompt | Owner | De-identification |
| 11 | Systems with zero markers default to 50% | Clinic + owner | Grid scores |
| 12 | Plain "Occult Blood" outside a urinalysis section | Clinic | Section mapping |
| 13 | Unrecognized lipid-ratio, particle-size, apolipoprotein and omega-3 names | Clinic | Lab-reported coverage |
| 14 | New clinic defaults: "computed" body-fat label, DEXA age tolerance, stale-scan warning | Clinic | DEXA display and staff notes |
| 15 | Remaining places that can silently accept, drop or substitute a value | Owner + clinic | Pipeline hardening |
| 16 | Scanned bloodwork: no sampling control; 2-of-3 reads | Owner | Scan cost and certainty |
| 17 | What the confirmation screen lists | Clinic | How often staff are asked |
| 18 | "Where you are now" when the latest scan has no body composition | Clinic | DEXA display |
| 19 | Scoring estimated and computed DEXA body fat | Clinic | Structure score |

---

## 1. Cortisol "(AM)" label for non-AM draws

**Evidence.** `markers_reference.py:110-112` names the marker `Cortisol, Total (AM)` and maps the
plain printed names `cortisol`, `cortisol total` and `cortisol, total` onto it.
`markers_reference.py:302` describes it to the patient as "measured in the morning". No draw time
is captured anywhere: not on the upload form (`templates/index.html`), not in the bloodwork parser
(`pipeline.py`, `_COLLECTED_RE` reads the date only) and not in the scan schema
(`scan_bloodwork.py`, `collected` is copied but identity comes from the staff date).

**Risk.** An afternoon draw is labelled and described as a morning cortisol. Morning and evening
cortisol have different expected values. That is an inference the source does not support.

**Options.**
- A. Rename the marker to `Cortisol, Total` and drop "measured in the morning" unless AM is printed.
- B. Keep "(AM)" only when the lab prints an AM specimen label (e.g. "CORTISOL, AM") or staff tick
  "AM draw" at upload; otherwise show `Cortisol, Total` with no time claim.
- C. Ask for the draw time at upload and label accordingly.

**Proposal.** B. It matches "never infer": the label comes from print or staff, never assumed.
Cortisol also has no CellDeep threshold today (item 9), so it is scored only against the printed
lab range.

## 2. Troponin T, HS: lab Moderate 6-22 vs report Flagged

**Evidence.** `markers_reference.py:247-248`: `kind="bounded", direction="lower", optimal=6,
moderate=6, inclusive=False`. With moderate equal to optimal there is no moderate band, so any
value of 6 or more scores `flag` and renders "Flagged" (`template.py`, `bio_row_tr`). The lab
report prints three tiers, with 6-22 ng/L as Moderate. `test_marker_range_audit.py` locks
`optimal=6` for Troponin T, HS.

**Risk.** A value the lab calls Moderate is shown to the patient as Flagged (red).

**Options.**
- A. Mirror the lab tiers: `optimal=6, moderate=22` (confirm whether 22 is inclusive).
- B. Keep the stricter CellDeep rule and add patient copy explaining why it differs from the lab.

**Proposal.** A, if the clinic agrees with the lab's tiers; then update the library, the locked
test and `docs/ranges_audit.md`. Separately, generic `troponin` / `troponin t` aliases were removed
in phase 2 because a conventional troponin assay is a different test from hs-TnT; those now go to
staff review instead of being scored.

## 3. DOB, age and "By N" wording

> **Status: implemented.** Age is computed from the printed DOB and the collection date (DOB never
> stored); disagreeing DOB sources give no age and a staff notice; "By N" is the next-birthday age.

**Evidence.**
- Age is typed by staff (`app.py`, `age` form field). No date of birth is collected, and the
  parser deliberately ignores DOB lines in lab PDFs (`pipeline.py:429`, `_BIRTH_DATE_RE`).
- `generation_prompt.py:259-271` sets the hero to "What if you were fully optimized by your next
  birthday?" / "TARGET: FULLY OPTIMIZED BY YOUR NEXT BIRTHDAY".
- `template.py:847` labels the journey step `By {record.age}` (the *current* age), and
  `template.py:865` prints `AGE {age} → TARGET...`.

**Risk.** A 44-year-old reads "By 44" next to "by your next birthday" (which is 45). Age is also
not tied to a date, so it can be stale on a later re-run.

**Options.**
- A. Collect DOB at upload (staff-entered), compute age on the report date, and print
  "By {age + 1}".
- B. Keep typed age, but change the label to "By your next birthday" with no number.
- C. Drop the age-based target from the hero.

**Proposal.** B now (no new data, no arithmetic on uncertain input); A if the clinic wants the
number, with DOB staff-entered, never read from the lab PDF.

## 4. Caveat for DEXA "(e)" estimated values

> **Status: partly implemented.** "(e)" values are kept as an `estimated` field and shown with the
> `clinic_config.DEXA_ESTIMATED_LABEL` label everywhere (history, first/current scan, summary line, headline);
> a value printed identically with "(e)" on only some reads or pages is kept and shown as estimated. Option C
> is implemented as "computed" (fat / (fat + lean), labelled). Still open: whether estimated values should
> count toward the Structure score (today they do; see item 19).

**Evidence.** DEXA reports mark some values as estimated with "(e)". The DEXA extraction prompt and
schema (`extraction_prompt.py`, `dexa_history`) have no field for it, so an estimated value is
indistinguishable from a measured one. Also, `scoring.py:100-126` (`normalize_dexa_body_fat`)
*computes* body fat % from fat mass / total mass when no percentage is printed, and the template
shows that computed value exactly like a printed one.

**Risk.** Estimated or calculated numbers are presented to the patient as measured values.

**Options.**
- A. Add an `estimated` marker per DEXA field to the extraction schema, show "(e)" next to the
  value, and add the footnote "(e) estimated by the scanner".
- B. Exclude "(e)" values from scoring but still display them with the caveat.
- C. Stop deriving body fat %, or label it "calculated from fat and total mass".

**Proposal.** A + C. Both change the DEXA prompt/schema or the DEXA display, so they need explicit
approval (CLAUDE.md non-negotiable 4) and should be implemented together with item 5.

## 5. DEXA scan-history ordering and run-to-run nondeterminism

> **Status: implemented** as proposed (`scan_dexa.py`, `test_dexa_two_reads.py`).

**Evidence.**
- One Claude call reads all DEXA PDFs (`pipeline.py:1517-1534`, `_extract_dexa_with_claude`).
  There is no second read and no agreement check, unlike scanned bloodwork
  (`scan_bloodwork.read_page` + `gate_staff_identified_reads`).
- `pipeline.py:2085` keeps `dexa_history` in the model's output order. Nothing sorts it.
- `template.py:497-502` picks "first" and "latest" complete scans by list position, and
  `template.py` renders the history rows in list order.

**Risk.** Two runs on the same PDFs can produce different numbers, a different scan order, or a
different "first" / "latest" scan, so the Structure score and the DEXA panel are not reproducible.

**Proposed design (mirrors scanned bloodwork).**
1. Read the DEXA PDFs twice with the same prompt, model and API error handling (independent
   requests), each returning `dexa_history`.
2. Key each scan by its normalized printed date. A date present in only one read is excluded and
   listed for staff.
3. For each metric of a scan, keep it only when both reads agree exactly (after spacing and
   number-format normalization). A disagreeing metric becomes `None` (rendered as a dash) and is
   listed for staff with both values. Never average or pick one.
4. Sort the kept scans by normalized date (oldest first) before building `DexaReading`s, so
   "first" and "latest" never depend on model order.
5. Carry the "(e)" flag from item 4 through the same agreement rule.
6. Put a "DEXA" block at the top of the staff notes like the scanned-bloodwork block: scans kept,
   metrics excluded and why.
7. Tests: mocked double reads, golden fixtures for one- and multi-scan reports, and a test that a
   shuffled model order gives the same report.

This changes `_extract_dexa_with_claude`, so it needs explicit approval before implementation.

## 6. TSH optimal band vs the clinic's revision

**Evidence.** `markers_reference.py:143-145`: `TSH kind="range", lo=0.40, hi=5.50`, locked by
`test_marker_range_audit.py` (`"TSH": ("Thyroid", "range", None, 0.40, 5.50, None)`). Quest prints
0.40-4.50 mIU/L (see the synthetic `quest_digital` fixture). A TSH of 4.8 is therefore lab-High
but CellDeep-optimal. The clinic has indicated a revised optimal band; its values are not in the
repository.

**Proposal.** The clinic supplies the revised band (and whether it is a `range` or a tiered
`bounded` rule). Then update `markers_reference.py`, the locked test, the golden files and
`docs/ranges_audit.md` in one reviewed change. No value is guessed here.

## 7. Lab H/L flag that disagrees with the CellDeep tier

> **Status: implemented** (option C): neutral "Lab flag: ..." line, switch
> `clinic_config.SHOW_LAB_FLAG_WHEN_IT_DIFFERS` (default on), and a staff-notes line.

**Evidence.** Found by the `quest_digital` fixture (`synthetic_fixtures/golden/quest_digital.json`):
Ferritin 20, printed **L** against the lab range 38-380, scores **optimal** against the male
CellDeep range 18-300 (`markers_reference.py`, Ferritin `sex_variants`). The patient's scored
table shows the CellDeep tier only. The lab's flag is captured for lab-reported results (phase 2)
but not for scored markers, because the occurrence shape is fixed by `extraction_prompt.py`
(the DEXA contract).

**Risk.** The patient sees "Optimal" for a result their lab printed as Low, with no explanation.

**Options.**
- A. Show the lab's flag beside the scored result ("20 · lab L") in the patient table.
- B. Add a staff note whenever the lab flag and the CellDeep tier disagree.
- C. Both.

**Proposal.** C. Implement by carrying the printed flag in a side structure, not in the
`marker_occurrences` schema, so the DEXA contract stays unchanged.

## 8. Plain "GLUCOSE"/"INSULIN" scored as fasting markers

**Evidence.** `markers_reference.py`: `Glucose (fasting)` aliases include plain `glucose`;
`Fasting Insulin` aliases include plain `insulin` (added in phase 1 so Quest's printed "INSULIN"
is no longer dropped). Neither the lab print nor the upload form states that the draw was fasting.

**Risk.** A non-fasting result is labelled "fasting" and scored against fasting ranges.

**Options.**
- A. Add a staff "fasting draw" confirmation at upload. Without it, show plain Glucose/Insulin as
  lab-reported (not scored).
- B. Keep the current mapping and state in the report that fasting is assumed.

**Proposal.** A. It keeps "never infer" for the fasting state.

## 9. Missing thresholds

**Evidence.** `docs/ranges_audit.md` (generated by `ranges_audit.py`).
- Male: Cortisol, Total (AM), DHEA-S, LH, FSH **and Progesterone**.
- Female: Cortisol, DHEA-S, Estradiol, Testosterone (Total, Free, Bioavailable), LH, FSH,
  Progesterone and SHBG. Outside explicit postmenopausal BHRT these have no CellDeep threshold.

Today these are scored against the lab's printed range when one is printed, otherwise shown as
"Reference range pending". The lab-reported section (CBC, chemistry, urinalysis, ...) is never
scored.

**Proposal.** The clinic supplies thresholds per marker and sex. Each one moves from
"Lab-printed fallback" to "CellDeep-calibrated" in the audit, with a test.

## 10. Real patient data in git history and in the DEXA prompt

> **Status:** the specimen ID in `extraction_prompt.py` was replaced with a synthetic placeholder once the
> old DEXA prompt stopped being called. Git history is unchanged and still needs the owner's decision.

**Evidence.**
- Phase 0 removed or replaced, in the working tree:
  - an embedded DEXA body image with scan dates (`dexa_scan_b64.txt`), plus a zip copy of it
  - a generated code export that quoted the reference patient
  - real patient names, a specimen ID, draw/scan dates and DEXA values in tests and comments
- All of these remain in **git history** on `main`.
- `extraction_prompt.py:73` still contains a real specimen ID as an example label inside the DEXA
  extraction prompt. It was not edited, because that prompt is the DEXA request (non-negotiable 4).

**Options.**
- A. Rewrite history (`git filter-repo`) to purge those blobs, force-push `main`, and ask every
  clone to re-clone. Also review hosting caches and forks.
- B. Accept history as is, and restrict repository access.

**Proposal.** A, coordinated by the owner (it rewrites `main`). Separately, replace the specimen
ID in `extraction_prompt.py` with a synthetic placeholder in a small reviewed PR, since it changes
the DEXA prompt text.

## 11. Systems with zero markers default to 50%

**Evidence.** `celldeep-tool/README.md` ("A known open item"): a patient-facing system with no
markers falls back to a default 50% (`scoring.py:250`, `category_rollup`), and that number feeds the overall
score (`template.build_rollups`). A score for a system with no data is not a measurement.

**Proposal.** Omit systems with no scored markers from the grid and from the overall average, and
say "not tested this round". This needs a clinic decision because it changes the headline score.

## 12. Plain "Occult Blood" outside a urinalysis section

**Evidence.** `markers_reference.py`: `Urinalysis — Occult Blood` aliases include plain
`occult blood` and `urinalysis`. `test_deterministic_parsing.py` expects a CHL row printed as
"Occult Blood" with no urinalysis heading to map to it. So phase 2 kept that behavior and only
added the rule that urinalysis rows never map to serum markers.

**Risk.** A stool (fecal) occult blood result printed as plain "Occult Blood" would be shown as a
urine result.

**Proposal.** Map plain "Occult Blood" to the urinalysis marker only under a urinalysis heading,
or when the report is a known CHL urinalysis panel. Otherwise list it for staff. Confirm against
the CHL layouts the clinic receives before changing it.

## 13. Unrecognized lipid-ratio, particle-size, apolipoprotein and omega-3 names

**Evidence.** A real January panel printed these names, which match no exact alias in
`markers_reference.py` (scored) or `lab_reported.py` (shown, not scored). They reach the staff notes as
unrecognized markers, and the patient report only when the lab flagged them H/L ("Other Lab-Flagged
Results"):

| Printed name | Closest existing entry | Note |
|---|---|---|
| Chol/HDL-C | Lab-reported `Cholesterol/HDL Ratio` (aliases include "chol/hdlc ratio") | Same test? Confirm before aliasing |
| TG/HDL-C | none | Triglyceride/HDL ratio |
| LDL Size | Lab-reported `LDL Peak Size` ("ldl particle size") | Confirm it is the same measurement and unit |
| HDL Size | none | |
| VLDL | Lab-reported `VLDL Cholesterol` ("vldl cholesterol", "vldl-c") | Confirm it is cholesterol, not particle count |
| Apo A1 | none | Apolipoprotein A1 |
| ApoB/ApoA1 | none | Ratio; scored ApoB exists, the ratio does not |
| OmegaCheck | Lab-reported `OmegaCheck` (alias "omegacheck") | Plain "OmegaCheck" already matches, so the January row printed a different form (e.g. a trademark sign); record the exact printed text |
| Omega-3 total | none | Lab-reported has Omega-6 Total but no Omega-3 total |

**Decision needed.** For each name: (a) an exact alias to an existing lab-reported entry (shown with
the lab's range and flag, never scored), (b) a new lab-reported entry, or (c) a scored marker with
clinic thresholds. Nothing is aliased or scored until the clinic decides; the names stay staff-only
until then.

Already decided in this change (exact aliases to existing lab-reported, not-scored entries):
"BUN (Blood Urea Nitrogen)", "CO2 (Carbon Dioxide, Bicarbonate)", "ALP (Alkaline Phosphatase)",
"ALT (Alanine Amino Transferase)", "AST (Aspartate Amino Transferase)", and a new lab-reported
"Prolactin" entry (Hormones), since no Prolactin marker existed.

## 14. New clinic defaults: "computed" body-fat label, DEXA age tolerance, stale-scan warning

**Evidence.** `celldeep-tool/clinic_config.py` now holds three defaults chosen without a clinic decision:
- `DEXA_COMPUTED_LABEL = "computed"`: shown after a DEXA body fat % that the scan did not print and that is
  computed as fat / (fat + lean) from the printed fat and lean mass (the scan's own definition; it was fat /
  total mass before this change, which also counts bone mass and reads about 1 point lower). A printed % is
  always used when printed; a printed but contested % (reads or pages disagree) stays empty and is never
  computed.
- `DEXA_ESTIMATED_LABEL = "estimated"`: shown after a DEXA value the scan printed with "(e)".
- `DEXA_AGE_TOLERANCE_YEARS = 2`: an unnamed DEXA page is attributed to the patient only when its printed age
  is within 2 years of the median age across the pages (and of the staff-entered age). A DEXA file that
  bundles scans more than 2 years apart, with no name printed, would lose its oldest unnamed pages (listed
  under INCOMPLETE, never silently).
- `DEXA_STALE_DAYS = 60`: staff notes warn when "Where you are now" is more than 60 days older than the
  latest bloodwork.

**Decision needed.** Confirm or change the patient-facing word "computed" (or hide computed values), and
the two staff thresholds.

## 15. Remaining places that can silently accept, drop or substitute a value

Found while reviewing the whole pipeline for this change. Not fixed here (each needs a decision or its own
change); each has a suggested test.

| File / function | Failure case | Suggested test |
|---|---|---|
| `scan_bloodwork.gate_staff_identified_reads` | A scanned page that prints history columns: if both reads agree on an *earlier* column's value (e.g. a prior "<3"), it is accepted and dated with the staff-entered Collected date. The schema has no "which date column" field to check. | Mocked page whose row prints Current 3.1 and a historical "<3": both reads return "<3" → row must be excluded, not dated as the current draw. Fix: add the column's printed header/date to the scan schema and gate on it. |
| `scan_bloodwork.names_match` | First name + last initial ("Pat S") also matches a different person with the same first name and initial ("Pat Smith" vs "Pat Stone"). | Two DEXA pages printing "SMITH, PAT" and "STONE, PAT" with staff "Pat S": at most one may be accepted, the other flagged. Option: require the full last name when any page prints one. |
| `scan_dexa.attribute_pages` (rule 4) | An unnamed, no-age page from another person whose scan dates happen to equal the patient's (same clinic day) is accepted by date corroboration. | Foreign no-age page with the patient's dates but different values → today the field conflict excludes the values; add a test that such a page is flagged when every one of its values conflicts. |
| `scoring._clear_zero_sentinel_partial_scan` | Legacy sentinel handling: a mass printed as 0, or -1, becomes "not measured" without a notice (from the old AI-extraction schema; the two-read path never emits sentinels). | Two-read DEXA mock printing total 0 → value must be excluded *with* a staff note, or the sentinel code removed. |
| `template._first_complete_dexa_reading` / `_latest_complete_dexa_reading` | With no complete scan (only VAT-only rows), they fall back to `dexa_history[0]`/`[-1]`, so a partial scan is shown in "Where you are now" with dashes. | VAT-only history → the comparison block shows no body-composition values and staff notes say why. |
| `pipeline._normalize_date_for_matching` (`schema.normalize_date_for_matching`) | Two-digit years are read as 20YY: a printed DOB "03/15/82" becomes 2082 (then caught as an impossible age), and a draw "1/2/98" would sort as 2098. | Lab row and DOB with two-digit years → excluded with a notice, never a 20YY date. |
| `pipeline._attach_report_collection_dates` | Fills a missing `date_display` from the report's single collection date for a source label: a date the row itself did not print. Not called by the report path today (only `test_report_regressions.py` uses it), so it is a latent risk if re-wired. | Remove it, or add a test that the report path never calls it (any row without a date stays undated and is listed for staff). |
| `app.generate` | Age typed with decimals ("45.5") raises in `int(age)` and shows the generic "Generation failed" instead of a field error. | Post age "45.5" → form re-rendered with "Age must be a whole number". |
| `pipeline.reconcile_marker_occurrences` | A marker whose only result is older than the newest draw is shown as "Not retested" in the earlier column; correct, but staff are not told which markers were not retested in the latest draw. | Two draws, one marker only in the earlier one → STAFF CHECK lists it under "not in the latest draw". |
| `pipeline._parse_bloodwork_tables` (trend-title check, ~line 1523) | A prose line on a results page that mentions "Cumulative Summary" / "Trend Summary" / "Progress Summary" marks every row below it on that page as excluded, with no INCOMPLETE line and no coverage gap. | Quest page: TSH, Ferritin, then "Note: see Cumulative Summary for prior values", then SODIUM 128 L and HEMOGLOBIN 17.6 H → both later rows reach the report or are listed under INCOMPLETE. |
| `pipeline._parse_table_region` (~line 1319) | A row whose result is not number-shaped for `_CELL_VALUE_RE` ("3,950", "128LL", "-2.1") is skipped; for a test outside the scored library nothing reaches the staff notes. | Rows `ABSOLUTE NEUTROPHILS 3,950`, `SODIUM 128LL`, `BASE EXCESS -2.1` → each appears in `parse_exclusions` or the unrecognized list. |
| `pipeline._parse_bloodwork_row` flags (~lines 852, 1290) | "6.9 HH" keeps only "H"; Flag-column words other than H/L/High/Low ("Critical", "Abnormal") are ignored, so the patient report shows no flag. | "6.9 HH" → `lab_flag == "HH"`; an unmapped Flag-column word → staff note. |
| Units (`pipeline.py` header "Units" column ignored; `score_and_build_record` uses the library unit) | `Vitamin D 75 nmol/L` is scored and shown as 75 ng/mL; `Glucose 5.1 mmol/L` as mg/dL. | A row whose printed unit differs from the library unit is excluded and listed, never scored. |
| `reconcile_marker_occurrences` (~lines 197, 237) | Two orders a few days apart: results from the earlier order become "then" only, get no current tier and read "not retested this round". | Order A 03/02 (hs-CRP, ApoB) + order B 03/05 (TSH) → a staff note, or all treated as one round by a clinic rule. |
| `scoring.category_rollup` (see item 11) | A system with no scored current marker shows 50% and is averaged into the overall score. | Only TSH + a censored hs-CRP → systems without a current score show no % and are left out of the overall. |
| `pipeline._match_row_name` urinalysis namespace (~line 1041) | Under the heading "URINALYSIS, COMPLETE" (not exactly "URINALYSIS"), `GLUCOSE NEGATIVE` maps to fasting glucose (see item 12). | That row → lab-reported "Urinalysis — Glucose", never scored glucose. |
| `markers_reference` sex variants (~line 382) | With no sex entered, female thresholds are used (Ferritin 250 scored against 9-150) with no note. | Sex None → sex-specific markers unscored with a staff note. |
| `pipeline._is_table_prose` (~line 1271) | A long test name running into the value column is treated as prose and the row is skipped silently. | A 37-character name with "9.9 H" → parsed, or listed under INCOMPLETE. |
| `score_and_build_record` lab-range fallback (~lines 2244-2257) | With no CellDeep threshold, a current result with no usable printed range is scored against the earlier draw's range; the note does not say which draw. | LH with a range printed only on the historical draw → note names the substitution, or the marker is unscored. |
| `verify_extraction_completeness` | The Magnesium alias "mg" matches units such as "mg/L", producing a false "FOUND IN SOURCE BUT MISSING" warning on most reports (noise that trains staff to ignore warnings). | Lab page with "mg/L" units and no magnesium row → no Magnesium warning. |
| `scan_dexa.gate` / `generation_prompt` (cosmetic) | DEXA masses are stored as floats: printed "180" shows as "180.0", "12.50" as "12.5". | History keeps the printed text. |
| Environment | CI runs Python 3.11; Render uses its default (3.14 in production). `flask`, `playwright`, `PyMuPDF` are unpinned, so a deploy can pick up versions CI never ran. | Pin Python on Render (`PYTHON_VERSION`) and in CI to the same version, and pin the remaining packages. |

## 16. Scanned bloodwork: no sampling control; 2-of-3 reads

**Evidence.** The SDK production installs (`anthropic==1.5.0`) accepts no `temperature`, `top_p` or `top_k`
and has no seed (`test_sdk_contract.py` checks every argument against it). Newer models reject sampling
parameters at the API too, and `temperature=0` never guaranteed identical output. Passing it through
`extra_body` would bypass the SDK check and break on the next model change, so it is not used.
`scan_bloodwork.py` now reads each scanned page twice and, when the two reads disagree on a result row, a
third time (`SCAN_MAX_READS = 3`); a row is kept when at least two reads print exactly the same value, flag
and range (`SCAN_AGREEMENT_READS = 2`).

**Risk.** (a) Two of three reads can agree on the same wrong value; this is less likely than one read being
wrong, but not impossible. (b) A page with any disagreement costs a third model call (about 50% more for
that page).

**Options.** A. Keep 2-of-3 (current). B. Require all three reads to agree after a disagreement (fewer
values kept, more listed for staff). C. Read every page three times always (cost) and require 2-of-3.

**Proposal.** A, with staff spot-checks of scanned values (`docs/staff_guide.md`). Revisit with real
disagreement counts from the staff notes after a month of use.

## 17. What the confirmation screen lists

**Evidence.** `pipeline.preflight_items` / `note_preflight` list every excluded lab page or section, every
excluded scanned result row, DEXA pages not attributed to the patient, unreadable DEXA pages, printed results
whose test name is not recognized (one line naming them) and a provider note that was not read (or read in
part). Headings without a value never count. On the synthetic runs (`test_scenarios.py`): 0 items for the
clean CHL, scanned and mixed reports; 1 for the Quest and Labcorp layouts (each has one unrecognized test
name); 1 for the noisy scan (one 3-way disagreement); 1 for the clinic DEXA file (the foreign page);
3 for the variant layout.

**Risk.** Real Quest reports often print tests the library does not know (item 13), so the screen may appear
on most Quest runs, and staff may start clicking through it.

**Options.** A. Keep listing unrecognized tests (current). B. List them only when the lab flagged them H/L.
C. Keep a clinic-maintained list of tests that are deliberately not reported, and stop listing those.

**Proposal.** C, once the clinic has gone through item 13.

## 18. "Where you are now" when the latest scan has no body composition

**Evidence.** "When you came in" and "Where you are now" are the first and latest accepted scans with a body
fat % or total/fat/lean mass (`scoring.has_body_composition`). A later VAT-only scan is shown in the history
but not as "Where you are now". When no scan has body composition, the template falls back to the first/last
scan and shows dashes (item 15).

**Decision needed.** Confirm that a VAT-only follow-up should never be "Where you are now".

## 19. Scoring estimated and computed DEXA body fat

**Evidence.** `template.build_rollups` scores the Structure system from the current scan's body fat % and VAT
area. An "(e)" body fat or a computed body fat now counts (before this change, an "(e)" value marked on only
one page was withheld, so Structure was scored from VAT alone: on the synthetic clinic file the DEXA score went
from 96% to 73%). The patient report shows the value with its label.

**Options.** A. Score them (current). B. Show them but leave them out of the score, with a staff note.

**Decision needed.** Clinic.

