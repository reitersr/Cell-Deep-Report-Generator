from pathlib import Path

from pipeline import verify_extraction_completeness


def main():
    log_path = Path("/tmp/extraction_completeness_log.txt")
    log_path.unlink(missing_ok=True)
    extracted = {
        "protocol": [{"name": "Retatrutide", "cadence": "weekly"}],
        "markers": [],
    }
    note_text = "Protocol: Retatrutide (weekly). Klow peptide blend (daily)."
    notice = verify_extraction_completeness(
        extracted,
        provider_note_text=note_text,
        lab_text="Fibrinogen 288 mg/dL",
    )
    added = extracted["protocol"][-1]
    log = log_path.read_text(encoding="utf-8")
    assert added["name"] == "Klow peptide blend"
    assert added["cadence"] == "daily"
    assert not extracted["markers"]
    assert "ADDED MISSING PROTOCOL ITEM: Klow peptide blend (cadence: daily)" in log
    assert "WARNING: MARKER 'Fibrinogen' FOUND IN SOURCE BUT MISSING FROM EXTRACTION" in log
    assert any("Fibrinogen" in warning for warning in notice.other_notes)
    print("PASS: protocol completeness correction")
    print("  added:", added)
    print("PASS: marker completeness warning without auto-fix")
    print("  markers:", extracted["markers"])
    print("FULL LOG:")
    print(log, end="")


if __name__ == "__main__":
    main()