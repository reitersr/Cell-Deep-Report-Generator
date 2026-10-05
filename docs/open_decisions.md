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

> **Status: partly implemented.** "(e)" values are now kept as an `estimated` field and shown with an
> "estimated" label. Still open: whether estimated values should count toward the Structure score, and
> option C (body fat % derived from masses).

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
