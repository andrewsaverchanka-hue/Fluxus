#!/usr/bin/env python3
"""Replay the GUI's parse sequence through the adapter (no Qt needed).

Mirrors gui/pages/parser_page.py:113-132 and the analysis window's
`analyzer.get_stats()` unpack, so a break in the drop-in contract fails here
instead of inside the GUI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

import fluxus_adapter as A  # noqa: E402

lima_log, front_log, low_log = sys.argv[1], sys.argv[2], sys.argv[3]

analyzer = A.LimaAnalyzer()
w2_front = A.W2FrontendAdapter()
w2_ll = A.W2LowLevelAdapter()

analyzer.parse_log_file(lima_log)
w2_front.parse_log_file(front_log)
w2_ll.parse_log_file(low_log)

analyzer.build_sessions()
w2_front.build_sessions()
w2_ll.build_sessions()
w2_ll.correlate_with_frontend(w2_front.sessions)

(stats, total_count, total_sum, lines_found, lines_parsed, debug_info,
 sessions, debug_stacked, between_stats, between_count, between_sum,
 first_coll, last_coll, collections, debug_collections,
 bnv_events_count, stats_by_device, device_models, bnv_events_by_type) = analyzer.get_stats()

assert len(sessions) == len(analyzer.sessions)
assert isinstance(stats, dict) and stats, "lima stats empty"
assert total_count > 0, "lima counted nothing"

fe_stats = w2_front.get_stats()
ll_stats = w2_ll.get_stats()
assert fe_stats["total"] > 0, "frontend sessions empty"
assert ll_stats["total"] > 0, "lowlevel sessions empty"

flagged = sum(1 for s in w2_front.sessions if s.has_suspicious_match())
linked = sum(1 for s in w2_front.sessions if s.lowlevel_sessions)

print(f"lima sessions={len(sessions)} counted={total_count} sum={total_sum}")
print(f"frontend sessions={fe_stats['total']} linked={linked} flagged={flagged}")
print(f"lowlevel sessions={ll_stats['total']} by_model={ll_stats['by_model']}")
print("adapter OK")
