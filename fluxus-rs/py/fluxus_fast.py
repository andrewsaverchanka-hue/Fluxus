"""Thin Python wrapper around the Rust core.

Drop-in entry point for the GUI: returns the same dict shape that
`tools/reference.py` derives from the Python LogAnalyzer.
"""

import json

import fluxus_native


def parse_lima(path: str, include_events: bool = False) -> dict:
    """Parse a devices_server log (plain or .gz) with the Rust implementation.

    `include_events=False` (default) omits per-session event lists: they are
    ~6x of the payload and only the drill-down view needs them.
    """
    return json.loads(fluxus_native.parse_lima_json(str(path), include_events))


def parse_lima_text(text: str, include_events: bool = False) -> dict:
    """Parse log contents already held in memory (streamed/remote logs)."""
    return json.loads(fluxus_native.parse_lima_json_str(text, include_events))
