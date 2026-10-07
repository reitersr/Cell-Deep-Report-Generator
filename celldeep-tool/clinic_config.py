"""Clinic-decision defaults for censored results, lab flags and range display.

Every setting here is a clinical or wording choice the clinic owns. Change a value here (in a reviewed
pull request) instead of editing code. Nothing in this file is computed from patient data, and no
setting may invent a value, range or status. test_clinic_config.py checks that every setting has its
own comment and that its wording is not duplicated in code.

Fixed rules that are NOT settings (they follow from "never infer"): a censored result is never
converted to a number, never given a CellDeep tier and never counted in any score; the lab's flag is
only ever the flag the lab printed, never computed.
"""

# ---- Censored results (a value printed as a limit, e.g. "<0.7" or ">2000") -----------------------

# Words used for the lab's own printed flag. A censored result is shown exactly as printed followed by
# " - lab flag <word>" when the lab printed a flag, e.g. ">2000 - lab flag High". Flags not listed here
# are shown as printed.
LAB_FLAG_WORDS = {"H": "High", "L": "Low", "HH": "Critical high", "LL": "Critical low"}

# Status chip shown next to a censored result in the patient report. Censored results are never
# converted to a number, never given a CellDeep tier and never included in any score.
CENSORED_CHIP_LABEL = "Reported as a limit, not scored"

# Status chip for any other result that cannot be scored as printed (e.g. a text status such as
# "SEE NOTE"). Shown as printed, no CellDeep tier.
UNSCORABLE_CHIP_LABEL = "Shown as reported, not scored"

# Label of the patient summary bullet that counts censored results separately from the scores.
CENSORED_SUMMARY_LABEL = "Reported as a limit"


# ---- Lab flag that disagrees with the CellDeep status ----------------------------------------------

# When the lab printed H or L for a result that CellDeep scores Optimal (CellDeep ranges can be wider
# or narrower than the lab's), show a small neutral line under the value, e.g.
# "Lab flag: Low (lab range 38-380)". The CellDeep status itself never changes. False hides the line;
# the staff notes list every disagreement either way.
SHOW_LAB_FLAG_WHEN_IT_DIFFERS = True

# CellDeep statuses that count as disagreeing with a printed lab H/L flag. Default: only "optimal"
# (a result the lab calls High or Low that CellDeep calls Optimal). Add "moderate" to also show the
# line when CellDeep calls the result Moderate, or None for results CellDeep does not score. The clinic
# deliberately does not show the lab flag on Moderate or unscored results (e.g. testosterone in men); see
# docs/open_decisions.md item 20. A result printed as a limit shows its flag next to the value either way.
LAB_FLAG_DISAGREES_WITH = ("optimal",)

# Wording of the line under the value. {flag} is the word from LAB_FLAG_WORDS; {lab_range} is
# " (lab range <printed range>)" when the lab printed one, otherwise empty.
LAB_FLAG_LINE = "Lab flag: {flag}{lab_range}"


# ---- Range shown for a marker without a CellDeep threshold -------------------------------------------

# When CellDeep has no threshold for a marker (see docs/ranges_audit.md), the range column shows the
# range the lab printed for that result, prefixed with this label, e.g. "lab range 1.5-9.3". The
# result is then scored against that printed range ("lab-printed fallback").
LAB_RANGE_LABEL = "lab range"

# Shown in the range column when there is no CellDeep threshold and the lab printed no range. No range
# is ever invented; the result is shown as printed and not scored.
NO_RANGE_LABEL = "no range printed"

# Status chip for that unscored case.
NO_RANGE_CHIP_LABEL = "Not scored, no range printed"


# ---- DEXA pages without a printed patient name ----------------------------------------------------

# An unnamed DEXA page is attributed to the patient only when the age printed on it is within this many
# years of the median age printed across the DEXA pages (and of the staff-entered age, when entered).
# Pages further away are excluded and listed for staff; pages that print no age are never accepted.
DEXA_AGE_TOLERANCE_YEARS = 2

# Label shown after a DEXA body fat % that the scan did not print and that is computed instead, as
# fat / (fat + lean) from the printed fat and lean mass (the scan's own definition). A printed body fat %
# is always used when one was printed.
DEXA_COMPUTED_LABEL = "computed"

# Label shown after a DEXA value the scan printed with its "(e)" estimate marker. The printed value is used
# (in the history, the first/current scan, the summary line, the headline and the score) and labelled.
DEXA_ESTIMATED_LABEL = "estimated"

# Staff notes warn when the latest accepted DEXA scan ("Where you are now") is more than this many days
# older than the latest bloodwork draw. The report still shows that scan; nothing is changed.
DEXA_STALE_DAYS = 60


# ---- Vitality Index ----------------------------------------------------------------------------------

# Shown in the Symptom / Vitality Index box when no domain has an answer on the upload form or in a read
# provider note. The box is then not scored and no domain is shown as "No Concern".
VITALITY_NOT_PROVIDED_LABEL = "Not provided"


# ---- Summary ------------------------------------------------------------------------------------------

# Label of the patient summary bullet that lists, per system, the current results taken from a draw earlier
# than the report's headline (latest) draw date, e.g. "Pace: TSH (03/02/2026)".
EARLIER_DRAW_SUMMARY_LABEL = "Results from earlier draws"


# ---- Lab assays that differ from the CellDeep range basis ------------------------------------------

# Tests a given lab measures with a different assay from the one the CellDeep range is based on. At that lab
# the result is shown as lab-reported (the lab's own range and flag) and never scored against the CellDeep
# range; the staff notes say "ASSAY DIFFERS FROM CELLDEEP RANGE BASIS". Access Medical Laboratories: free and
# bioavailable testosterone (the CellDeep ranges are based on Quest's dialysis assays).
LAB_ASSAY_DIFFERS = {"access_medical": ("Free Testosterone", "Bioavailable Testosterone")}

# The same safeguard for any other scored test: when the lab's printed range is more than this many times above
# or below the CellDeep range basis, the result is shown as lab-reported and not scored.
LAB_RANGE_BASIS_RATIO = 5


# ---- Non-fasting glucose ------------------------------------------------------------------------------

# When the lab header prints "Fasting: N", glucose is shown as "Glucose (non-fasting)" in the lab-reported
# section (the lab's range and flag, never scored against the fasting range), with this one-line note.
NON_FASTING_GLUCOSE_NOTE = "This sample was drawn without fasting, so it is shown as reported and not scored."


# "Fasting: Unknown", a blank "Fasting:" or no printed status for a draw is treated like "Fasting: N": glucose and
# fasting insulin from that draw are shown lab-reported (the lab's range and flag) with this note, never scored.
FASTING_NOT_CONFIRMED_NOTE = "Fasting was not confirmed for this sample, so it is shown as reported and not scored."

# Tests scored only from a fasting-confirmed draw, and the name each is shown under otherwise (lab-reported group).
FASTING_ONLY_MARKERS = {"Glucose (fasting)": ("Glucose (non-fasting)", "Chemistry"),
                        "Fasting Insulin": ("Insulin (non-fasting)", "Hormones")}


# ---- Assay or range change across draws ------------------------------------------------------------

# When a scored test prints a different reference range in different draws (a different assay or a new range),
# every draw is shown with its own printed range and this note. A draw whose printed range is not the CellDeep
# range basis below is shown lab-reported and never scored.
ASSAY_CHANGED_NOTE = "assay or range changed - not directly comparable"

# The printed reference range of the assay each CellDeep range is based on (Quest's dialysis assays). Tests not
# listed here fall back to LAB_RANGE_BASIS_RATIO.
CELLDEEP_RANGE_BASIS = {"Free Testosterone": "35-155 pg/mL"}


# ---- Cortisol collection time ------------------------------------------------------------------------

# Printed collection times that are placeholders, not a real time: never inside the morning window (no "(AM)").
PLACEHOLDER_COLLECTION_TIMES = ("00:01",)

# What the report shows next to a cortisol result whose draw printed a placeholder time or none.
COLLECTION_TIME_NOT_RECORDED = "time not recorded"


# ---- Latest draw guard -------------------------------------------------------------------------------

# A report is never built when the staff-entered Collected date, or the latest Collected date printed in the lab
# PDF, has no accepted result: an older draw would otherwise be shown as "now".
LATEST_DRAW_GUARD = True


# ---- Sparse panels -----------------------------------------------------------------------------------

# Shown on the report near "Your systems".
SPARSE_PANEL_NOTE = ("Systems shown reflect the markers included in this panel. Systems with no scored markers are "
                     "not shown.")


# ---- A scored test whose newest result is shown lab-reported ----------------------------------------

# Summary bullet label listing scored tests whose newest result is shown lab-reported (not fasting, other assay).
LATEST_LAB_REPORTED_LABEL = "Shown as lab-reported this round"

# Status chip on such a test's scored row (instead of "Not retested").
LATEST_LAB_REPORTED_CHIP = "See lab-reported"
