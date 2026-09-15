"""
CellDeep Report Generator — Deterministic Scoring
=====================================================
Ported directly from data.py, including every correction made during
calibration (the real-vs-provisional threshold fixes, the "no gray ever"
fix for range-kind markers). This is pure math — no AI, no judgment calls,
runs identically every time given the same numbers. Tier and percentage
are computed here, never left to the generation model to decide.
"""

from schema import Marker


def normalize_dexa_body_fat(dexa_data: dict) -> dict:
    """Derive a body-fat percentage only from already-reported mass values when needed."""
    body_fat = dexa_data.get("body_fat_pct")
    if body_fat not in (None, "", "None"):
        return dexa_data

    total_mass = dexa_data.get("total_mass_lb")
    fat_mass = dexa_data.get("fat_mass_lb")
    if total_mass in (None, "") or fat_mass in (None, ""):
        dexa_data["body_fat_pct"] = None
        return dexa_data

    try:
        total_mass = float(total_mass)
        fat_mass = float(fat_mass)
    except (TypeError, ValueError):
        dexa_data["body_fat_pct"] = None
        return dexa_data

    if total_mass == 0:
        dexa_data["body_fat_pct"] = None
        return dexa_data

    value = (fat_mass / total_mass) * 100
    dexa_data["body_fat_pct"] = f"{value:.1f}%"
    return dexa_data


def score_bounded(value: float, direction: str, optimal: float, moderate: float):
    if value is None or optimal is None or moderate is None:
        return None, "unscored"
    if direction == "lower":
        if value <= optimal:
            frac = 0 if optimal == 0 else value / optimal
            pct = 100 - frac * 12
            return max(88, round(pct)), "optimal"
        elif value <= moderate:
            span = moderate - optimal if moderate != optimal else 1
            frac = (value - optimal) / span
            pct = 88 - frac * 38
            return max(50, round(pct)), "moderate"
        else:
            over = (value - moderate) / moderate if moderate else 1
            pct = max(8, 50 - over * 90)
            return round(pct), "flag"
    else:  # higher is better
        if value >= optimal:
            pct = 88 + min(12, (value - optimal) / max(optimal, 1) * 12)
            return round(pct), "optimal"
        elif value >= moderate:
            span = optimal - moderate if optimal != moderate else 1
            frac = (value - moderate) / span
            pct = 50 + frac * 38
            return round(pct), "moderate"
        else:
            under = (moderate - value) / moderate if moderate else 1
            pct = max(8, 50 - under * 90)
            return round(pct), "flag"


def score_range(value: float, lo: float, hi: float):
    """Corrected during calibration: previously always returned 'normal' (the source of the
    'no gray ever' bug). Now buckets by distance from center, same as every other marker."""
    mid = (lo + hi) / 2
    half = (hi - lo) / 2
    if half <= 0:
        return 90, "optimal"
    dist = abs(value - mid) / half
    pct = max(58, 100 - dist * 34)
    if dist <= 0.5:
        tier = "optimal"
    elif dist <= 1.0:
        tier = "moderate"
    else:
        tier = "flag"
    return round(pct), tier


def score_categorical(is_good: bool):
    return (96, "optimal") if is_good else (55, "moderate")


def attach_scores(m: Marker, sex: str | None = None) -> None:
    """Mutates a Marker in place: sets now_tier/now_pct/then_tier/then_pct as attributes.
    (Marker is a dataclass without these fields declared, so the template/generation code
    should access them via getattr with a safe default, or this can be extended into the
    dataclass directly — kept this way so schema.py stays a pure data contract.)

    "sex" is accepted but not used for scoring here - the resolved optimal/moderate/lo/hi
    already reflect the patient's sex by the time they reach this function, per
    markers_reference.resolve_marker_config."""
    if m.kind == "bounded":
        now_pct, now_tier = score_bounded(m.now, m.direction, m.optimal, m.moderate)
        then_pct, then_tier = (score_bounded(m.then, m.direction, m.optimal, m.moderate)
                                if m.then is not None and now_tier != "unscored" else (None, None))
    elif m.kind == "range":
        now_pct, now_tier = score_range(m.now, m.lo, m.hi)
        then_pct, then_tier = (score_range(m.then, m.lo, m.hi) if m.then is not None else (None, None))
    else:  # categorical
        now_pct, now_tier = score_categorical(bool(m.is_good_now))
        then_pct, then_tier = (score_categorical(bool(m.is_good_then))
                                if m.is_good_then is not None else (None, None))

    m.now_pct, m.now_tier = now_pct, now_tier
    m.then_pct, m.then_tier = then_pct, then_tier


def category_rollup(markers_for_category: list[Marker]) -> dict:
    """Averages now_pct/then_pct across a patient-facing category's markers to get the
    box-level score, then buckets that average into optimal/moderate/flag using the same
    thresholds the box color logic uses (band: >=88 optimal, >=50 moderate, else flag)."""
    now_vals = [m.now_pct for m in markers_for_category]
    then_vals = [m.then_pct for m in markers_for_category if m.then_pct is not None]
    now_score = round(sum(now_vals) / len(now_vals)) if now_vals else 50
    then_score = round(sum(then_vals) / len(then_vals)) if then_vals else None

    def band(score):
        if score >= 88:
            return "optimal"
        if score >= 50:
            return "moderate"
        return "flag"

    now_zone = band(now_score)
    then_zone = band(then_score) if then_score is not None else None
    improved = then_zone is not None and _tier_rank(now_zone) > _tier_rank(then_zone)
    weak = [m for m in markers_for_category if m.now_tier in ("moderate", "flag")]

    # honest severity: a category's displayed color reflects the WORST individual marker inside it,
    # not just the averaged score — this was a real bug caught during calibration (a flagged marker
    # could be outranked by an average that still read as "moderate").
    weak_tiers = [m.now_tier for m in weak]
    if "flag" in weak_tiers:
        display_zone = "flag"
    elif "moderate" in weak_tiers:
        display_zone = "moderate"
    else:
        display_zone = now_zone

    return dict(now=now_score, then=then_score, now_zone=display_zone, then_zone=then_zone,
                improved=improved, weak=weak)


def _tier_rank(tier: str) -> int:
    return {"flag": 0, "moderate": 1, "optimal": 2}[tier]
