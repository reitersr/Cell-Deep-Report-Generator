import pipeline


def _occurrence(name, date_display, value, disp_value):
    return {
        "name": name,
        "date_display": date_display,
        "source_label": "Synthetic Evan three-draw panel",
        "status": "reported",
        "value": value,
        "disp_value": disp_value,
        "is_good": None,
        "lab_range_lo": 0,
        "lab_range_hi": 0,
        "lab_range_display": "",
    }


def test_multiple_date_header_does_not_invent_dates_for_single_result_row():
    source = (
        "Historical Previous Current Draw Dates: 01/07/2026 01/27/2026 04/24/2026\n"
        "DHEA-S 250 µg/dL Reference range 100-500\n"
    )

    assert pipeline._source_marker_dates(source).get("DHEA-S") is None


def test_platform_change_footnote_date_is_not_required_marker_occurrence():
    source = (
        "Collection Date: 04/24/2026\n"
        "HbA1c 5.6 %\n"
        "\n"
        "\n"
        "This test was performed on the Roche c503 platform. Effective 3/5/2024, a change in "
        "test platforms from the Abbott Architect to the Roche c503 may have shifted HbA1c results "
        "compared to historical results. Based on laboratory validation testing conducted at Quest, "
        "the Roche platform relative to the Abbott platform had an average increase in HbA1c value "
        "of <0.3%.\n"
    )
    extracted = {"marker_occurrences": [_occurrence("HbA1c", "04/24/2026", 5.6, "5.6")]}

    assert pipeline._source_marker_dates(source)["HbA1c"] == {
        (2026, 4, 24): "04/24/2026",
    }
    assert pipeline._missing_source_marker_dates(extracted, source) == []


def test_guideline_marker_alias_does_not_inherit_platform_change_footnote_date():
    source = (
        "Effective 3/5/2024, historical and current results may vary after a platform change.\n"
        "Hemoglobin A1c interpretive guideline: values below 5.7 % are in the expected range.\n"
    )

    assert pipeline._missing_source_marker_dates({"marker_occurrences": []}, source) == []


def test_inline_dated_result_with_unit_remains_required_occurrence():
    source = "HbA1c 5.6 % Collection Date: 04/24/2026\n"

    assert pipeline._missing_source_marker_dates({"marker_occurrences": []}, source) == [
        ("HbA1c", "04/24/2026"),
    ]


def test_capped_value_on_differently_formatted_page_is_accepted_as_matching():
    # A capped inequality result ("<0.7") on a plain fixed-width report page, structurally
    # different from a colored multi-column table - this must not be flagged as a mismatch.
    source = "Collected: 04/24/2026\nFSH               <0.7    mIU/mL     1.4-12.8\n"
    occurrence = _occurrence("FSH", "04/24/2026", None, "<0.7")

    assert pipeline._mismatched_source_marker_values({"marker_occurrences": [occurrence]}, source) == []


def test_section_scoped_attribution_finds_marker_far_below_its_own_report_date(monkeypatch, tmp_path):
    # Three combined lab reports concatenated in one PDF, each with its own collection date printed
    # once near that section's start - a marker appearing only far below the third section's header
    # must still be attributed to that section's date, not left undated by a fixed line-window.
    filler = "\n".join(f"Unrelated report boilerplate line {i}" for i in range(60))
    source = (
        f"Collected: 01/07/2026\n{filler}\nGlucose (fasting) 90 mg/dL\n\n"
        f"Collected: 01/27/2026\n{filler}\nGlucose (fasting) 92 mg/dL\n\n"
        f"Collected: 04/24/2026\n{filler}\nFSH                <0.7    mIU/mL\n"
    )

    evidence = pipeline._source_marker_dates(source)
    assert evidence["FSH"] == {(2026, 4, 24): "04/24/2026"}
    assert pipeline._missing_source_marker_dates({"marker_occurrences": []}, source) == [
        ("FSH", "04/24/2026"),
        ("Glucose (fasting)", "01/07/2026"),
        ("Glucose (fasting)", "01/27/2026"),
    ]