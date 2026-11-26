#!/usr/bin/env python3
"""Round-trip a bundle through the adapter (save -> load -> verify links)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

import fluxus_adapter as A  # noqa: E402

lima_log, front_log, low_log = sys.argv[1], sys.argv[2], sys.argv[3]
out = Path("fixtures/adapter.anl.gz")

lima = A.LimaAnalyzer(); lima.parse_log_file(lima_log); lima.build_sessions()
fe = A.W2FrontendAdapter(); fe.parse_log_file(front_log); fe.build_sessions()
ll = A.W2LowLevelAdapter(); ll.parse_log_file(low_log); ll.build_sessions()
ll.correlate_with_frontend(fe.sessions)

A.AnalysisSerializer.save(out, lima, fe, ll, [lima_log, front_log, low_log])
lima2, fe2, ll2, meta = A.AnalysisSerializer.load(out)

assert len(lima2.sessions) == len(lima.sessions)
assert len(fe2.sessions) == len(fe.sessions)
assert len(ll2.sessions) == len(ll.sessions)
assert lima2.get_stats()[1] == lima.get_stats()[1]
links_before = sum(len(s.lowlevel_sessions) for s in fe.sessions)
links_after = sum(len(s.lowlevel_sessions) for s in fe2.sessions)
assert links_before == links_after, (links_before, links_after)
assert all(isinstance(x, A.W2LowLevelSession) for s in fe2.sessions for x in s.lowlevel_sessions)

print(f"bundle {meta['version']} saved_at={meta['saved_at']}")
print(f"lima={len(lima2.sessions)} frontend={len(fe2.sessions)} lowlevel={len(ll2.sessions)} links={links_after}")
print("serializer adapter OK")
