# Scored markers in a female report (audit)

This is the clinic's review list for the first female patient type (Cleveland HeartLab, an undated scanned draw plus a dated report). It lists every marker the report scores, with the threshold it is scored against and where that threshold comes from. No patient values are listed. The report scores 40 markers.

**Where the thresholds come from.** All 40 are scored against CellDeep's own thresholds in `celldeep-tool/markers_reference.py`. None is scored against the lab's printed range. A female result with no CellDeep female threshold is shown lab-reported instead and never scored (`pipeline.apply_female_ranges`). That covers cortisol, DHEA-S, SHBG, total / free / bioavailable testosterone, and the cycle-phase hormones Estradiol, FSH, LH and Progesterone.

**Status of the female thresholds.** None of the thresholds below has been confirmed by the clinic for women. That is why `clinic_config.FEMALE_RANGES_CONFIRMED` is False, and why every female report is marked DRAFT and staff-review-only.

**Boundary rule** (fixed in this change). "<" and ">" exclude the cutoff; "≤" and "≥" include it. `test_strict_bounds.py` checks every bounded marker against the sign it prints.

**Sex-dependent?** column. ⚠ marks a marker whose normal values differ between women and men; the clinic should confirm the threshold used. Hemoglobin is sex-dependent too, but it is not scored: it is shown lab-reported (CBC) with the lab's own range and flag.

| # | Marker | System (data category) | Kind | Threshold used | Source | Sex-dependent? |
|---|---|---|---|---|---|---|
| 1 | Myeloperoxidase | Inflammation | bounded | optimal <470 | CellDeep config (`markers_reference.py`) |  |
| 2 | Lp-PLA2 Activity | Inflammation | bounded | optimal ≤123 | CellDeep config (`markers_reference.py`) |  |
| 3 | hs-CRP | Inflammation | bounded | optimal <1.0 | CellDeep config (`markers_reference.py`) |  |
| 4 | ADMA | Inflammation | bounded | optimal <100 | CellDeep config (`markers_reference.py`) |  |
| 5 | SDMA | Also Monitored | range | 73–135 | CellDeep config (`markers_reference.py`) |  |
| 6 | Oxidized LDL | Inflammation | bounded | optimal <60 | CellDeep config (`markers_reference.py`) |  |
| 7 | Total Cholesterol | Lipids | bounded | optimal <200 | CellDeep config (`markers_reference.py`) |  |
| 8 | HDL Cholesterol | Lipids | bounded | optimal ≥50 | CellDeep config (`markers_reference.py`) | ⚠ Single threshold for both sexes: optimal ≥50, moderate ≥40. Labs often use different HDL cut-offs for women and men. |
| 9 | Triglycerides | Lipids | bounded | optimal <150 | CellDeep config (`markers_reference.py`) |  |
| 10 | LDL Cholesterol | Lipids | bounded | optimal <100 | CellDeep config (`markers_reference.py`) |  |
| 11 | Non-HDL Cholesterol | Lipids | bounded | optimal <130 | CellDeep config (`markers_reference.py`) |  |
| 12 | LDL-P (particle count) | Lipids | bounded | optimal <935 | CellDeep config (`markers_reference.py`) |  |
| 13 | HDL-P | Lipids | bounded | optimal >32.8 | CellDeep config (`markers_reference.py`) |  |
| 14 | Apolipoprotein B | Lipids | bounded | optimal <90 | CellDeep config (`markers_reference.py`) |  |
| 15 | Lipoprotein(a) | Lipids | bounded | optimal <75 | CellDeep config (`markers_reference.py`) |  |
| 16 | Glucose (fasting) | Metabolic | range | 70–90 | CellDeep config (`markers_reference.py`) |  |
| 17 | HbA1c | Metabolic | bounded | optimal <5.7% | CellDeep config (`markers_reference.py`) |  |
| 18 | Estimated Average Glucose | Metabolic | bounded | optimal <117 | CellDeep config (`markers_reference.py`) |  |
| 19 | TMAO | Metabolic | bounded | optimal <6.2 | CellDeep config (`markers_reference.py`) |  |
| 20 | Insulin Resistance Score | Metabolic | bounded | sensitive <33 | CellDeep config (`markers_reference.py`) |  |
| 21 | Fasting Insulin | Metabolic | range | 2–10 | CellDeep config (`markers_reference.py`) |  |
| 22 | C-Peptide | Metabolic | bounded | optimal ≤2.16 | CellDeep config (`markers_reference.py`) |  |
| 23 | CoQ10 | Foundational | bounded | optimal >0.35 | CellDeep config (`markers_reference.py`) |  |
| 24 | Folate | Foundational | bounded | optimal >5.4 | CellDeep config (`markers_reference.py`) |  |
| 25 | Vitamin B12 | Foundational | range | 600–1000 | CellDeep config (`markers_reference.py`) |  |
| 26 | Vitamin D | Foundational | range | 60–90 | CellDeep config (`markers_reference.py`) |  |
| 27 | Fibrinogen | Inflammation | bounded | optimal <350 | CellDeep config (`markers_reference.py`) |  |
| 28 | Creatinine | Also Monitored | range | 0.50–0.97 | CellDeep config (`markers_reference.py`) | ⚠ Single range 0.50–0.97 for both sexes. This is the lab's female range; men run higher (see open_decisions.md). |
| 29 | eGFR | Also Monitored | bounded | optimal >70 | CellDeep config (`markers_reference.py`) |  |
| 30 | Phosphorus | Also Monitored | range | 2.5–4.5 | CellDeep config (`markers_reference.py`) |  |
| 31 | Magnesium | Also Monitored | range | 1.5–2.5 | CellDeep config (`markers_reference.py`) |  |
| 32 | Uric Acid | Also Monitored | range | 2.5–7.3 | CellDeep config (`markers_reference.py`) | ⚠ Sex-specific in the library (female and male variants); the female variant is used here. |
| 33 | TSH | Thyroid | range | 0.40–5.50 | CellDeep config (`markers_reference.py`) |  |
| 34 | Free T4 | Thyroid | range | 0.8–1.8 | CellDeep config (`markers_reference.py`) |  |
| 35 | Total T4 | Thyroid | range | 4.5–11.7 | CellDeep config (`markers_reference.py`) |  |
| 36 | Free T3 | Thyroid | range | 3.0–4.5 | CellDeep config (`markers_reference.py`) |  |
| 37 | Total T3 | Thyroid | range | 80–200 | CellDeep config (`markers_reference.py`) |  |
| 38 | Thyroid Peroxidase Ab | Thyroid | bounded | optimal <35 | CellDeep config (`markers_reference.py`) |  |
| 39 | Thyroglobulin Ab | Thyroid | bounded | optimal <115 | CellDeep config (`markers_reference.py`) |  |
| 40 | Ferritin | Foundational | range | 9–150 | CellDeep config (`markers_reference.py`) | ⚠ Sex-specific in the library: female 9–150 (used here), male 18–300. |

## Why one run gives 85% overall and another 89%

The overall score combines bloodwork (weight 60), the Vitality Index symptom score (30) and DEXA Structure (10). A missing domain is left out, and the remaining weights are rescaled (`scoring.overall_percent_optimized`).

- **The dry run that gave 85%:** no Vitality answers. The symptom score is left out, so overall = 60/70 × bloodwork 83 + 10/70 × Structure 96 = 84.9, shown as 85.
- **The run that gave 89%:** all seven Vitality domains "No Concern", so the symptom score is 100. Overall = 0.6 × 83 + 0.3 × 100 + 0.1 × 96 = 89.4, shown as 89.
- **Check:** re-running the dry run with all seven domains "No Concern" gives 89. The Vitality answers are the whole difference; the bloodwork and DEXA parts are the same.

The bloodwork part (83) averages six systems. Drive has no scored marker in a female report, because all its hormones are lab-reported, so it enters the average at the default 50 (open decision #11, "Systems with zero markers default to 50%"). The other five are Flow 81, Fuel 88, Pace 96, Repair 91 and Reserves 90. Without the default 50 the bloodwork part would be 89. That is a clinic decision; this change does not alter it.
