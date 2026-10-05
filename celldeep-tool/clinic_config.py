"""Clinic-decision defaults. Every setting here is a clinical or wording choice the clinic owns.

Change a value here (in a reviewed pull request) instead of editing code; nothing in this file is
computed from patient data, and no setting may invent a value, range or status.
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
