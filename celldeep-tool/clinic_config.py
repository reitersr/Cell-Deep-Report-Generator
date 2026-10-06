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

# CellDeep statuses for which the lab's printed flag is shown under the current result: every status
# except "flag" (CellDeep Flagged already says it), i.e. Optimal, Moderate and None (no CellDeep tier: the
# result is not scored). A result printed as a limit already shows its flag next to the value. Remove a
# status to stop showing the line for it.
LAB_FLAG_DISAGREES_WITH = ("optimal", "moderate", None)

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

