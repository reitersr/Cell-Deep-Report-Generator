"""
CellDeep Report Generator — Template Renderer
=================================================
Ported from build_final_v7.py (the locked V23 reference). CSS is copied
VERBATIM — every value, every rule, unchanged, because that is the locked
design and nothing about it is open to variation per patient. What changed
is every place that used to read a reference-patient module-level constant now
reads from the PatientRecord and the generated copy dict passed into render().

This file has not been run end-to-end (no live API access during
development — see pipeline.py). The first real test should be running the
full pipeline against the reference patient's source material and confirming the
output matches V23 pixel for pixel, since that's the one case we already
know the correct answer to.
"""

import math
import base64
import os
from datetime import datetime
from playwright.sync_api import sync_playwright

from schema import PatientRecord, DexaReading
import scoring
from dexa_reference import dexa_percent_optimized
from markers_reference import DATA_TO_PATIENT_CATEGORY, NARRATIVE_CATEGORY_OVERRIDE
from unknown_marker_policy import STAFF_NOTE_MARKERS
from html import escape as html_escape
import clinic_config

AQUA = "#81CADF"
AQUA_DK = "#3E7C93"
CHARCOAL = "#373436"
MIDGRAY = "#AAAAAC"
CREAM = "#D7D0C2"
DARKGRAY = "#5A5A5A"
INK = CHARCOAL
MUTE = DARKGRAY
LINE = "#E4DFD5"
GREEN = "#6FA287"
YELLOW = "#D3A84B"
RED = "#B75B4E"
TIER_COLOR = {"optimal": GREEN, "moderate": YELLOW, "flag": RED}
TIER_ORDER = {"optimal": 2, "moderate": 1, "flag": 0}

# patient-facing category -> icon glyph name (matches render_common.icon_svg's expected keys)
PATIENT_CATEGORY_SUB = {
    "Drive": "Hormones", "Pace": "Thyroid", "Fuel": "Metabolic & Insulin",
    "Flow": "Lipids & Cardiovascular", "Repair": "Inflammation", "Reserves": "Vitamins & Minerals",
    "Structure": "Body Composition",
}


def fmt(value, dash="—"):
    if value is None or value == "" or value == "None":
        return dash
    return value


def fmt_date(date_str):
    if date_str in (None, "", "None"):
        return ""
    if isinstance(date_str, datetime):
        return date_str.strftime("%B %-d, %Y")
    text = str(date_str).strip()
    for fmt_string in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y/%m/%d",
        "%m-%d-%Y",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt_string).strftime("%B %-d, %Y")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).strftime("%B %-d, %Y")
    except ValueError:
        return text


def parse_date_value(date_str):
    text = str(date_str).strip()
    for fmt_string in (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%Y/%m/%d", "%m-%d-%Y",
    ):
        try:
            return datetime.strptime(text, fmt_string)
        except ValueError:
            pass
    return datetime.fromisoformat(text)


def fmt_patient_name(raw):
    if raw in (None, ""):
        return "Patient"
    name = str(raw).strip()
    if not name:
        return "Patient"
    if "," in name:
        last, first = (part.strip() for part in name.split(",", 1))
        name = " ".join(part for part in (first, last) if part)
    return " ".join(part.capitalize() for part in name.split())


def _join_sentences(*parts: str) -> str:
    """Join sentence fragments without creating duplicate terminal punctuation."""
    cleaned = []
    for part in parts:
        text = (part or "").strip()
        if text:
            cleaned.append(text.rstrip(" ."))
    if not cleaned:
        return ""
    return ". ".join(cleaned) + "."


def _load_logo_traced_path() -> str | None:
    candidate = os.path.join(os.path.dirname(__file__), "logo_traced_path.txt")
    if os.path.exists(candidate):
        with open(candidate, "r", encoding="utf-8") as f:
            value = f.read().strip()
            return value or None
    return None


def icon_svg(category: str, size: int) -> str:
    """Minimal category glyphs — ported from render_common.py's established set."""
    glyphs = {
        "Drive": '<path d="M6 14 L10 6 L14 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "Pace": '<path d="M3 10 Q6 6 10 10 T17 10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
        "Fuel": '<path d="M10 3 L10 13 M6 9 L10 13 L14 9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "Flow": '<path d="M3 11 Q6 5 8 11 T13 11 T18 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
        "Repair": '<path d="M10 3 L12 8 L17 8 L13 11 L15 16 L10 13 L5 16 L7 11 L3 8 L8 8 Z" fill="currentColor"/>',
        "Reserves": '<rect x="7" y="3" width="6" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><line x1="7" y1="8" x2="13" y2="8" stroke="currentColor" stroke-width="2"/>',
        "Structure": '<rect x="3" y="9" width="14" height="2" rx="1" fill="currentColor"/>',
    }
    body = glyphs.get(category, glyphs["Repair"])
    return f'<svg width="{size}" height="{size}" viewBox="0 0 20 20">{body}</svg>'


TRACED_PATH_FALLBACK = None  # set via render(logo_path=...) if a real traced logo path is supplied


def logo_svg(fill: str, traced_path: str | None) -> str:
    if not traced_path:
        # simple hourglass fallback if no traced logo path is supplied to render()
        return (f'<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">'
                f'<circle cx="50" cy="50" r="46" fill="none" stroke="{fill}" stroke-width="4"/>'
                f'<path d="M32 30 L68 30 L50 50 L68 70 L32 70 L50 50 Z" fill="none" stroke="{fill}" stroke-width="4"/></svg>')
    return f'<svg viewBox="0 0 300 300" xmlns="http://www.w3.org/2000/svg"><path d="{traced_path}" fill="{fill}" fill-rule="evenodd"/></svg>'


CHECK = '<svg viewBox="0 0 20 20" width="13" height="13"><path d="M4 10.5 L8 14.5 L16 5.5" fill="none" stroke="#fff" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
CHECK_SM = (f'<span style="display:inline-flex; width:12px; height:12px; border-radius:50%; background:{GREEN}; '
            f'align-items:center; justify-content:center; vertical-align:middle;">'
            f'<svg viewBox="0 0 20 20" width="8" height="8"><path d="M4 10.5 L8 14.5 L16 5.5" fill="none" '
            f'stroke="#fff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>')


def gauge_svg(then_score, now_score, now_zone, w=150, h=86, num_size=30):
    cx, cy, r = w / 2, h - 8, 58
    def pt(score, radius=r):
        ang = math.radians(180 - (score / 100) * 180)
        return cx + radius * math.cos(ang), cy - radius * math.sin(ang)
    def arc_path(s0, s1, radius, color, width=16):
        pts = []
        n = 24
        for i in range(n + 1):
            s = s0 + (s1 - s0) * i / n
            pts.append(pt(s, radius))
        d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="butt"/>'
    bands = [(0, 50, RED), (50, 88, YELLOW), (88, 100, GREEN)]
    arcs = "".join(arc_path(s0, s1, r, c) for s0, s1, c in bands)
    nx, ny = pt(now_score, r - 3)
    needle_color = TIER_COLOR[now_zone]
    then_html = ""
    if then_score is not None:
        tx, ty = pt(then_score, r + 10)
        then_html = f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="3.2" fill="none" stroke="{MUTE}" stroke-width="1.8"/>'
    return f'''<svg width="{w}" height="{h+8}" viewBox="0 0 {w} {h+8}">
      {arcs}
      {then_html}
      <line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="{needle_color}" stroke-width="3.5" stroke-linecap="round"/>
      <circle cx="{cx}" cy="{cy}" r="5" fill="{needle_color}"/>
      <text x="{cx}" y="{cy-16}" text-anchor="middle" font-family="Georgia,serif" font-weight="700" font-size="{num_size}" fill="{INK}">{now_score}%</text>
    </svg>'''


# ---- CSS: verbatim from V23. Do not edit values here without going back through calibration. ----
CSS = f'''
/* ============================================================
    CELLDEEP LAYOUT FRAMEWORK — LOCKED RULES
    8. Any section whose underlying data is absent for a given
        patient must be fully omitted from output - no empty card,
        no reserved blank space, no placeholder. Applies to DEXA,
        pain points, protocol, and any marker category, for any
        patient regardless of data completeness.
    ============================================================ */
@page {{ size: Letter; margin: 0.15in 0in 0.15in 0in; }}
*{{box-sizing:border-box; margin:0; padding:0;}}
body{{font-family:'Helvetica Neue',Arial,sans-serif; color:{INK}; font-size:14px; line-height:1.55; background:#fff; zoom:0.96;}}
.page{{width:8.5in; padding:0 0.6in;}}
h1,h2,h3{{font-family:Georgia,'Times New Roman',serif; font-weight:700;}}
.draft-note{{color:{MIDGRAY}; font-size:10px; margin-bottom:14px;}}
.masthead{{display:flex; align-items:center; justify-content:space-between; margin-bottom:4px; border-bottom:2px solid {INK}; padding-bottom:4px;}}
.brand-row{{display:flex; align-items:center; gap:13px;}}
.brand-row svg{{width:38px; height:38px;}}
.brand-row .brand-name{{font-size:19px; font-weight:800; letter-spacing:0.13em;}}
.meta{{text-align:right;}}
.meta .eyebrow{{font-size:10px; font-weight:700; letter-spacing:0.1em; color:{MUTE}; text-transform:uppercase; margin-bottom:4px;}}
.meta .pname{{font-family:Georgia,serif; font-size:18px; font-weight:700;}}
.meta .ddates{{font-size:11px; color:{MUTE}; margin-top:3px;}}
.sec-title{{font-size:11px; font-weight:700; letter-spacing:0.1em; color:{INK}; text-transform:uppercase; margin:8px 0 5px; display:flex; align-items:center; gap:8px;}}
.sec-title::after{{content:""; flex:1; height:2.5px; background:{AQUA};}}

.hero{{border:1.5px solid {AQUA}; border-radius:12px; padding:8px 14px; margin-bottom:4px; box-shadow:0 0 0 1px {AQUA}33 inset;}}
.hero-eyebrow{{font-size:10.5px; font-weight:700; letter-spacing:0.08em; color:{AQUA_DK}; margin-bottom:7px;}}
.bottomline{{background:{CHARCOAL}; color:#F1EEE7; border-radius:9px; padding:4px 14px; margin-bottom:2px;}}
.bl-eyebrow{{font-size:10px; font-weight:700; letter-spacing:0.08em; color:{AQUA}; text-transform:uppercase; margin-bottom:7px;}}
.bottomline p{{font-size:11.5px; line-height:1.42; color:#EDEAE2;}}
.bl-list{{list-style:none; margin:0; padding:0;}}
.bl-list li{{font-size:10.5px; line-height:1.32; color:#EDEAE2; padding-left:13px; position:relative; margin-bottom:3px;}}
.bl-list li::before{{content:"\\2022"; position:absolute; left:0; color:{AQUA};}}
.bl-list li b{{color:#fff;}}
.hero-top{{display:flex; justify-content:space-between; gap:16px; margin-bottom:8px; align-items:center;}}
.hero-top .big{{font-family:Georgia,serif; font-size:22px; font-weight:700; max-width:5.1in; line-height:1.28;}}
.hero-ring{{flex:none;}}
.gauge-legend{{display:none;}}
.gauge-legend span{{display:flex; align-items:center; gap:5px;}}
.gdot{{display:inline-block; width:7px; height:7px; border-radius:50%; border:1.8px solid {MUTE}; background:#fff;}}
.gneedle{{display:inline-block; width:7px; height:7px; border-radius:50%; background:{AQUA_DK};}}
.color-legend{{font-size:9px; color:{MUTE}; margin-top:4px; padding-top:5px; border-top:1px solid {LINE};}}
.journey-row{{display:flex; gap:0; border-top:1.5px solid {LINE}; margin-top:4px; padding-top:6px;}}
.jstep{{flex:1; padding-right:14px; border-right:1.5px solid {LINE};}}
.jstep:last-child{{border-right:none; padding-right:0;}}
.jstep .lbl{{font-size:9.5px; font-weight:700; letter-spacing:0.06em; color:{MUTE}; text-transform:uppercase; margin-bottom:4px;}}
.jstep .val{{font-family:Georgia,serif; font-size:16px; font-weight:700; line-height:1.2;}}
.jstep.going .val{{color:{AQUA_DK};}}
.jstep .sub{{font-size:10px; color:{MUTE}; margin-top:2px;}}

.dexa-panel{{border-radius:11px; padding:7px 14px; margin-bottom:7px; position:relative; overflow:hidden; color:{INK};
    background:#fff; border:2px solid {AQUA};}}
.dexa-top{{position:relative; z-index:2; display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;}}
.dexa-eyebrow{{font-size:10.5px; letter-spacing:0.12em; color:{AQUA_DK}; font-weight:700;}}
.dexa-title{{font-family:Georgia,serif; font-size:19px; font-weight:700; color:{INK}; margin-top:4px;}}
.dexa-badge{{width:26px; height:26px; border-radius:50%; background:{GREEN}; display:flex; align-items:center; justify-content:center; border:2.5px solid #fff; box-shadow:0 0 0 1.5px {GREEN};}}
.dexa-body{{display:flex; align-items:center; gap:14px; position:relative; z-index:2; margin-top:4px;}}
.dexa-figure{{flex:none; display:flex; justify-content:center; width:220px;}}
.dexa-scan-img{{width:220px; max-height:220px; object-fit:contain; border-radius:7px; border:1px solid {LINE};}}
.dexa-history{{margin-top:5px; padding-top:5px; border-top:1px solid {LINE}; position:relative; z-index:2; break-inside:avoid; page-break-inside:avoid;}}
.dexa-history-title{{font-size:9px; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; color:{MUTE}; margin-bottom:5px;}}
.dexa-hist-row{{display:flex; gap:10px; font-size:9px; color:{DARKGRAY}; padding:1.5px 0;}}
.dexa-hist-row .d{{flex:0 0 1.0in; font-weight:700; color:{INK}; font-size:10.5px;}}
.dexa-hist-row .v{{flex:0 0 0.92in;}}
.dexa-stat-block{{flex:1; display:flex; flex-direction:column; gap:2px;}}
.dexa-row-lbl{{font-size:11.5px; letter-spacing:0.05em; text-transform:uppercase; color:{MUTE}; margin-bottom:5px; font-weight:700;}}
.dexa-row-lbl.bright{{color:{AQUA_DK};}}
.dexa-row-stats{{display:flex; gap:22px;}}
.dexa-row-stats.dim .num{{color:{MIDGRAY}; font-size:18px;}}
.dexa-row-stats.dim .cap{{color:{MIDGRAY};}}
.dexa-stat .num{{font-family:Georgia,serif; font-size:19px; font-weight:700; color:{AQUA_DK};}}
.dexa-stat .cap{{font-size:9px; color:{MUTE}; margin-top:2px; letter-spacing:0.03em; text-transform:uppercase;}}
.dexa-delta{{font-family:Georgia,serif; font-size:13px; font-weight:700; color:{INK}; margin-top:6px; padding:6px 12px;
  background:{AQUA}14; border-left:4px solid {AQUA}; border-radius:4px; position:relative; z-index:2;}}
.dexa-note{{font-size:9px; color:{DARKGRAY}; line-height:1.25; margin-top:3px; position:relative; z-index:2; max-width:6.2in;}}
.dexa-quote{{margin-top:4px; padding-top:4px; border-top:1px solid {LINE}; font-size:10px; font-style:italic; color:{AQUA_DK}; position:relative; z-index:2;}}
.dexa-quote .lbl{{font-style:normal; font-size:9px; color:{MUTE}; text-transform:uppercase; letter-spacing:0.04em;}}

.grid{{margin-bottom:3px;}}
.grid-row{{display:flex; gap:7px; margin-bottom:5px;}}
.grid-row .box{{flex:1; min-width:0;}}
.box{{border:1.5px solid var(--c); border-top:4px solid var(--c); border-radius:9px; padding:7px 12px 8px; background:var(--c-bg); position:relative; overflow:hidden; break-inside:avoid; page-break-inside:avoid;}}
.box-top{{display:flex; align-items:center; gap:9px; position:relative; z-index:2;}}
.box-icon{{width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center; flex:none; background:#fff; color:var(--c); border:2px solid var(--c);}}
.box-titles{{flex:1;}}
.box-name{{font-family:Georgia,serif; font-size:17px; font-weight:700; line-height:1.1;}}
.box-sub{{font-size:10.5px; color:{MUTE};}}
.box-score{{font-family:Georgia,serif; font-size:23px; font-weight:700; flex:none;}}
.box-badge{{width:22px; height:22px; border-radius:50%; background:{GREEN}; display:flex; align-items:center; justify-content:center; border:3px solid #fff; box-shadow:0 0 0 1.5px {GREEN}; flex:none; margin-left:3px;}}
.box-why{{font-size:10.5px; color:{INK}; line-height:1.36; margin-top:5px; position:relative; z-index:2;}}
.box-why b{{font-weight:700;}}
.box-quote{{font-size:9.5px; font-style:italic; color:{AQUA_DK}; margin-top:4px; padding-top:4px; border-top:1px dashed {LINE}; position:relative; z-index:2;}}
.box-quote-lbl{{font-style:normal; font-size:8.5px; color:{MUTE}; text-transform:uppercase; letter-spacing:0.03em;}}
.box-forward{{font-size:9px; color:{AQUA_DK}; font-weight:700; margin-top:4px; padding-top:4px; border-top:1px dashed {LINE}; position:relative; z-index:2;}}
.box-tierword{{font-size:10.5px; font-weight:800; letter-spacing:0.03em;}}
.box-proto{{color:{AQUA_DK}; font-weight:600;}}
.box-mark{{position:absolute; right:-16px; bottom:-18px; width:76px; height:76px; opacity:0.06; z-index:1;}}
.vitality-score-missing{{font-family:Georgia,serif; font-size:14px; font-weight:700; color:{MUTE}; text-align:right; max-width:1.25in; line-height:1.15;}}
.vitality-domains{{display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:2px 18px; margin-top:7px; position:relative; z-index:2;}}
.vitality-domain{{display:flex; justify-content:space-between; gap:8px; font-size:10px; line-height:1.3; border-top:1px solid {LINE}; padding-top:2px;}}
.vitality-domain .status{{font-weight:700; white-space:nowrap;}}

.protocol-block{{border-left:3px solid var(--c); padding-left:9px; margin:4px 0 5px; break-inside:avoid; page-break-inside:avoid;}}
.protocol-heading-unit{{break-inside:avoid; page-break-inside:avoid;}}
.protocol-block .pname{{font-size:12.5px; font-weight:700;}}
.protocol-block .pcadence{{font-size:10px; color:{MUTE}; font-weight:400;}}
.protocol-block .preason{{font-size:10px; color:#4c4744; margin-top:2px; line-height:1.32;}}

.nonlab-box{{background:{CREAM}55; border:1.5px dashed {MIDGRAY}; border-radius:9px; padding:7px 12px; margin-top:3px; break-inside:avoid; page-break-inside:avoid;}}
.nonlab-box .t{{font-size:9.5px; font-weight:700; letter-spacing:0.05em; color:{MUTE}; text-transform:uppercase; margin-bottom:5px;}}
.nonlab-item{{font-size:11px; margin-bottom:3px;}}
.nonlab-item b{{font-weight:700;}}
.nonlab-item span{{color:#4c4744;}}

.footer-note{{font-size:8.5px; color:{MUTE}; margin-top:5px; max-width:6.9in; line-height:1.35; border-top:1px solid {LINE}; padding-top:5px;}}
.legend{{display:flex; gap:16px; font-size:10.5px; color:#4c4744; margin:2px 0 9px;}}
.legend .sw{{width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:6px;}}
.bio-group{{margin-bottom:5px;}}
.bio-group-title{{font-family:Georgia,serif; font-size:13.5px; font-weight:700; display:flex; align-items:baseline; gap:10px;
    border-bottom:2px solid {INK}; padding-bottom:4px; margin-bottom:3px;}}
.bio-category-start > td{{padding:0;}}
.bio-group-title .link{{font-size:9.5px; color:{MUTE}; font-weight:400; text-transform:uppercase; letter-spacing:0.03em;}}

.bio-table{{width:100%;}}
.bio-table-row{{display:grid; grid-template-columns:34% 22% 22% 22%;}}
.bio-table-header{{font-size:8.5px; font-weight:700; letter-spacing:0.04em; text-transform:uppercase; color:{MUTE};
    padding:5px 8px 5px 0; border-bottom:1.5px solid {INK};}}
.bio-table-header > div:nth-child(n+3){{text-align:center;}}
.bio-tr{{break-inside:avoid; page-break-inside:avoid;}}
.bio-tr > div{{padding:6px 8px 6px 0; border-bottom:1px solid {LINE}; vertical-align:middle;}}
.bio-tr .td-name{{border-left:3px solid var(--c); padding-left:8px;}}
.bio-name{{font-size:12.5px; font-weight:700; line-height:1.15;}}
.bio-tierchip{{display:inline-block; font-size:8px; font-weight:700; letter-spacing:0.03em; padding:2px 7px; border-radius:10px; vertical-align:middle; margin-left:3px;}}
.td-range{{font-size:9.5px; color:{MUTE};}}
.td-then, .td-now{{text-align:center;}}
.bio-pill{{display:inline-block; font-weight:800; font-size:13px; padding:4px 12px; border-radius:20px;}}
.bio-dash{{color:{MIDGRAY}; font-size:13px;}}
.bio-unit{{font-size:9px; color:{MUTE}; margin-left:3px; display:block; margin-top:1px;}}
.bio-date{{display:block; font-size:8px; color:{MUTE}; margin-top:2px; line-height:1.1;}}
.lab-pill{{background:{MIDGRAY}22; color:{INK};}}
.lab-flag{{font-weight:700; margin-left:3px;}}
.dexa-est{{font-size:8px; font-weight:600; color:{MUTE}; text-transform:lowercase; margin-left:2px;}}
.also-on-file{{font-size:8.5px; color:{MUTE}; margin-top:2px; line-height:1.25;}}
.lab-flag-line{{display:block; font-size:8.5px; color:{MUTE}; margin-top:2px; line-height:1.2;}}
.lab-flag-words{{font-size:10px; color:{MUTE}; font-weight:600;}}
.bio-note{{font-size:9.5px; color:{DARKGRAY}; line-height:1.3; font-style:italic; max-width:6.2in;}}
'''


# ---- rollup logic: identical math to V23, now driven by record.markers instead of module-level M ----

def markers_for_category(record: PatientRecord, patient_cat: str):
    data_cat = [k for k, v in DATA_TO_PATIENT_CATEGORY.items() if v == patient_cat]
    data_cat = data_cat[0] if data_cat else None
    rows = [m for m in record.markers if m.category == data_cat]
    # apply the locked narrative override (Lp-PLA2 speaks to Flow, not Repair)
    rows = [m for m in rows if NARRATIVE_CATEGORY_OVERRIDE.get(m.name, patient_cat) == patient_cat
            or (m.category == data_cat and NARRATIVE_CATEGORY_OVERRIDE.get(m.name) is None)]
    override_rows = [m for m in record.markers if NARRATIVE_CATEGORY_OVERRIDE.get(m.name) == patient_cat
                      and m.category != data_cat]
    return rows + override_rows


def build_rollups(record: PatientRecord, structure_now: int | None, structure_then: int | None,
                   structure_improved: bool):
    patient_cats = [c for c in DATA_TO_PATIENT_CATEGORY.values()]
    roll = {}
    for cat in patient_cats:
        rows = markers_for_category(record, cat)
        roll[cat] = scoring.category_rollup(rows)
    has_dexa = bool(record.dexa_history)
    if has_dexa:
        # a trailing VAT-only recheck scan (no body-fat/mass data) must never be picked as the
        # "now"/"then" snapshot - same completeness guard dexa_panel() already applies for display
        first_dexa = _first_complete_dexa_reading(record.dexa_history)
        latest_dexa = _latest_complete_dexa_reading(record.dexa_history)
        structure_now = dexa_percent_optimized(
            latest_dexa.body_fat_pct, latest_dexa.visceral_fat_area_cm2, record.sex
        )
        structure_then = dexa_percent_optimized(
            first_dexa.body_fat_pct, first_dexa.visceral_fat_area_cm2, record.sex
        ) if first_dexa is not latest_dexa else None
        structure_zone = ("optimal" if structure_now is not None and structure_now >= 88
                          else "moderate" if structure_now is not None and structure_now >= 50
                          else "flag")
        roll["Structure"] = dict(now=structure_now, then=structure_then,
                                  now_zone=structure_zone,
                                  then_zone=None, improved=structure_improved, weak=[])
    grid_cats = patient_cats  # Structure is never in the uniform grid — same rule as V23
    order = sorted(grid_cats, key=lambda c: roll[c]["now"])
    bloodwork_now = round(sum(roll[c]["now"] for c in grid_cats) / len(grid_cats))
    symptom_now = scoring.symptom_percent_optimized(record.vitality_index)
    overall_now = round(scoring.overall_percent_optimized(bloodwork_now, symptom_now, structure_now))
    then_vals = [roll[c]["then"] for c in grid_cats if roll[c]["then"] is not None]
    bloodwork_then = round(sum(then_vals) / len(then_vals)) if then_vals else None
    # no concept of a historical Vitality Index snapshot exists in this codebase - a baseline
    # note's vitality answers are never captured apart from the current one, so this is always
    # None, which overall_percent_optimized already treats as the missing-Symptom case
    symptom_then = None
    overall_then = (round(scoring.overall_percent_optimized(bloodwork_then, symptom_then, structure_then))
                     if bloodwork_then is not None else None)
    return roll, order, overall_now, overall_then, has_dexa


def box_html(patient_cat, roll, copy, record, logo_mark):
    r = roll[patient_cat]
    weak_tiers = [m.now_tier for m in r["weak"]]
    if "flag" in weak_tiers:
        display_zone = "flag"
    elif "moderate" in weak_tiers:
        display_zone = "moderate"
    else:
        display_zone = r["now_zone"]
    color = TIER_COLOR[display_zone]
    badge = f'<div class="box-badge">{CHECK}</div>' if r["improved"] else ""
    tier_word = "FLAGGED" if display_zone == "flag" else ("MODERATE" if display_zone == "moderate" else "ON TRACK")
    # generation step may emit JSON null (not a missing key) for an empty category - .get(key) or ""
    # catches that, since a plain default only covers the key-missing case
    story = copy.get("box_stories", {}).get(patient_cat) or ""
    if r["count"] and story.strip().lower() == "no markers in this category this round.":
        story = ""
    if not story and r.get("count", 1) == 0:
        story = "No markers in this category this round."
    why_lines = f'<span class="box-tierword" style="color:{color}">{tier_word}.</span> {story}'
    forward = copy.get("box_forward", {}).get(patient_cat) or ""
    headline = copy.get("headlines", {}).get(patient_cat) or ""
    pain = next((p for p in record.pain_points if patient_cat in p.categories), None)
    quote_html = ""
    if pain:
        maint = copy.get("pain_point_maintenance", {}).get(patient_cat) or ""
        quote_html = f'<div class="box-quote"><span class="box-quote-lbl">At first visit:</span> {pain.text} {maint}</div>'
    return f'''<div class="box avoid" style="--c:{color}; --c-bg:{color}14;">
      <div class="box-top">
        <div class="box-icon">{icon_svg(patient_cat, 13)}</div>
        <div class="box-titles"><div class="box-name">{patient_cat}</div><div class="box-sub">{headline}</div></div>
        <div class="box-score" style="color:{color};">{r["now"]}%</div>
        {badge}
      </div>
      <div class="box-why">{why_lines}</div>
      {quote_html}
      <div class="box-forward">{forward}</div>
      <div class="box-mark">{logo_mark}</div>
    </div>'''


def vitality_index_box_html(record: PatientRecord, symptom_now: float | None, logo_mark: str):
    status_by_score = {
        0: "No Concern",
        1: "Some Concern",
        2: "Significant Concern",
        None: "Not Assessed",
    }
    domain_rows = []
    for label, domain in scoring.VITALITY_LABELS.items():
        value = record.vitality_index.get(domain)
        status = status_by_score.get(value, value if isinstance(value, str) else "Not Assessed")
        status_color = (RED if status == "Significant Concern" else
                        YELLOW if status == "Some Concern" else
                        GREEN if status == "No Concern" else MUTE)
        domain_rows.append(
            f'<div class="vitality-domain"><span>{label}</span>'
            f'<span class="status" style="color:{status_color};">{status}</span></div>'
        )
    why = "Current domain assessments"
    if all(record.vitality_index.get(domain) is None for domain in scoring.VITALITY_DOMAINS):
        # No answer on the upload form or in a read provider note: never shown as "No Concern", never scored.
        color = MUTE
        label = _html(clinic_config.VITALITY_NOT_PROVIDED_LABEL)
        score_html = f'<div class="vitality-score-missing">{label}</div>'
        tier_word = label.upper()
        why = "No Vitality Index answers were provided for this report"
        domain_rows = []
    else:
        score = round(symptom_now)
        color = GREEN if score >= 88 else YELLOW if score >= 50 else RED
        score_html = f'<div class="box-score" style="color:{color};">{score}%</div>'
        tier_word = "OPTIMAL" if color == GREEN else "MODERATE" if color == YELLOW else "FLAGGED"
    return f'''<div class="box avoid" style="--c:{color}; --c-bg:{color}14;">
      <div class="box-top">
        <div class="box-icon">{icon_svg("Repair", 13)}</div>
        <div class="box-titles"><div class="box-name">Symptom / Vitality Index</div><div class="box-sub">Seven scored domains</div></div>
        {score_html}
      </div>
      <div class="box-why"><span class="box-tierword" style="color:{color}">{tier_word}.</span> {why}</div>
      <div class="vitality-domains">{"".join(domain_rows)}</div>
      <div class="box-mark">{logo_mark}</div>
    </div>'''


def _default_structure_headline(record: PatientRecord) -> str:
    """Deterministic, data-grounded fallback so the DEXA headline is never blank/dashed even if
    the generation step's own headline came back empty - built only from real scan values, never
    a fabricated one. This is a safety net; the generation prompt is the primary fix."""
    scans_with_vat = [d for d in record.dexa_history if d.vat_fat_mass_lb is not None]
    if len(scans_with_vat) >= 2:
        first_vat, latest_vat = scans_with_vat[0].vat_fat_mass_lb, scans_with_vat[-1].vat_fat_mass_lb
        if latest_vat < first_vat:
            return f"Visceral fat down from {first_vat} lb to {latest_vat} lb."
        if latest_vat > first_vat:
            return f"Visceral fat up from {first_vat} lb to {latest_vat} lb."
        return f"Visceral fat holding steady at {latest_vat} lb."
    return "Body composition tracked across your DEXA scan history."


def _is_complete_dexa_reading(d: DexaReading) -> bool:
    """A partial scan (e.g. a VAT-only follow-up) belongs in the full scan history, never as the snapshot
    "current"/"when you came in" reading. One definition for the report, copy and staff notes:
    scoring.has_body_composition (a body fat %, or total, fat and lean mass)."""
    return scoring.has_body_composition(d)


def _first_complete_dexa_reading(dexa_history: list[DexaReading]) -> DexaReading:
    return next((d for d in dexa_history if _is_complete_dexa_reading(d)), dexa_history[0])


def _latest_complete_dexa_reading(dexa_history: list[DexaReading]) -> DexaReading:
    return next((d for d in reversed(dexa_history) if _is_complete_dexa_reading(d)), dexa_history[-1])


def _dexa_value(reading: DexaReading, field: str, suffix: str = "") -> str:
    """A DEXA value as printed, with a short label when the scanner marked it "(e)" estimated or when it
    was not printed and is computed from printed values (body fat % from fat and total mass)."""
    value = getattr(reading, field)
    if value is None:
        return f"{fmt(value)}{suffix}"
    label = (f' <span class="dexa-est">{_html(clinic_config.DEXA_ESTIMATED_LABEL)}</span>'
             if field in (reading.estimated or []) else "")
    if field in (reading.computed or []):
        label += f' <span class="dexa-est">{_html(clinic_config.DEXA_COMPUTED_LABEL)}</span>'
    return f"{fmt(value)}{suffix}{label}"


def dexa_panel(record: PatientRecord, copy, roll, dexa_img_b64: str | None):
    if not record.dexa_history:
        return ""
    color = TIER_COLOR["optimal"]
    # chronologically last is not necessarily "current" - a corrupted/partial scan (no total/fat/
    # lean mass) that happens to sort last must never be picked over an earlier complete scan
    first = _first_complete_dexa_reading(record.dexa_history)
    latest = _latest_complete_dexa_reading(record.dexa_history)
    pain = next((p for p in record.pain_points if "Structure" in p.categories), None)
    quote_html = ""
    if pain:
        maint = copy.get("pain_point_maintenance", {}).get("Structure", "")
        quote_html = f'<div class="dexa-quote"><span class="lbl">At first visit:</span> {pain.text} {maint}</div>'
    img_html = (f'<img src="data:image/png;base64,{dexa_img_b64}" class="dexa-scan-img" '
                f'alt="{record.name} DEXA scan comparison"/>') if dexa_img_b64 else ""
    def _history_row(d: DexaReading) -> str:
        if d.vat_fat_mass_lb is not None:
            vat_col = f'<span class="v">{_dexa_value(d, "vat_fat_mass_lb", " lb VAT")}</span>'
        elif d.visceral_fat_area_cm2 is not None:
            vat_col = f'<span class="v">{_dexa_value(d, "visceral_fat_area_cm2", " cm&sup2; VAT")}</span>'
        else:
            vat_col = ""
        return (
            f'<div class="dexa-hist-row"><span class="d">{fmt_date(d.date_display)}</span>'
            f'<span class="v">{_dexa_value(d, "total_mass_lb", " lb total")}</span>'
            f'<span class="v">{_dexa_value(d, "fat_mass_lb", " lb fat")}</span>'
            f'<span class="v">{_dexa_value(d, "lean_mass_lb", " lb lean")}</span>'
            f'<span class="v">{_dexa_value(d, "body_fat_pct", " fat")}</span>'
            f'{vat_col}</div>'
        )

    history_rows = "".join(_history_row(d) for d in record.dexa_history)
    structure_now = roll.get("Structure", {}).get("now", "")
    delta = copy.get("dexa_delta", "")
    note = copy.get("box_stories", {}).get("Structure", "")
    delta_html = f'<div class="dexa-delta">{delta}</div>' if delta else ""
    note_html = f'<p class="dexa-note">{note}</p>' if note else ""
    # a plain default() call here would treat "" the same as null and convert it to a dash - a
    # real headline must always render when real DEXA data exists (see generation prompt), so an
    # empty AI response falls back to a deterministic real-data sentence instead of a blank dash
    structure_headline = copy.get("headlines", {}).get("Structure") or _default_structure_headline(record)
    # only a genuine 2+ scan comparison earns the before/after layout and improvement badge -
    # a single scan on file has nothing to compare against, so it gets one "current scan" box
    has_comparison = first is not latest  # two different complete scans, as in the summary line
    # No DEXA score (no body fat % or VAT area for the current scan, or no sex entered): the stat is left out,
    # never shown as "—% optimized"; the staff notes say why (pipeline.dexa_score_notes).
    optimized_html = ("" if structure_now in (None, "") else
                      f'<div class="dexa-stat"><div class="num" style="color:{color};">{fmt(structure_now)}%</div>'
                      '<div class="cap">Optimized</div></div>')
    badge_html = f'<div class="dexa-badge">{CHECK}</div>' if has_comparison else ""
    if has_comparison:
        stat_block_html = f'''<div class="dexa-stat-block">
          <div class="dexa-row">
            <div class="dexa-row-lbl">When you came in &middot; {fmt_date(first.date_display)}</div>
            <div class="dexa-row-stats dim">
              <div class="dexa-stat"><div class="num">{_dexa_value(first, "body_fat_pct")}</div><div class="cap">Body fat</div></div>
              <div class="dexa-stat"><div class="num">{_dexa_value(first, "fat_mass_lb")}</div><div class="cap">Fat mass, lb</div></div>
              <div class="dexa-stat"><div class="num">{_dexa_value(first, "lean_mass_lb")}</div><div class="cap">Lean mass, lb</div></div>
            </div>
          </div>
          <div class="dexa-row">
            <div class="dexa-row-lbl bright">Where you are now &middot; {fmt_date(latest.date_display)}</div>
            <div class="dexa-row-stats">
              <div class="dexa-stat"><div class="num">{_dexa_value(latest, "body_fat_pct")}</div><div class="cap">Body fat</div></div>
              <div class="dexa-stat"><div class="num">{_dexa_value(latest, "fat_mass_lb")}</div><div class="cap">Fat mass, lb</div></div>
              <div class="dexa-stat"><div class="num">{_dexa_value(latest, "lean_mass_lb")}</div><div class="cap">Lean mass, lb</div></div>
              {optimized_html}
            </div>
          </div>
        </div>'''
    else:
        stat_block_html = f'''<div class="dexa-stat-block">
          <div class="dexa-row">
            <div class="dexa-row-lbl bright">Current scan &middot; {fmt_date(latest.date_display)}</div>
            <div class="dexa-row-stats">
              <div class="dexa-stat"><div class="num">{_dexa_value(latest, "body_fat_pct")}</div><div class="cap">Body fat</div></div>
              <div class="dexa-stat"><div class="num">{_dexa_value(latest, "fat_mass_lb")}</div><div class="cap">Fat mass, lb</div></div>
              <div class="dexa-stat"><div class="num">{_dexa_value(latest, "lean_mass_lb")}</div><div class="cap">Lean mass, lb</div></div>
              {optimized_html}
            </div>
          </div>
        </div>'''
    return f'''<div class="dexa-panel">
      <div class="dexa-top">
        <div><div class="dexa-eyebrow">STRUCTURE &middot; DEXA BODY COMPOSITION SCAN</div>
        <div class="dexa-title">{structure_headline}</div></div>
        {badge_html}
      </div>
      <div class="dexa-body">
        <div class="dexa-figure">{img_html}</div>
        {stat_block_html}
      </div>
    {delta_html}
      <div class="dexa-history">
        <div class="dexa-history-title">Full scan history</div>
        {history_rows}
      </div>
    {note_html}
            <p class="dexa-note">Interim reference ranges use standard medical/athletic body-fat and visceral-fat ranges; CellDeep calibration is pending.</p>
      {quote_html}
    </div>'''


def protocol_section(record: PatientRecord, roll, copy):
    blocks = []
    lab_visible = [p for p in record.protocol if p.lab_visible]
    not_lab_visible = [p for p in record.protocol if not p.lab_visible]
    reasons = copy.get("protocol_reasons", {})
    for item in lab_visible:
        cats = item.target_categories or ["General"]
        cat_str = ", ".join(cats)
        cadence = fmt(item.cadence, dash="")
        protocol_label = (f"{item.name} ({cadence}, targeting {cat_str})" if cadence
                  else f"{item.name}, targeting {cat_str}")
        color = TIER_COLOR[roll.get(cats[0], {}).get("now_zone", "optimal")] if cats and cats[0] in roll else AQUA_DK
        reason = reasons.get(item.name, item.name)
        blocks.append(f'''<div class="protocol-block avoid" style="--c:{color};">
          <div class="pname">{protocol_label}</div>
          <div class="preason">{reason}</div>
        </div>''')
    nonlab = "".join(
            f'<div class="nonlab-item"><b>{p.name}</b>{f" ({fmt(p.cadence)})" if p.cadence else ""}. '
            f'<span>{reasons.get(p.name, "")}</span></div>'
        for p in not_lab_visible
    )
    nonlab_block = ""
    if not_lab_visible:
        nonlab_block = f'''<div class="nonlab-box avoid">
          <div class="t">Also in your protocol, not reflected in bloodwork</div>
          {nonlab}
        </div>'''
    heading = '<div class="sec-title">Why You\'re On What You\'re On</div>'
    if blocks:
        return (f'<div class="protocol-heading-unit">{heading}{blocks[0]}</div>'
                + "".join(blocks[1:]) + nonlab_block)
    if nonlab_block:
        return f'<div class="protocol-heading-unit">{heading}{nonlab_block}</div>'
    return ""


def _html(text) -> str:
    return html_escape(text or "")


def _lab_flag_words(flag) -> str:
    """' - lab flag High' for a printed flag; empty when the lab printed none. Never computed."""
    if not flag:
        return ""
    return f'<span class="lab-flag-words"> - lab flag {_html(clinic_config.LAB_FLAG_WORDS.get(flag, flag))}</span>'


def lab_flag_differs(m) -> bool:
    """The lab printed H/L for the current result while CellDeep's status is one listed in
    clinic_config.LAB_FLAG_DISAGREES_WITH (default: Optimal only). A result printed as a limit shows its flag
    next to the value instead, so it never gets an extra line."""
    return (bool(m.lab_flag_now) and m.lab_flag_now in clinic_config.LAB_FLAG_WORDS
            and m.now_tier in clinic_config.LAB_FLAG_DISAGREES_WITH
            and not scoring.is_censored(m.disp_now, m.now))


def _lab_flag_line(m) -> str:
    """'Lab flag: Low (lab range 38-380)' under a scored value whose lab flag disagrees."""
    if not (clinic_config.SHOW_LAB_FLAG_WHEN_IT_DIFFERS and lab_flag_differs(m)):
        return ""
    lab_range = (m.lab_range_now or {}).get("display")
    range_text = f" ({clinic_config.LAB_RANGE_LABEL} {_html(lab_range)})" if lab_range else ""
    text = clinic_config.LAB_FLAG_LINE.format(flag=_html(clinic_config.LAB_FLAG_WORDS[m.lab_flag_now]),
                                              lab_range=range_text)
    return f'<span class="lab-flag-line">{text}</span>'


def bio_row_tr(m, copy, color_override=None):
    # A capped/inequality result (e.g. "<0.7") is a real current reading even with now=None,
    # so all three of now/now_tier/disp_now must be empty before calling this "not retested".
    not_retested = m.now is None and m.now_tier is None and not m.disp_now
    unscored = m.unscored_reason == "missing_threshold"
    censored_now = scoring.is_censored(m.disp_now, m.now)
    if unscored:
        color = MUTE
        tier_word = clinic_config.NO_RANGE_CHIP_LABEL
    elif not_retested:
        color = MUTE
        tier_word = "Not retested"
    elif censored_now:
        color = MUTE
        tier_word = clinic_config.CENSORED_CHIP_LABEL
    elif m.now_tier is None:
        color = MUTE
        tier_word = clinic_config.UNSCORABLE_CHIP_LABEL
    else:
        color = color_override or TIER_COLOR.get(m.now_tier, MUTE)
        tier_word = {"optimal": "Optimal", "moderate": "Moderate", "flag": "Flagged"}[m.now_tier]
    bg = f"{color}22"
    unit = f' <span class="bio-unit">{m.unit}</span>' if m.unit else ""
    note = copy.get("marker_notes", {}).get(m.name)
    what = copy.get("marker_what", {}).get(m.name)
    if m.then is not None:
        then_color = MUTE if unscored else TIER_COLOR.get(m.then_tier, YELLOW)
        then_date = f'<span class="bio-date">{fmt_date(m.then_date_display)}</span>' if m.then_date_display else ""
        then_cell = f'<span class="bio-pill" style="background:{then_color}22; color:{then_color};">{fmt(m.disp_then)}</span>{then_date}'
    elif scoring.is_censored(m.disp_then, m.then):
        then_date = f'<span class="bio-date">{fmt_date(m.then_date_display)}</span>' if m.then_date_display else ""
        then_cell = (f'<span class="bio-pill" style="background:{MUTE}22; color:{MUTE};">{_html(m.disp_then)}</span>'
                     f'{_lab_flag_words(m.lab_flag_then)}{then_date}')
    else:
        then_cell = f'<span class="bio-dash">{fmt(m.disp_then)}</span>'
    now_date = f'<span class="bio-date">{fmt_date(m.now_date_display)}</span>' if m.now_date_display else ""
    now_text = _html(m.disp_now) if censored_now else fmt(m.disp_now)
    now_flag = _lab_flag_words(m.lab_flag_now) if censored_now else ""
    now_cell = (f'<span class="bio-pill now" style="background:{bg}; color:{color};">{now_text}</span>'
                f'{now_flag}{unit}{now_date}{_lab_flag_line(m)}')
    note_html = ""
    if note:
        note_text = _join_sentences(what, note)
        note_html = (f'<div class="bio-note"><b style="font-style:normal; color:{INK};">What this is:</b> '
                     f'{note_text}</div>') if note_text else ""
    row = f'''<div class="bio-table-row bio-tr" style="--c:{color};">
      <div class="td-name"><span class="bio-name">{m.name}</span> <span class="bio-tierchip" style="color:{color}; background:{color}18;">{tier_word}</span>{_also_on_file(m.full_history)}{note_html}</div>
      <div class="td-range">{m.disp_range}</div>
      <div class="td-then">{then_cell}</div>
      <div class="td-now">{now_cell}</div>
    </div>'''
    return row


def _column_header(dates, fallback):
    """A column header names a date only when every result in that column is from that date."""
    distinct = {date for date in dates if date}
    return fmt_date(distinct.pop()) if len(distinct) == 1 else fallback


def panel_column_headers(record):
    """(earlier, latest) headers for the full-panel table. A marker's earlier column can hold a result that
    is not from the document's first draw (e.g. a single result older than the latest draw), so the
    headers come from the dates actually shown in each column, never from the document-wide dates."""
    earlier = [m.then_date_display for m in record.markers if m.then is not None or m.disp_then]
    latest = [m.now_date_display for m in record.markers if m.now is not None or m.disp_now]
    return (_column_header(earlier, "Earlier") if earlier else "", _column_header(latest, "Latest"))


def bio_group(cat, record, copy, show_headers=True):
    rows = [m for m in record.markers if m.category == cat]
    if not rows:
        return ""
    pcat = DATA_TO_PATIENT_CATEGORY.get(cat)
    link = f'<span class="link">&uarr; see {pcat} above</span>' if pcat else '<span class="link">general screening</span>'
    tagline = copy.get("category_taglines", {}).get(cat, "")

    def row_color(m):
        override_cat = NARRATIVE_CATEGORY_OVERRIDE.get(m.name)
        if override_cat:
            return TIER_COLOR.get(m.now_tier)
        return None

    row_html = [bio_row_tr(m, copy, row_color(m)) for m in rows]
    narrative = copy.get("group_narratives", {}).get(cat, "")
    narr_html = f'<p style="font-size:9.5px; color:#4c4744; margin:4px 0 8px; font-style:italic;">{fmt(narrative)}</p>' if narrative else ""
    tagline_html = f'<div style="font-size:10.5px; color:#4c4744; margin:4px 0 8px;">{fmt(tagline)}</div>' if tagline else ""
    date_headers = panel_column_headers(record)
    header_html = (f'<div class="bio-table-row bio-table-header"><div>Marker</div><div>Reference Range</div><div>{date_headers[0]}</div><div>{date_headers[1]}</div></div>'
                   if show_headers else "")
    first_row = row_html[0]
    remaining_rows = "\n".join(row_html[1:])
    return (f'<div class="bio-group"><div class="bio-category-lead">'
            f'<div class="bio-group-title">{cat.upper()} {link}</div>{tagline_html}'
            f'<div class="bio-table">{header_html}{first_row}</div></div>{narr_html}{remaining_rows}</div>')


def _also_on_file(results) -> str:
    """Every result between the earliest and latest shown, so no lab-flagged result is left out."""
    parts = []
    for result in results:
        flag = result.get("lab_flag")
        flag_text = f" (lab flag {_html(clinic_config.LAB_FLAG_WORDS.get(flag, flag))})" if flag else ""
        parts.append(f"{_html(result.get('disp_value'))}{flag_text} on {fmt_date(result.get('date_display'))}")
    return f'<div class="also-on-file">Also on file: {"; ".join(parts)}</div>' if parts else ""


def _lab_result_cell(result) -> str:
    if result is None:
        return f'<span class="bio-dash">{fmt(None)}</span>'
    flag = result.get("lab_flag")
    flag_html = f' <b class="lab-flag">{flag}</b>' if flag else ""
    date = f'<span class="bio-date">{fmt_date(result["date_display"])}</span>' if result.get("date_display") else ""
    return f'<span class="bio-pill lab-pill">{fmt(result["disp_value"])}{flag_html}</span>{date}'


def lab_reported_section(record: PatientRecord) -> str:
    """Lab-reported results exactly as printed: value, the lab's range and the lab's own H/L flag.
    Never a CellDeep score or tier color, and never part of any system rollup."""
    if not record.lab_reported:
        return ""
    groups = []
    for group in dict.fromkeys(item.group for item in record.lab_reported):
        rows = []
        for item in (item for item in record.lab_reported if item.group == group):
            latest = item.results[-1]
            earlier = item.results[0] if len(item.results) > 1 else None
            rows.append(f'''<div class="bio-table-row bio-tr lab-tr" style="--c:{MIDGRAY};">
      <div class="td-name"><span class="bio-name">{item.name}</span>{_also_on_file(item.results[1:-1])}</div>
      <div class="td-range">{_html(latest.get("lab_range") or clinic_config.NO_RANGE_LABEL)}</div>
      <div class="td-then">{_lab_result_cell(earlier)}</div>
      <div class="td-now">{_lab_result_cell(latest)}</div>
    </div>''')
        header = ('<div class="bio-table-row bio-table-header"><div>Test</div><div>Lab Reference Range</div>'
                  '<div>Earlier</div><div>Latest</div></div>')
        groups.append(f'<div class="bio-group"><div class="bio-group-title">{group.upper()}</div>'
                      f'<div class="bio-table">{header}{"".join(rows)}</div></div>')
    return f'''<div class="lab-reported">
    <div class="sec-title" style="margin-top:22px;">Lab-Reported Results, Not Scored</div>
    <p style="font-size:11px; color:#4c4744; margin-bottom:12px;">These results are shown exactly as your lab reported them, with the lab's own reference range and the lab's H (high) or L (low) flag. CellDeep has not scored them, and they do not change any score in this report.</p>
    {"".join(groups)}
    </div>'''


class StaffContentLeak(RuntimeError):
    """Raised instead of writing a patient PDF that would contain staff-only QA text."""


def render(record: PatientRecord, copy: dict, out_path: str,
           logo_traced_path: str | None = None, dexa_img_b64: str | None = None):
    """The single entry point. Produces a finished PDF at out_path."""

    if dexa_img_b64 is None and record.dexa_history:
        dexa_img_b64 = record.dexa_history[-1].scan_image_b64
    first_draw = record.first_draw_date
    latest_draw = record.latest_draw_date
    try:
        if first_draw and latest_draw:
            start_dt = parse_date_value(first_draw)
            end_dt = parse_date_value(latest_draw)
            days_apart = f"· {(end_dt - start_dt).days} days"
        else:
            days_apart = ""
    except ValueError:
        try:
            if first_draw and latest_draw:
                start_dt = parse_date_value(first_draw)
                end_dt = parse_date_value(latest_draw)
                days_apart = f"· {(end_dt - start_dt).days} days"
            else:
                days_apart = ""
        except ValueError:
            days_apart = ""

    structure_now = copy.get("structure_score_now")
    structure_then = copy.get("structure_score_then")
    structure_improved = copy.get("structure_improved", False)

    roll, order, overall_now, overall_then, has_dexa = build_rollups(
        record, structure_now, structure_then, structure_improved)

    resolved_logo_path = logo_traced_path or _load_logo_traced_path()
    logo_mark = logo_svg(CHARCOAL, resolved_logo_path)
    logo = logo_svg(CHARCOAL, resolved_logo_path)

    grid_pairs = (("Reserves", "Flow"), ("Fuel", "Repair"), ("Pace", "Drive"))
    visible_systems = {cat for cat in DATA_TO_PATIENT_CATEGORY.values() if markers_for_category(record, cat)}
    grid_html = "\n".join(
        f'<div class="grid-row">{"".join(box_html(cat, roll, copy, record, logo_mark) for cat in pair if cat in visible_systems)}</div>'
        for pair in grid_pairs
        if any(cat in visible_systems for cat in pair)
    )
    dexa_html = dexa_panel(record, copy, roll, dexa_img_b64) if has_dexa else ""
    symptom_now = scoring.symptom_percent_optimized(record.vitality_index)
    vitality_html = vitality_index_box_html(record, symptom_now, logo_mark)
    protocol_html = protocol_section(record, roll, copy)
    protocol_section_html = protocol_html
    systems_heading = "YOUR SIX SYSTEMS, ATTENTION NEEDED FIRST" if len(visible_systems) == 6 else "YOUR SYSTEMS, ATTENTION NEEDED FIRST"
    systems_html = f'''<div class="systems-flow">
        {f'<div class="sec-title">{systems_heading}</div><div class="grid">{grid_html}</div>' if grid_html else ""}
        <div class="grid"><div class="grid-row">{vitality_html}</div></div>
        {protocol_section_html}
        <p class="footer-note">Colors: green indicates optimal, yellow indicates moderate, red indicates flagged. Box position, top to bottom, reflects what needs attention first, not severity of illness. Some markers move as an expected result of your current protocol rather than a concern.</p>
    </div>''' if grid_html or protocol_html else ""

    data_categories = sorted(set(m.category for m in record.markers), key=lambda c: (
        ["Inflammation", "Lipids", "Metabolic", "Hormones", "Thyroid", "Foundational", "Also Monitored"].index(c)
        if c in ["Inflammation", "Lipids", "Metabolic", "Hormones", "Thyroid", "Foundational", "Also Monitored"]
        else 99))
    breakdown_groups = [
        bio_group(c, record, copy, index == 0)
        for index, c in enumerate(data_categories)
    ]
    first_breakdown = breakdown_groups[0] if breakdown_groups else ""
    remaining_breakdown = "\n".join(breakdown_groups[1:])
    full_panel_html = f'''<div class="full-panel-intro">
    <div class="sec-title" style="margin-top:22px;">Full Panel, Connected to Your Systems Above</div>
    <h1 style="font-size:17px; margin-bottom:5px;">Your complete record</h1>
    <p style="font-size:11px; color:#4c4744; margin-bottom:12px;">Every marker from this round, grouped exactly as they feed the systems above.</p>
    <div class="legend">
        <span><span class="sw" style="background:{GREEN}"></span>Optimal</span>
        <span><span class="sw" style="background:{YELLOW}"></span>Moderate</span>
        <span><span class="sw" style="background:{RED}"></span>Flagged</span>
    </div>
    {first_breakdown}
    </div>
    {remaining_breakdown}''' if data_categories else ""
    if data_categories:
        full_panel_html += '<p class="footer-note">Reference ranges reflect standard laboratory values. Markers vary by which panel was run for this draw; some rounds include a more extensive workup than others, and that is expected, not a gap in your care. This document is generated for CellDeep and replaces the standard lab notebook page in your chart.</p>'

    full_panel_html += lab_reported_section(record)

    bullets_html = "".join(f'<li>{fmt(b)}</li>' for b in copy.get("optimization_summary_bullets", []))

    gauge_zone = "optimal" if overall_now >= 88 else "moderate"
    first_date = fmt_date(first_draw)
    latest_date = fmt_date(latest_draw)
    date_range = f"{first_date} &nbsp;&rarr;&nbsp; {latest_date}"
    if days_apart:
        date_range = f"{date_range} {days_apart}"

    next_30_label = fmt(copy.get("next_30_label", "Stay the course"))
    next_30_sub = fmt(copy.get("next_30_sub", ""))
    next_90_label = fmt(copy.get("next_90_label", "Next 90 days"))
    next_90_sub = fmt(copy.get("next_90_sub", ""))
    # The hero targets "your next birthday", so the step names the age the patient turns next.
    by_age_label = fmt(copy.get("by_age_label", f"By {record.age + 1}" if record.age is not None
                                else "By your next birthday"))
    by_age_sub = fmt(copy.get("by_age_sub", ""))
    overall_then_display = f"{overall_then}%" if overall_then is not None else fmt(overall_then)
    HTML = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>CellDeep: Patient and Protocol Record</title>
<style>{CSS}</style></head>
<body>
<div class="page">
    <div class="opening-flow">
  <div class="masthead">
    <div class="brand-row">{logo}<span class="brand-name">CELLDEEP</span></div>
    <div class="meta">
      <div class="eyebrow">Patient &amp; Protocol Record</div>
      <div class="pname">{fmt_patient_name(record.name)}</div>
      <div class="ddates">{fmt(date_range)}</div>
    </div>
  </div>
    <div class="hero">
    <div class="hero-eyebrow">{f"AGE {record.age} &nbsp;&rarr;&nbsp; " if record.age else ""}{fmt(copy.get("hero_target_line", ""))}</div>
    <div class="hero-top">
      <div class="big">{fmt(copy.get("hero_question", ""))}</div>
      <div class="hero-ring">
        {gauge_svg(overall_then, overall_now, gauge_zone)}
      </div>
    </div>
    <div class="color-legend">Red = flagged &nbsp;&middot;&nbsp; Yellow = moderate &nbsp;&middot;&nbsp; Green = optimal &nbsp;&middot;&nbsp; {CHECK_SM} = improved since your first visit</div>
    <div class="journey-row">
    <div class="jstep"><div class="lbl">You were</div><div class="val">{overall_then_display}</div><div class="sub">{fmt_date(first_draw)}</div></div>
      <div class="jstep"><div class="lbl">You are</div><div class="val">{overall_now}%</div><div class="sub">{fmt_date(latest_draw)}</div></div>
      <div class="jstep"><div class="lbl">Next 30 days</div><div class="val">{next_30_label}</div><div class="sub">{next_30_sub}</div></div>
      <div class="jstep"><div class="lbl">Next 90 days</div><div class="val">{next_90_label}</div><div class="sub">{next_90_sub}</div></div>
      <div class="jstep"><div class="lbl">By age</div><div class="val">{by_age_label}</div><div class="sub">{by_age_sub}</div></div>
    </div>
  </div>

    <div class="bottomline">
    <div class="bl-eyebrow">OPTIMIZATION SUMMARY</div>
    <ul class="bl-list">{bullets_html}</ul>
  </div>

  {dexa_html}
    </div>

        {systems_html}

    {full_panel_html}
</div>
</body></html>'''

    leaks = [marker for marker in STAFF_NOTE_MARKERS if marker in HTML]
    if leaks:
        raise StaffContentLeak(f"Staff QA text reached the patient report: {leaks}; report not written")

    html_path = out_path.replace(".pdf", ".html")
    import os
    html_path = os.path.abspath(html_path)
    with open(html_path, "w") as f:
        f.write(HTML)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{html_path}")
        page.wait_for_timeout(200)
        page.pdf(path=out_path, print_background=True, format="Letter")
        browser.close()
