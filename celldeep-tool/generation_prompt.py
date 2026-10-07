"""
CellDeep Report Generator — Patient-Facing Copy (deterministic template fill)
=================================================================================
Every sentence that states a value, a date, or a priority is filled directly
from the reconciled, scored Marker objects (.then/.then_date_display/.now/
.now_date_display) into a fixed template. There is no model call and no
retained AI phrasing, so there is nothing that could add, drop, or choose a
fact. "Baseline"/"starting point" claims resolve via min() on normalized date;
"Next 30/90 Days" priority comes from select_priority_marker().
"""

from markers_reference import (CATEGORY_TAGLINES, DATA_TO_PATIENT_CATEGORY, MARKER_DESCRIPTIONS,
                               NARRATIVE_CATEGORY_OVERRIDE, SYSTEM_ORDER)
from schema import normalize_date_for_matching
from scoring import has_body_composition, is_censored
import clinic_config

PATIENT_SYSTEMS = ("Drive", "Pace", "Fuel", "Flow", "Repair", "Reserves")
_TIER_WORDS = {"optimal": "optimal", "moderate": "moderate", "flag": "flagged"}
_TIER_RANK = {"flag": 0, "moderate": 1, "optimal": 2}


def _date_key(date_display):
    key = normalize_date_for_matching(date_display or "")
    return key if isinstance(key, tuple) else None


def _value_text(disp, value, unit) -> str:
    text = disp if disp not in (None, "") else (f"{value:g}" if value is not None else "")
    return f"{text} {unit}" if text and unit else text


def is_retested(marker) -> bool:
    return marker.now is not None or marker.now_tier is not None or bool(marker.disp_now)


def marker_occurrences(marker) -> list[tuple[str, object, str]]:
    """Every dated reconciled reading as (date_display, value, disp_value)."""
    entries = []
    if marker.then_date_display and (marker.then is not None or marker.disp_then):
        entries.append((marker.then_date_display, marker.then, marker.disp_then))
    for entry in marker.full_history:
        if entry.get("date_display") and entry.get("disp_value"):
            entries.append((entry["date_display"], entry.get("value"), entry["disp_value"]))
    if is_retested(marker) and marker.now_date_display:
        entries.append((marker.now_date_display, marker.now, marker.disp_now))
    return entries


def baseline_occurrence(markers):
    """The earliest dated reading across one marker or a list of markers - always min(), never chosen."""
    markers = markers if isinstance(markers, (list, tuple)) else [markers]
    dated = [entry for marker in markers for entry in marker_occurrences(marker) if _date_key(entry[0])]
    return min(dated, key=lambda entry: _date_key(entry[0])) if dated else None


def marker_sentence(marker) -> str:
    unit = marker.unit
    then_text = _value_text(marker.disp_then, marker.then, unit)
    if is_retested(marker):
        now_text = _value_text(marker.disp_now, marker.now, unit)
        now_when = f" on {marker.now_date_display}" if marker.now_date_display else ""
        if then_text and marker.then_date_display:
            sentence = (f"{marker.name} moved from {then_text} on {marker.then_date_display} "
                        f"to {now_text}{now_when}.")
        else:
            sentence = f"{marker.name} measured {now_text}{now_when}."
        tier = _TIER_WORDS.get(marker.now_tier)
        return f"{sentence} That result is {tier}." if tier else sentence
    if getattr(marker, "latest_lab_reported_date", None):
        earlier = f"{marker.name} was {then_text} on {marker.then_date_display}; " if then_text and \
            marker.then_date_display else ""
        return (f"{earlier}its {marker.latest_lab_reported_date} result is shown as lab-reported, not scored."
                if earlier else f"{marker.name}'s {marker.latest_lab_reported_date} result is shown as lab-reported, "
                "not scored.")
    if then_text and marker.then_date_display:
        return (f"{marker.name} was {then_text} on {marker.then_date_display} "
                "and was not retested this round.")
    return f"{marker.name} was not retested this round."


def _system_of(marker) -> str | None:
    return NARRATIVE_CATEGORY_OVERRIDE.get(marker.name) or DATA_TO_PATIENT_CATEGORY.get(marker.category)


def _precedence(marker) -> int | None:
    if not is_retested(marker):
        return 0 if marker.then_tier == "flag" else None
    if marker.now_tier == "flag":
        return 1
    if marker.now_tier == "moderate":
        then_rank = _TIER_RANK.get(marker.then_tier)
        worsening = then_rank is not None and (
            then_rank > _TIER_RANK["moderate"]
            or (marker.then_tier == "moderate" and marker.then_pct is not None
                and marker.now_pct is not None and marker.now_pct < marker.then_pct))
        return 2 if worsening else 3
    return None


def _priority_sort_key(indexed):
    index, marker = indexed
    system_rank = SYSTEM_ORDER.index(marker.category) if marker.category in SYSTEM_ORDER else len(SYSTEM_ORDER)
    return (_precedence(marker), system_rank, index)


def _attention_markers(markers) -> list:
    """Markers needing attention, in fixed priority order (see select_priority_marker)."""
    indexed = [(index, m) for index, m in enumerate(markers) if _precedence(m) is not None]
    return [marker for _, marker in sorted(indexed, key=_priority_sort_key)]


def select_priority_marker(markers):
    """Pure, fixed precedence: flagged-and-not-retested, then flagged, then moderate-and-worsening,
    then moderate; ties broken by SYSTEM_ORDER and then record order. None when nothing qualifies."""
    attention = _attention_markers(list(markers))
    return attention[0] if attention else None


def _not_retested_markers(markers) -> list:
    return [m for m in markers if not is_retested(m) and (m.then is not None or m.disp_then)
            and not getattr(m, "latest_lab_reported_date", None)]


def _latest_lab_reported_markers(markers) -> list:
    return [m for m in markers if not is_retested(m) and getattr(m, "latest_lab_reported_date", None)]


def earlier_draw_systems(record) -> list[tuple[str, list]]:
    """[(system, [markers])] for each system whose current results include one from a draw before the headline
    (latest) draw date, in PATIENT_SYSTEMS order. Only results with a printed date are compared."""
    latest = normalize_date_for_matching(record.latest_draw_date or "")
    if not isinstance(latest, tuple):
        return []
    systems = []
    for system in PATIENT_SYSTEMS:
        earlier = [m for m in _system_markers(record, system)
                   if (m.now is not None or m.disp_now)
                   and isinstance(day := normalize_date_for_matching(m.now_date_display or ""), tuple)
                   and day < latest]
        if earlier:
            systems.append((system, earlier))
    return systems


def _system_markers(record, system) -> list:
    return [m for m in record.markers if _system_of(m) == system]


def _tier_counts(markers) -> dict[str, int]:
    return {tier: sum(1 for m in markers if m.now_tier == tier) for tier in ("optimal", "moderate", "flag")}


def _is_complete_reading(reading) -> bool:
    """A scan that can stand as the first or current body composition (scoring.has_body_composition)."""
    return has_body_composition(reading)


def body_fat_text(reading) -> str:
    """The body fat % exactly as the report shows it everywhere, with its label when the scanner marked it
    "(e)" estimated or it was computed because none was printed: '19.0% (estimated)', '22.2% (computed)'."""
    labels = [clinic_config.DEXA_ESTIMATED_LABEL] if "body_fat_pct" in (reading.estimated or []) else []
    labels += [clinic_config.DEXA_COMPUTED_LABEL] if "body_fat_pct" in (reading.computed or []) else []
    return reading.body_fat_pct + (f" ({', '.join(labels)})" if labels else "")


def _pct_number(text):
    try:
        return float(str(text).rstrip("%"))
    except (TypeError, ValueError):
        return None


def _dexa_comparison(record):
    complete = [d for d in record.dexa_history if _is_complete_reading(d)]
    if len(complete) < 2:
        return None, None
    return complete[0], complete[-1]


def _dexa_delta(record) -> str:
    first, latest = _dexa_comparison(record)
    if first is None:
        return ""
    parts = []
    if first.body_fat_pct and latest.body_fat_pct:
        parts.append(f"Body fat {body_fat_text(first)} on {first.date_display} to "
                     f"{body_fat_text(latest)} on {latest.date_display}.")
    if first.lean_mass_lb is not None and latest.lean_mass_lb is not None:
        parts.append(f"Lean mass {first.lean_mass_lb} lb to {latest.lean_mass_lb} lb.")
    return " ".join(parts)


def _structure_improved(record) -> bool:
    first, latest = _dexa_comparison(record)
    if first is None:
        return False
    before, after = _pct_number(first.body_fat_pct), _pct_number(latest.body_fat_pct)
    return before is not None and after is not None and after < before


def plural(count: int, singular: str, plural_form: str) -> str:
    """The word that agrees with count: plural('1', 'is', 'are') -> 'is'; 0 and 2+ take the plural."""
    return singular if count == 1 else plural_form


def _count_phrase(count: int, singular: str, plural_form: str) -> str:
    """'1 marker', '0 markers', '2 markers'."""
    return f"{count} {plural(count, singular, plural_form)}"


def _box_story(markers) -> str:
    if not markers:
        return "No markers in this category this round."
    attention = _attention_markers(markers) + [m for m in _not_retested_markers(markers)
                                               if _precedence(m) is None]
    if not attention:
        scored = [m for m in markers if m.now_tier is not None]
        if not scored:
            return "No scored markers in this system this round."
        if len(scored) == 1:
            return "Your 1 scored marker in this system is optimal."
        return f"All {len(scored)} scored markers in this system are optimal."
    sentences = [marker_sentence(m) for m in attention[:3]]
    if len(attention) > 3:
        more = len(attention) - 3
        sentences.append(f"{_count_phrase(more, 'more marker', 'more markers')} in this system "
                         f"{plural(more, 'needs', 'need')} attention.")
    return " ".join(sentences)


def _headline(markers) -> str:
    if not markers:
        return "No markers in this category this round."
    counts = _tier_counts(markers)
    parts = [f"{counts[tier]} {_TIER_WORDS[tier]}" for tier in ("optimal", "moderate", "flag") if counts[tier]]
    return ", ".join(parts) if parts else "Results need review."


def _protocol_reason(item, record) -> str:
    if not item.lab_visible:
        return "Not expected to show up in bloodwork."
    categories = list(item.target_categories or [])
    targets = [m for m in _attention_markers(record.markers) if _system_of(m) in categories][:3]
    if targets:
        return f"Aimed at {', '.join(m.name for m in targets)} in your {', '.join(categories)} results."
    if categories:
        return f"Supports your {', '.join(categories)} system."
    return "Part of your provider's current plan."


def build_copy(record) -> dict:
    """Fill every copy slot template.render() reads, directly from the scored record."""
    markers = list(record.markers)
    priority = select_priority_marker(markers)
    attention = _attention_markers(markers)
    not_retested = _not_retested_markers(markers)
    counts = _tier_counts(markers)

    bullets = []
    baseline = baseline_occurrence(markers)
    if baseline:
        bullets.append(f"<b>Starting point:</b> Your earliest bloodwork on file is from {baseline[0]}.")
    scored = sum(counts.values())
    if scored == 1:
        tier = next(_TIER_WORDS[t] for t in ("optimal", "moderate", "flag") if counts[t])
        bullets.append(f"<b>Where you are now:</b> Your 1 scored marker is {tier}.")
    elif scored:
        parts = [f"{counts[t]} {plural(counts[t], 'is', 'are')} {_TIER_WORDS[t]}" for t in ("optimal", "moderate", "flag")]
        bullets.append(f"<b>Where you are now:</b> Of your {scored} scored markers, {parts[0]}, {parts[1]} "
                       f"and {parts[2]}.")
    if priority:
        bullets.append(f"<b>What to focus on next:</b> {marker_sentence(priority)}")
    if not_retested:
        bullets.append(f"<b>Not retested this round:</b> {', '.join(m.name for m in not_retested)}.")
    if moved := _latest_lab_reported_markers(markers):
        bullets.append(f"<b>{clinic_config.LATEST_LAB_REPORTED_LABEL}:</b> {', '.join(m.name for m in moved)}.")
    earlier = earlier_draw_systems(record)
    if earlier:
        listed = "; ".join(f"{system}: " + ", ".join(f"{m.name} ({m.now_date_display})" for m in markers)
                           for system, markers in earlier)
        bullets.append(f"<b>{clinic_config.EARLIER_DRAW_SUMMARY_LABEL}:</b> "
                       f"{plural(len(earlier), 'This system uses', 'These systems use')} results from draws before "
                       f"{record.latest_draw_date}: {listed}.")
    censored = [m for m in markers if is_censored(m.disp_now, m.now)]
    if censored:
        bullets.append(f"<b>{clinic_config.CENSORED_SUMMARY_LABEL}:</b> "
                       f"{_count_phrase(len(censored), 'result was', 'results were')} "
                       "reported by the lab as a limit (such as &lt;0.7 or &gt;2000) rather than an exact number: "
                       f"{', '.join(m.name for m in censored)}. "
                       f"{plural(len(censored), 'It is', 'These are')} shown exactly as printed and "
                       f"{plural(len(censored), 'is', 'are')} not part of any score.")
    dexa_delta = _dexa_delta(record)
    if dexa_delta:
        bullets.append(f"<b>Body composition:</b> {dexa_delta}")

    if priority is None:
        next_30_label, next_30_sub = "Stay the course", "No flagged or moderate markers"
    elif is_retested(priority):
        next_30_label = priority.name
        next_30_sub = _value_text(priority.disp_now, priority.now, priority.unit)
        if priority.now_date_display:
            next_30_sub = f"{next_30_sub} on {priority.now_date_display}"
    else:
        next_30_label = priority.name
        next_30_sub = (f"{clinic_config.LATEST_LAB_REPORTED_LABEL}" if getattr(priority, "latest_lab_reported_date", None)
                       else f"Not retested since {priority.then_date_display}" if priority.then_date_display
                       else "Not retested this round")
    if attention:
        next_90_label = "Retest and reassess"
        next_90_sub = f"{_count_phrase(len(attention), 'marker', 'markers')} to recheck"
    else:
        next_90_label, next_90_sub = "Maintain", "All scored markers optimal"

    headlines, box_stories, box_forward = {}, {}, {}
    for system in PATIENT_SYSTEMS:
        system_markers = _system_markers(record, system)
        headlines[system] = _headline(system_markers)
        box_stories[system] = _box_story(system_markers)
        recheck = _attention_markers(system_markers)
        box_forward[system] = ("" if not system_markers else
                               f"Next 90 days: retest {', '.join(m.name for m in recheck)}." if recheck
                               else "Next 90 days: maintain your current approach.")
    latest_scan = next((d for d in reversed(record.dexa_history) if _is_complete_reading(d)), None)
    if latest_scan and latest_scan.body_fat_pct:
        headlines["Structure"] = f"Body fat {body_fat_text(latest_scan)} on {latest_scan.date_display}."
    box_stories["Structure"] = ""

    noteworthy = attention + [m for m in not_retested if m not in attention]
    marker_notes = {m.name: marker_sentence(m) for m in noteworthy}
    marker_what = {m.name: MARKER_DESCRIPTIONS[m.name] for m in noteworthy if m.name in MARKER_DESCRIPTIONS}

    return {
        "hero_question": "What if you were fully optimized by your next birthday?",
        "hero_target_line": "TARGET: FULLY OPTIMIZED BY YOUR NEXT BIRTHDAY",
        "optimization_summary_bullets": bullets,
        "headlines": headlines,
        "box_stories": box_stories,
        "box_forward": box_forward,
        "next_30_label": next_30_label,
        "next_30_sub": next_30_sub,
        "next_90_label": next_90_label,
        "next_90_sub": next_90_sub,
        "by_age_sub": "Everything, optimized",
        "category_taglines": {c: CATEGORY_TAGLINES[c] for c in {m.category for m in markers}
                              if c in CATEGORY_TAGLINES},
        "marker_notes": marker_notes,
        "marker_what": marker_what,
        "protocol_reasons": {item.name: _protocol_reason(item, record) for item in record.protocol},
        "pain_point_maintenance": {},
        "dexa_delta": dexa_delta,
        "structure_improved": _structure_improved(record),
    }

