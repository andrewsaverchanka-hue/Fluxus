#!/usr/bin/env python3
"""Replay the GUI's rendering access patterns without Qt.

This exists because the GUI does things the JSON shape alone doesn't reveal:
  ev['dt'].strftime(...)            -> datetimes, not ISO strings
  sorted(sessions, key=x.start_dt)  -> datetime or None, never a string
  sorted(c['notes'].keys())         -> numeric keys, or the order is wrong
  stats arithmetic                  -> int/float keys

Run: gui_smoke_check.py <lima log> <frontend log> <lowlevel log>
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

import fluxus_adapter as A  # noqa: E402

lima_log, front_log, low_log = sys.argv[1], sys.argv[2], sys.argv[3]


def check_dt(value, where):
    assert value is None or isinstance(value, datetime), (
        f"{where}: expected datetime, got {type(value).__name__}: {value!r}"
    )


# ---------------- Lima ----------------
lima = A.LimaAnalyzer()
lima.parse_log_file(lima_log)
lima.build_sessions()

for s in lima.sessions:
    for ev in s.events:
        check_dt(ev.get("dt_obj"), "lima event dt_obj")
for ev in lima.bnv_events:
    check_dt(ev.get("dt_obj"), "lima bnv event dt_obj")
for c in lima.collections:
    check_dt(c.get("dt"), "lima collection dt")
    for d in c["notes"]:
        assert isinstance(d, (int, float)), f"collection note key is {type(d).__name__}"
for curr, denoms in lima.stats.items():
    for d in denoms:
        assert isinstance(d, (int, float)), f"stats key is {type(d).__name__}"
        _ = d * denoms[d]          # arithmetic the GUI may do
print(f"lima OK: sessions={len(lima.sessions)} bnv={len(lima.bnv_events)} "
      f"collections={len(lima.collections)}")

# ---------------- W2 ----------------
fe = A.W2FrontendAdapter()
fe.parse_log_file(front_log)
fe.build_sessions()
ll = A.W2LowLevelAdapter()
ll.parse_log_file(low_log)
ll.build_sessions()
ll.correlate_with_frontend(fe.sessions)

# line 762 / 1190: sorting sessions
sorted(fe.sessions, key=lambda x: x.start_dt or datetime.min)
sorted(ll.sessions, key=lambda x: x.start_dt or datetime.min)
for s in fe.sessions:
    check_dt(s.start_dt, "frontend session start_dt")
    check_dt(s.end_dt, "frontend session end_dt")

# lines 999-1004: rendering every LL event
rendered = 0
for s in ll.sessions:
    for ev in s.events:
        dt = ev.get("dt")
        check_dt(dt, f"ll event dt (kind={ev.get('kind')})")
        _ = dt.strftime("%H:%M:%S") if dt else "?"
        rendered += 1
for s in fe.sessions:
    for ev in s.events:
        check_dt(ev.get("dt"), "frontend event dt")

# lines 1348-1363: health records
for records in (ll.lcdm_health, ll.lcdm_health_changes):
    for kind, state in records:
        dt = state.get("dt")
        check_dt(dt, "health state dt")
        _ = dt.strftime("%d/%m/%Y") if dt else "?"

# cash events
for c in fe.cash_events:
    check_dt(c.dt, "cash event dt")

print(f"w2 OK: frontend={len(fe.sessions)} lowlevel={len(ll.sessions)} "
      f"events_rendered={rendered} health={len(ll.lcdm_health)} "
      f"cash={len(fe.cash_events)}")

# ---------------- saved analysis round-trip ----------------
out = Path("fixtures/gui_smoke.anl.gz")
A.AnalysisSerializer.save(out, lima, fe, ll, [lima_log, front_log, low_log])
lima2, fe2, ll2, meta = A.AnalysisSerializer.load(out)

for s in fe2.sessions:
    check_dt(s.start_dt, "loaded frontend session start_dt")
    for ev in s.events:
        check_dt(ev.get("dt"), "loaded frontend event dt")
for s in ll2.sessions:
    for ev in s.events:
        check_dt(ev.get("dt"), "loaded ll event dt")
for c in lima2.collections:
    check_dt(c.get("dt"), "loaded collection dt")
sorted(fe2.sessions, key=lambda x: x.start_dt or datetime.min)

print(f"loaded bundle OK: version={meta['version']} "
      f"fe={len(fe2.sessions)} ll={len(ll2.sessions)} lima={len(lima2.sessions)}")
print("GUI SMOKE CHECK OK")
