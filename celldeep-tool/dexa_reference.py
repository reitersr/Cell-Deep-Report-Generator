# INTERIM — standard medical reference ranges, not yet CellDeep-calibrated. Flag for a future calibration pass the same way bloodwork ranges were done.

TIER_SCORES = {
    "optimal": 96,
    "suboptimal": 75,
    "borderline": 50,
    "poor": 20,
}

# ACE body-fat categories mapped to CellDeep's interim optimization tiers.
BODY_FAT_TIERS = {
    "female": (
        (0, 10, "poor", "below essential fat"),
        (10, 14, "suboptimal", "essential fat"),
        (14, 21, "optimal", "athletes"),
        (21, 25, "optimal", "fitness"),
        (25, 32, "borderline", "average"),
        (32, float("inf"), "poor", "obese"),
    ),
    "male": (
        (0, 2, "poor", "below essential fat"),
        (2, 6, "suboptimal", "essential fat"),
        (6, 14, "optimal", "athletes"),
        (14, 18, "optimal", "fitness"),
        (18, 25, "borderline", "average"),
        (25, float("inf"), "poor", "obese"),
    ),
}

# Clinical VAT area bands in square centimeters.
VISCERAL_FAT_AREA_TIERS = (
    (0, 100, "optimal", "normal"),
    (100, 160, "borderline", "borderline"),
    (160, float("inf"), "poor", "high"),
)


def _numeric(value) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def _body_fat_score(body_fat_pct, sex: str | None) -> int | None:
    value = _numeric(body_fat_pct)
    ranges = BODY_FAT_TIERS.get((sex or "").lower())
    if value is None or ranges is None or value < 0:
        return None
    for lower, upper, tier, _category in ranges:
        if lower <= value < upper:
            return TIER_SCORES[tier]
    return None


def _visceral_fat_score(visceral_fat_area) -> int | None:
    value = _numeric(visceral_fat_area)
    if value is None or value < 0:
        return None
    for lower, upper, tier, _category in VISCERAL_FAT_AREA_TIERS:
        if lower <= value < upper:
            return TIER_SCORES[tier]
    return None


def dexa_percent_optimized(body_fat_pct, visceral_fat_area, sex: str | None) -> float | None:
    """Score only DEXA metrics actually present; never infer a missing measurement."""
    scores = [
        score for score in (
            _body_fat_score(body_fat_pct, sex),
            _visceral_fat_score(visceral_fat_area),
        )
        if score is not None
    ]
    return sum(scores) / len(scores) if scores else None