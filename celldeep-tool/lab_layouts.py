"""Registry of deterministic lab-report layouts for digital (text) lab PDFs.

Each layout is a module with:
    NAME                      the lab/layout name shown in the staff notes
    detect(pages) -> bool     True only when the pages carry this layout's own printed structure
    parse(pages, ...)         reads the pages by exact text and word boxes (never a vision model) and returns
                              (marker_occurrences, unrecognized_rows, info)

select() returns the first layout whose detect() is true, or None. With None the original Quest/Cleveland
HeartLab table parser (pipeline._parse_bloodwork_tables) reads the PDF, and a document it does not recognize
is excluded page by page with a staff notice, never guessed. To add a lab, write its module and append it
to LAYOUTS; nothing else changes.
"""

import access_medical

LAYOUTS = [access_medical]
DEFAULT_LAYOUT_NAME = "Quest / Cleveland HeartLab table layout"


def select(pages):
    """The registered layout these digital lab pages print, or None for the default parser."""
    for layout in LAYOUTS:
        if layout.detect(pages):
            return layout
    return None
