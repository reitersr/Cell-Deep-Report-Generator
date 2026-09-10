"""
CellDeep Report Generator — Template Renderer
=================================================
Ported from build_final_v7.py (the locked V23 reference). CSS is copied
VERBATIM — every value, every rule, unchanged, because that is the locked
design and nothing about it is open to variation per patient. What changed
is every place that used to read a Star-specific module-level constant now
reads from the PatientRecord and the generated copy dict passed into render().

This file has not been run end-to-end (no live API access during
development — see pipeline.py). The first real test should be running the
full pipeline against Star's actual source material and confirming the
output matches V23 pixel for pixel, since that's the one case we already
know the correct answer to.
"""

import math
import base64
from playwright.sync_api import sync_playwright

from schema import PatientRecord
import scoring
from markers_reference import DATA_TO_PATIENT_CATEGORY, NARRATIVE_CATEGORY_OVERRIDE

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
@page {{ size: Letter; margin: 0.15in 0in 0.15in 0in; }}
*{{box-sizing:border-box; margin:0; padding:0;}}
body{{font-family:'Helvetica Neue',Arial,sans-serif; color:{INK}; font-size:14px; line-height:1.55; background:#fff;}}
.page{{width:8.5in; padding:0 0.6in;}}
.avoid{{page-break-inside:avoid; break-inside:avoid;}}
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
.sec-title{{font-size:11px; font-weight:700; letter-spacing:0.1em; color:{INK}; text-transform:uppercase; margin:10px 0 6px; display:flex; align-items:center; gap:10px;}}
.sec-title::after{{content:""; flex:1; height:2.5px; background:{AQUA};}}

.hero{{border:1.5px solid {AQUA}; border-radius:12px; padding:9px 16px; margin-bottom:5px; box-shadow:0 0 0 1px {AQUA}33 inset;}}
.hero-eyebrow{{font-size:10.5px; font-weight:700; letter-spacing:0.08em; color:{AQUA_DK}; margin-bottom:7px;}}
.bottomline{{background:{CHARCOAL}; color:#F1EEE7; border-radius:9px; padding:4px 16px; margin-bottom:3px;}}
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

.dexa-panel{{border-radius:11px; padding:9px 16px; margin-bottom:9px; position:relative; overflow:hidden; color:{INK};
  background:#fff; border:2px solid {AQUA};}}
.dexa-top{{position:relative; z-index:2; display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;}}
.dexa-eyebrow{{font-size:10.5px; letter-spacing:0.12em; color:{AQUA_DK}; font-weight:700;}}
.dexa-title{{font-family:Georgia,serif; font-size:19px; font-weight:700; color:{INK}; margin-top:4px;}}
.dexa-badge{{width:26px; height:26px; border-radius:50%; background:{GREEN}; display:flex; align-items:center; justify-content:center; border:2.5px solid #fff; box-shadow:0 0 0 1.5px {GREEN};}}
.dexa-body{{display:flex; align-items:center; gap:16px; position:relative; z-index:2; margin-top:5px;}}
.dexa-figure{{flex:none;}}
.dexa-scan-img{{width:148px; border-radius:7px; border:1px solid {LINE};}}
.dexa-history{{margin-top:5px; padding-top:5px; border-top:1px solid {LINE}; position:relative; z-index:2;}}
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

.grid{{display:grid; grid-template-columns:1fr 1fr; gap:7px; margin-bottom:4px;}}
.box{{border:1.5px solid var(--c); border-top:4px solid var(--c); border-radius:9px; padding:9px 14px 10px; background:var(--c-bg); position:relative; overflow:hidden; min-height:80px;}}
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

.protocol-block{{border-left:3px solid var(--c); padding-left:10px; margin:5px 0 6px;}}
.protocol-block .pname{{font-size:12.5px; font-weight:700;}}
.protocol-block .pcadence{{font-size:10px; color:{MUTE}; font-weight:400;}}
.protocol-block .preason{{font-size:10px; color:#4c4744; margin-top:2px; line-height:1.32;}}

.nonlab-box{{background:{CREAM}55; border:1.5px dashed {MIDGRAY}; border-radius:9px; padding:8px 14px; margin-top:4px;}}
.nonlab-box .t{{font-size:9.5px; font-weight:700; letter-spacing:0.05em; color:{MUTE}; text-transform:uppercase; margin-bottom:5px;}}
.nonlab-item{{font-size:11px; margin-bottom:3px;}}
.nonlab-item b{{font-weight:700;}}
.nonlab-item span{{color:#4c4744;}}

.footer-note{{font-size:8.5px; color:{MUTE}; margin-top:6px; max-width:6.9in; line-height:1.4; border-top:1px solid {LINE}; padding-top:6px;}}

.legend{{display:flex; gap:16px; font-size:10.5px; color:#4c4744; margin:2px 0 9px;}}
.legend .sw{{width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:6px;}}
.bio-group{{margin-bottom:7px;}}
.bio-group-title.keepnext{{break-after:avoid; page-break-after:avoid;}}
.bio-group-title{{font-family:Georgia,serif; font-size:13.5px; font-weight:700; display:flex; align-items:baseline; gap:10px;
  border-bottom:2px solid {INK}; padding-bottom:4px; margin-bottom:3px;}}
.bio-group-title .link{{font-size:9.5px; color:{MUTE}; font-weight:400; text-transform:uppercase; letter-spacing:0.03em;}}

.bio-table{{width:100%; border-collapse:collapse; table-layout:fixed;}}
.bio-table col.c-name{{width:34%;}}
.bio-table col.c-range{{width:22%;}}
.bio-table col.c-date{{width:22%;}}
.bio-table thead{{display:table-header-group;}}
.bio-table th{{text-align:left; font-size:8.5px; font-weight:700; letter-spacing:0.04em; text-transform:uppercase; color:{MUTE};
  padding:5px 8px 5px 0; border-bottom:1.5px solid {INK};}}
.bio-table th.th-date{{text-align:center;}}
.bio-tr{{break-inside:avoid; page-break-inside:avoid;}}
.bio-tr td{{padding:6px 8px 6px 0; border-bottom:1px solid {LINE}; vertical-align:middle;}}
.bio-tr .td-name{{border-left:3px solid var(--c); padding-left:8px;}}
.bio-name{{font-size:12.5px; font-weight:700; line-height:1.15;}}
.bio-tierchip{{display:inline-block; font-size:8px; font-weight:700; letter-spacing:0.03em; padding:2px 7px; border-radius:10px; vertical-align:middle; margin-left:3px;}}
.td-range{{font-size:9.5px; color:{MUTE};}}
.td-then, .td-now{{text-align:center;}}
.bio-pill{{display:inline-block; font-weight:800; font-size:13px; padding:4px 12px; border-radius:20px;}}
.bio-dash{{color:{MIDGRAY}; font-size:13px;}}
.bio-unit{{font-size:9px; color:{MUTE}; margin-left:3px; display:block; margin-top:1px;}}
.bio-note-tr .td-note{{padding:0 8px 8px 11px; border-bottom:1px solid {LINE};}}
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
        roll["Structure"] = dict(now=structure_now, then=structure_then,
                                  now_zone="optimal" if structure_now and structure_now >= 88 else "moderate",
                                  then_zone=None, improved=structure_improved, weak=[])
    grid_cats = patient_cats  # Structure is never in the uniform grid — same rule as V23
    order = sorted(grid_cats, key=lambda c: roll[c]["now"])
    all_cats = grid_cats + (["Structure"] if has_dexa else [])
    overall_now = round(sum(roll[c]["now"] for c in all_cats) / len(all_cats))
    then_vals = [roll[c]["then"] for c in all_cats if roll[c]["then"] is not None]
    overall_then = round(sum(then_vals) / len(then_vals)) if then_vals else None
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
    story = copy.get("box_stories", {}).get(patient_cat, "")
    why_lines = f'<span class="box-tierword" style="color:{color}">{tier_word}.</span> {story}'
    forward = copy.get("box_forward", {}).get(patient_cat, "")
    headline = copy.get("headlines", {}).get(patient_cat, "")
    pain = next((p for p in record.pain_points if patient_cat in p.categories), None)
    quote_html = ""
    if pain:
        maint = copy.get("pain_point_maintenance", {}).get(patient_cat, "")
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


def dexa_panel(record: PatientRecord, copy, roll, dexa_img_b64: str | None):
    if not record.dexa_history:
        return ""
    color = TIER_COLOR["optimal"]
    first, latest = record.dexa_history[0], record.dexa_history[-1]
    pain = next((p for p in record.pain_points if "Structure" in p.categories), None)
    quote_html = ""
    if pain:
        maint = copy.get("pain_point_maintenance", {}).get("Structure", "")
        quote_html = f'<div class="dexa-quote"><span class="lbl">At first visit:</span> {pain.text} {maint}</div>'
    img_html = (f'<img src="data:image/png;base64,{dexa_img_b64}" class="dexa-scan-img" '
                f'alt="{record.name} DEXA scan comparison"/>') if dexa_img_b64 else ""
    history_rows = "".join(
        f'<div class="dexa-hist-row"><span class="d">{d.date_display}</span>'
        f'<span class="v">{d.total_mass_lb} lb total</span><span class="v">{d.fat_mass_lb} lb fat</span>'
        f'<span class="v">{d.lean_mass_lb} lb lean</span><span class="v">{d.body_fat_pct} fat</span>'
        f'<span class="v">{(str(d.vat_fat_mass_lb) + " lb") if d.vat_fat_mass_lb is not None else "\u2014"} VAT</span></div>'
        for d in record.dexa_history
    )
    structure_now = roll.get("Structure", {}).get("now", "")
    delta = copy.get("dexa_delta", "")
    note = copy.get("box_stories", {}).get("Structure", "")
    return f'''<div class="dexa-panel avoid">
      <div class="dexa-top">
        <div><div class="dexa-eyebrow">STRUCTURE &middot; DEXA BODY COMPOSITION SCAN</div>
        <div class="dexa-title">{copy.get("headlines", {}).get("Structure", "")}</div></div>
        <div class="dexa-badge">{CHECK}</div>
      </div>
      <div class="dexa-body">
        <div class="dexa-figure">{img_html}</div>
        <div class="dexa-stat-block">
          <div class="dexa-row">
            <div class="dexa-row-lbl">When you came in &middot; {first.date_display}</div>
            <div class="dexa-row-stats dim">
              <div class="dexa-stat"><div class="num">{first.body_fat_pct}</div><div class="cap">Body fat</div></div>
              <div class="dexa-stat"><div class="num">{first.fat_mass_lb}</div><div class="cap">Fat mass, lb</div></div>
              <div class="dexa-stat"><div class="num">{first.lean_mass_lb}</div><div class="cap">Lean mass, lb</div></div>
            </div>
          </div>
          <div class="dexa-row">
            <div class="dexa-row-lbl bright">Where you are now &middot; {latest.date_display}</div>
            <div class="dexa-row-stats">
              <div class="dexa-stat"><div class="num">{latest.body_fat_pct}</div><div class="cap">Body fat</div></div>
              <div class="dexa-stat"><div class="num">{latest.fat_mass_lb}</div><div class="cap">Fat mass, lb</div></div>
              <div class="dexa-stat"><div class="num">{latest.lean_mass_lb}</div><div class="cap">Lean mass, lb</div></div>
              <div class="dexa-stat"><div class="num" style="color:{color};">{structure_now}%</div><div class="cap">Optimized</div></div>
            </div>
          </div>
        </div>
      </div>
      <div class="dexa-delta">{delta}</div>
      <div class="dexa-history">
        <div class="dexa-history-title">Full scan history</div>
        {history_rows}
      </div>
      <p class="dexa-note">{note}</p>
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
        color = TIER_COLOR[roll.get(cats[0], {}).get("now_zone", "optimal")] if cats and cats[0] in roll else AQUA_DK
        reason = reasons.get(item.name, item.name)
        blocks.append(f'''<div class="protocol-block avoid" style="--c:{color};">
          <div class="pname">{item.name} <span class="pcadence">({item.cadence}, targeting {cat_str})</span></div>
          <div class="preason">{reason}</div>
        </div>''')
    nonlab = "".join(
        f'<div class="nonlab-item"><b>{p.name}</b> ({p.cadence}). <span>{reasons.get(p.name, "")}</span></div>'
        for p in not_lab_visible
    )
    nonlab_block = ""
    if not_lab_visible:
        nonlab_block = f'''<div class="nonlab-box avoid">
          <div class="t">Also in your protocol, not reflected in bloodwork</div>
          {nonlab}
        </div>'''
    return "".join(blocks) + nonlab_block


def bio_row_tr(m, copy, color_override=None):
    color = color_override or TIER_COLOR.get(m.now_tier, MUTE)
    bg = f"{color}22"
    unit = f' <span class="bio-unit">{m.unit}</span>' if m.unit else ""
    note = copy.get("marker_notes", {}).get(m.name)
    what = copy.get("marker_what", {}).get(m.name)
    tier_word = {"optimal": "Optimal", "moderate": "Moderate", "flag": "Flagged"}.get(m.now_tier, "Optimal")
    if m.then is not None:
        then_color = TIER_COLOR.get(m.then_tier, YELLOW)
        then_cell = f'<span class="bio-pill" style="background:{then_color}22; color:{then_color};">{m.disp_then}</span>'
    else:
        then_cell = '<span class="bio-dash">&mdash;</span>'
    now_cell = f'<span class="bio-pill now" style="background:{bg}; color:{color};">{m.disp_now}</span>{unit}'
    row = f'''<tr class="bio-tr" style="--c:{color};">
      <td class="td-name"><span class="bio-name">{m.name}</span> <span class="bio-tierchip" style="color:{color}; background:{color}18;">{tier_word}</span></td>
      <td class="td-range">{m.disp_range}</td>
      <td class="td-then">{then_cell}</td>
      <td class="td-now">{now_cell}</td>
    </tr>'''
    note_row = ""
    if note:
        what_html = f'<b style="font-style:normal; color:{INK};">What this is:</b> {what}. ' if what else ""
        note_row = f'<tr class="bio-note-tr"><td colspan="4" class="td-note"><div class="bio-note">{what_html}{note}</div></td></tr>'
    return row + note_row


def bio_group(cat, record, copy, first_draw, latest_draw):
    rows = [m for m in record.markers if m.category == cat]
    if not rows:
        return ""
    pcat = DATA_TO_PATIENT_CATEGORY.get(cat)
    link = f'<span class="link">&uarr; see {pcat} above</span>' if pcat else '<span class="link">general screening</span>'
    def row_color(m):
        override_cat = NARRATIVE_CATEGORY_OVERRIDE.get(m.name)
        if override_cat:
            return TIER_COLOR.get(m.now_tier)
        return None
    rows_html = "\n".join(bio_row_tr(m, copy, row_color(m)) for m in rows)
    narrative = copy.get("group_narratives", {}).get(cat, "")
    narr_html = f'<p style="font-size:9.5px; color:#4c4744; margin:4px 0 8px; font-style:italic;">{narrative}</p>' if narrative else ""
    date_headers = f'<th class="th-date">{first_draw}</th><th class="th-date">{latest_draw}</th>' if first_draw else f'<th class="th-date">{latest_draw}</th>'
    return f'''<div class="bio-group">
      <div class="bio-group-title keepnext">{cat.upper()} {link}</div>
      {narr_html}
      <table class="bio-table">
        <colgroup><col class="c-name"><col class="c-range"><col class="c-date"><col class="c-date"></colgroup>
        <thead><tr><th class="th-name">Marker</th><th class="th-range">Reference Range</th>{date_headers}</tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>'''


def render(record: PatientRecord, copy: dict, out_path: str,
           logo_traced_path: str | None = None, dexa_img_b64: str | None = None):
    """The single entry point. Produces a finished PDF at out_path."""

    days_apart = ""  # left blank unless computed upstream and passed in copy

    structure_now = copy.get("structure_score_now")
    structure_then = copy.get("structure_score_then")
    structure_improved = copy.get("structure_improved", False)

    roll, order, overall_now, overall_then, has_dexa = build_rollups(
        record, structure_now, structure_then, structure_improved)

    logo_mark = logo_svg(CHARCOAL, logo_traced_path)
    logo = logo_svg(CHARCOAL, logo_traced_path)

    grid_html = "\n".join(box_html(c, roll, copy, record, logo_mark) for c in order)
    dexa_html = dexa_panel(record, copy, roll, dexa_img_b64) if has_dexa else ""
    protocol_html = protocol_section(record, roll, copy)

    data_categories = sorted(set(m.category for m in record.markers), key=lambda c: (
        ["Inflammation", "Lipids", "Metabolic", "Hormones", "Thyroid", "Foundational", "Also Monitored"].index(c)
        if c in ["Inflammation", "Lipids", "Metabolic", "Hormones", "Thyroid", "Foundational", "Also Monitored"]
        else 99))
    breakdown_html = "\n".join(
        bio_group(c, record, copy, record.first_draw_date, record.latest_draw_date) for c in data_categories)

    bullets_html = "".join(f'<li>{b}</li>' for b in copy.get("optimization_summary_bullets", []))

    gauge_zone = "optimal" if overall_now >= 88 else "moderate"
    date_range = (f"{record.first_draw_date} &nbsp;&rarr;&nbsp; {record.latest_draw_date}"
                  if record.first_draw_date else record.latest_draw_date)

    HTML = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>CellDeep: Patient and Protocol Record</title>
<style>{CSS}</style></head>
<body>
<div class="page">
  <div class="masthead">
    <div class="brand-row">{logo}<span class="brand-name">CELLDEEP</span></div>
    <div class="meta">
      <div class="eyebrow">Patient &amp; Protocol Record</div>
      <div class="pname">{record.name}</div>
      <div class="ddates">{date_range}</div>
    </div>
  </div>

  <div class="hero avoid">
    <div class="hero-eyebrow">{f"AGE {record.age} &nbsp;&rarr;&nbsp; " if record.age else ""}{copy.get("hero_target_line", "")}</div>
    <div class="hero-top">
      <div class="big">{copy.get("hero_question", "")}</div>
      <div class="hero-ring">
        {gauge_svg(overall_then, overall_now, gauge_zone)}
      </div>
    </div>
    <div class="color-legend">Red = flagged &nbsp;&middot;&nbsp; Yellow = moderate &nbsp;&middot;&nbsp; Green = optimal &nbsp;&middot;&nbsp; {CHECK_SM} = improved since your first visit</div>
    <div class="journey-row">
      <div class="jstep"><div class="lbl">You were</div><div class="val">{overall_then if overall_then is not None else "&mdash;"}%</div><div class="sub">{record.first_draw_date or ""}</div></div>
      <div class="jstep"><div class="lbl">You are</div><div class="val">{overall_now}%</div><div class="sub">{record.latest_draw_date}</div></div>
      {copy.get("forward_steps_html", "")}
    </div>
  </div>

  <div class="bottomline avoid">
    <div class="bl-eyebrow">OPTIMIZATION SUMMARY</div>
    <ul class="bl-list">{bullets_html}</ul>
  </div>

  {dexa_html}

  <div class="sec-title">Your Systems, Attention Needed First</div>
  <div class="grid">{grid_html}</div>

  <div class="sec-title">Why You're On What You're On</div>
  {protocol_html}

  <p class="footer-note">Colors: green indicates optimal, yellow indicates moderate, red indicates flagged. Box position, top to bottom, reflects what needs attention first, not severity of illness. Some markers move as an expected result of your current protocol rather than a concern.</p>

  <div class="sec-title" style="margin-top:22px;">Full Panel, Connected to Your Systems Above</div>
  <h1 style="font-size:17px; margin-bottom:5px;">Your complete record</h1>
  <p style="font-size:11px; color:#4c4744; margin-bottom:12px;">Every marker from this round, grouped exactly as they feed the systems above.</p>
  <div class="legend">
    <span><span class="sw" style="background:{GREEN}"></span>Optimal</span>
    <span><span class="sw" style="background:{YELLOW}"></span>Moderate</span>
    <span><span class="sw" style="background:{RED}"></span>Flagged</span>
  </div>
  {breakdown_html}
  <p class="footer-note">Reference ranges reflect standard laboratory values. Markers vary by which panel was run for this draw; some rounds include a more extensive workup than others, and that is expected, not a gap in your care. This document is generated for CellDeep and replaces the standard lab notebook page in your chart.</p>
</div>
</body></html>'''

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
