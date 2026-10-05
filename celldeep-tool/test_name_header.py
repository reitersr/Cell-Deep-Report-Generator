"""Staff-notes header: who the report is for and the name every source printed."""

import copy

import pipeline
from synthetic_fixtures import deterministic_fixtures as fx
from synthetic_fixtures import dexa as dexa_fx
from test_scan_bloodwork import MockClient, payloads

NOTE = """## Consultation Note
Patient: Synthetic, Pat
Follow-up visit.
"""


def _review(tmp_path, monkeypatch, capsys, other_scan_name=None):
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [
        fx.section_preamble("SYN-NAME", "02/03/2026") + fx.header_line(100) + fx.row(130, "TSH", "1.0")
        + [(300, 54, "DOB: 03/15/1982")], [], []])
    scan_reads = []
    for page in payloads():
        if other_scan_name and page["page"] == 3:
            page["patient_name"] = other_scan_name
        scan_reads += [copy.deepcopy(page), copy.deepcopy(page)]
    dexa = dexa_fx.write_pdf(tmp_path / "dexa.pdf", ["summary"])
    dexa_read = dexa_fx.read([dexa_fx.BASELINE], patient_name="SYNTHETIC, PAT")
    dexa_reader = dexa_fx.DexaReader(dexa, ["summary"], {"summary": (dexa_read, copy.deepcopy(dexa_read))})
    scan_reader = MockClient(scan_reads)

    class Client:
        """Routes scanned-lab reads and DEXA reads to their own mocks."""
        timeout = 240.0

        def __init__(self, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            reader = dexa_reader if "DEXA" in kwargs["messages"][0]["content"][1]["text"] else scan_reader
            return reader.create(**kwargs)

    monkeypatch.setattr(pipeline, "Anthropic", Client)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    pipeline.run(str(labs), [str(dexa)], NOTE, "Synthetic, Pat", None, "male", str(tmp_path / "r.pdf"),
                 collected_date="04/14/2026")
    return (tmp_path / "review.txt").read_text(encoding="utf-8").splitlines(), capsys.readouterr().out


def test_header_lists_every_source_name_and_matches(tmp_path, monkeypatch, capsys):
    lines, _ = _review(tmp_path, monkeypatch, capsys)
    assert lines[:5] == [
        "This report is for Synthetic, Pat (staff-entered)",
        "  lab PDF text pages: Synthetic, Pat - matches",
        "  scanned lab page (2, 3): Synthetic, Pat - matches",
        "  DEXA (file 1 page 1): SYNTHETIC, PAT - matches",
        "  provider note: Synthetic, Pat - matches",
    ]


def test_mismatching_source_is_flagged(tmp_path, monkeypatch, capsys):
    lines, _ = _review(tmp_path, monkeypatch, capsys, other_scan_name="Other, Person")
    assert "  scanned lab page (2): Synthetic, Pat - matches" in lines[:6]
    assert ("  scanned lab page (3): Other, Person - NAME MISMATCH - confirm this source belongs to the patient"
            in lines[:6])


def test_header_holds_no_dob_or_ids_and_logs_hold_no_names(tmp_path, monkeypatch, capsys):
    lines, output = _review(tmp_path, monkeypatch, capsys)
    header = "\n".join(lines[:5])
    assert "1982" not in header and "SYN-SCAN-001" not in header and "SYN-NAME" not in header
    assert "Synthetic" not in output and "Pat" not in output.replace("Patient", "")


def test_sources_without_a_printed_name_say_so():
    lines = pipeline.name_header("Synthetic, Pat", [("lab PDF text pages", None, "Synthetic, Pat")])
    assert lines == ["This report is for Synthetic, Pat (staff-entered)",
                     "  lab PDF text pages: Synthetic, Pat - matches",
                     "  scanned lab page: no name printed", "  DEXA: no name printed",
                     "  provider note: no name printed"]


def test_printed_name_stops_at_the_next_label_on_the_line():
    import scan_bloodwork
    words = [(40, 54, 80, 62, "Patient:"), (82, 54, 130, 62, "Synthetic,"), (132, 54, 150, 62, "Pat"),
             (300, 54, 325, 62, "DOB:"), (327, 54, 380, 62, "03/15/1982"), (400, 54, 420, 62, "Sex:"),
             (422, 54, 430, 62, "M")]
    names = scan_bloodwork.digital_patient_names([words], pipeline._group_lines, pipeline._page_words)
    assert names == {"Synthetic, Pat"}
