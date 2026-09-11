from pipeline import sanitize_text


def test_sanitize_text_preserves_percent_signs():
    assert sanitize_text("moved from 33.2% — to 20.0%") == "moved from 33.2%; to 20.0%"
    assert sanitize_text("Glucose at 77 and HbA1c at 5.0%, respectively") == "Glucose at 77 and HbA1c at 5.0%, respectively"