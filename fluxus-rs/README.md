# Fluxus — Rust core

A Rust implementation of Fluxus' log parsing, wired into the existing PySide6
application through PyO3. The GUI is untouched; the analyzers are swapped.

## Status

| Piece | State |
|---|---|
| `lima_parser` (devices_server / Lima) | **Ported**, parity verified |
| `w2_lowlevel_parser` (LCDM2000/LCDM4000) | **Ported**, parity verified |
| `w2_frontend_parser` (skills-battle-frontend) | **Ported**, parity verified |
| frontend ↔ low-level correlation + suspicion flags | **Ported**, parity verified |
| Models, constants, classifiers, utils | **Ported** |
| Drop-in adapter (`LogAnalyzer`/`W2*Analyzer` API) | **Done**, GUI-sequence tested |
| PyO3 bindings (all three + correlated) | **Working** (abi3: 3.11–3.14) |
| `serializer.py` (`.anl.gz` save/load, v1 + v2) | **Ported**, both directions verified |

## Layout

```
crates/fluxus-core/src/
  lima.rs          Lima/devices_server analyzer
  w2_lowlevel.rs   LCDM2000 + LCDM4000 dispenser analyzer
  w2_frontend.rs   frontend withdraw/cash-event analyzer
  w2_models.rs     W2 session/event models
  models.rs        Lima session/event models
  constants.rs     denomination + BNV + status tables
  utils.rs         ANSI strip, log readers, classifiers
crates/fluxus-cli/   `fluxus-parse <log> --kind lima|frontend|lowlevel`
crates/fluxus-py/    PyO3 module `fluxus_native`
py/fluxus_adapter.py drop-in analyzer classes used by the GUI
py/fluxus_fast.py    thin `parse_lima` helper
tools/               generators, Python reference runner, diff, benchmarks
WINDOWS_BUILD.md     building the .pyd + exe on Windows
```

## Verify everything

```bash
./tools/parity_all.sh
```

Runs eight checks: Lima, Lima-gzip, frontend, lowlevel, correlated, the adapter
replaying the GUI's exact call sequence, and `.anl.gz` compatibility in both
directions. Each parser is compared structurally against the Python
implementation — aggregates, every session, every event, collections, cash
events, suspicion flags.

Last run:

```
== lima ==                        PARITY OK
== lima (gzip) ==                 PARITY OK
== frontend ==                    PARITY OK
== lowlevel ==                    PARITY OK
== correlated ==                  PARITY OK
== adapter ==                     adapter OK
== serializer (both directions) == SERIALIZER PARITY OK
== serializer via adapter ==      serializer adapter OK
```

## Benchmark

Single core, release build. The honest breakdown matters: a large part of the
Lima speedup is an algorithmic fix that the Python app should have regardless.

### Lima, 400,000-line / 79 MB log

| Path | Time | vs shipping Python |
|---|---|---|
| Python as shipped (quadratic de-dupe) | 60.1 s | 1× |
| Python with de-dupe fixed to O(1) | 9.31 s | 6.5× |
| **Rust core** | **2.58 s** | **23×** |

So: ~6.5× comes from fixing the O(n²) collection de-dupe, and a further ~3.6×
from Rust. Both are real; only one of them requires a rewrite.

### W2 (60,000 lines each)

| Parser | Python | Rust | Speedup |
|---|---|---|---|
| frontend | 0.66 s | 0.34 s | 1.9× |
| lowlevel | 0.54 s | 0.49 s | 1.1× |

The W2 parsers barely benefit: their lines are short, Python's regex engine is
C, and the Rust port allocates a JSON map per event (faithful to the Python
dicts). If W2 parsing is not a bottleneck for you, keeping those two in Python
is a defensible choice — the adapter supports mixing.

Reproduce with `./tools/bench_all.sh` (Lima) and the commands in
`tools/parity_all.sh` (W2 fixtures).

## Real-log validation

Run against production W2 logs (13 rotated `devices_server` files + 2 frontend
logs), with no modifications to the originals:

| Check | Data | Result |
|---|---|---|
| lowlevel, `devices_server.log.7.gz` | 1.07M lines / 190 MB decompressed, 80 sessions | **identical** |
| lowlevel, logs `.8`, `.13`, `.1` | 46–92 sessions each | **identical** |
| frontend, `skills-battle-frontend.log` | 20 MB, 192 sessions | **identical** |
| frontend, `…log.1.gz` | smaller rotation | **identical** |
| correlated (Oct 19 pair) | 192 frontend / 61 low-level, 30 linked, 14 flagged | **identical** |
| `.anl.gz` both directions | real trio | **identical** |

Timings on the same hardware:

| Workload | Python | Rust | Speedup |
|---|---|---|---|
| one 190 MB `devices_server` log | 10.08 s | 1.60 s | **6.3×** |
| one 20 MB frontend log | 0.61 s | 0.17 s | **3.6×** |
| all 13 rotated device logs | ~131 s | ~20 s | **~6.5×** |

Re-run on a new batch with:

```bash
./tools/real_log_check.sh <devices_server.log[.gz]> <skills-battle-frontend.log[.gz]>
```

Two things the real data taught us:

* **Lima finds zero events in these logs, correctly.** They are W2 terminal
  logs: 0 `DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_` lines, 389k `LCDM` lines.
  The Lima parser is for bill-acceptor terminals, not these.
* **Pair logs by time range.** Correlating the frontend log (Oct 18–19) with
  `devices_server.log.7` (Oct 17) yields zero links — and should. Pairing it with
  `devices_server.log.1` (Oct 19) yields 30 linked transactions and 14 suspicion
  flags. Both implementations agree exactly in either case.

### Lima logs (bill-acceptor terminal)

Seven rotations from a Lima terminal (`NV9 Spectral` / ITL), extracted from a
244 MB support archive:

| Check | Result |
|---|---|
| all 7 rotations vs Python | **identical** |
| totals | 229 banknotes, 265 sessions, 1 collection, $6,472 |

| Workload | Python | Rust | Speedup |
|---|---|---|---|
| 42 MB plain log | 1,647 ms | 434 ms | 3.8× |
| 6 gzipped rotations | 27,784 ms | 7,180 ms | 3.9× |
| **all 7** | **29.8 s** | **8.0 s** | **3.7×** |

This batch caught a genuine fidelity bug that synthetic data missed: with no
collections, `between_count`/`between_sum` must be `None`, not `0`. The GUI
shows "no data" rather than "zero money" because of that distinction. Fixed in
`lima.rs` (`Option<i64>`/`Option<f64>`) and in the adapter.

It also exposed a flaw in the *test harness*: the reference mapped an empty
between-stats dict to `None` (`if between_stats else None`), which would have
hidden the reverse mismatch. The comparison is now `is not None`.

### Performance note

Real logs are ~85% unrelated noise; the low-level parser was running its
25-regex cascade over every line (5.1 s per file). A two-substring prefilter
(`Dispense` / `Command: Status`, which every branch requires) cut that to
1.6 s with identical output, and switching `flate2` to the `zlib-rs` backend
trimmed the decompression share further.

## Installing it into the app

Step-by-step, beginner-level instructions (Windows build, `main.py` edit,
PyInstaller flags, troubleshooting, rollback): **`INTEGRATION_GUIDE.md`**.
Quick self-check after installing: `python check_rust_core.py`.

## Using the adapter

```python
import sys
sys.path.insert(0, "fluxus-rs/py")

from fluxus_adapter import LimaAnalyzer, W2FrontendAdapter, W2LowLevelAdapter

lima = LimaAnalyzer()
lima.parse_log_file("devices_server.log")
lima.build_sessions()
stats = lima.get_stats()          # the same 19-tuple the GUI unpacks

fe = W2FrontendAdapter(); fe.parse_log_file("frontend.log"); fe.build_sessions()
ll = W2LowLevelAdapter(); ll.parse_log_file("devices_server.log"); ll.build_sessions()
ll.correlate_with_frontend(fe.sessions)
```

To switch the whole app over, call `fluxus_adapter.install()` **before** the GUI
modules are imported (they bind the analyzer classes at import time). See
`WINDOWS_BUILD.md` §3 for the exact `main.py` edit and the
`FLUXUS_PYTHON_PARSERS=1` fallback switch.

## Bundles (`.anl.gz`)

`serializer.rs` writes and reads format version 2 (and reads version 1).
Compatibility is proven in both directions by `tools/serializer_parity.py`:

1. Rust-saved and Python-saved bundles are semantically identical (only
   `saved_at` differs).
2. Rust loads a *Python*-written bundle and re-saves it unchanged.
3. Python loads a *Rust*-written bundle and recovers every session, correlation
   link and cash event.

From Python, `fluxus_adapter.AnalysisSerializer` has the same static API as the
original (`save(path, lima, front, lowlevel, source_files)` and `load(path)`),
and `install()` patches it into `parsers.serializer`.

One caveat: the Rust `save` needs the source log paths (it re-parses natively),
so a bundle that was *loaded* cannot be re-saved by the adapter — the Python
implementation can, because it keeps the objects. This only matters for
"load → edit → save", which the GUI doesn't currently do.

## Parity notes

### Intentional deviations

* Collection de-duplication is O(1) instead of a linear scan (same results).
* The current year is cached at first use instead of calling `now()` per line.
  A process left running across New Year would group old lines by the old year.
* Regexes are compiled once at startup rather than per call.
* Maps are ordered (`BTreeMap`) for deterministic JSON.

### Porting hazards found

* **Python lookaheads in the cash-event regexes.**
  `outByNotes...(?=\\?"|$)` has no Rust equivalent. The port captures to the
  next quote and trims a trailing escape backslash, which reproduces Python's
  boundaries. Covered by the frontend parity test.
* **`lines_processed` off-by-one.** Python always flushes a remainder line, so a
  file ending in `\n` yields one extra empty line. Reproduced explicitly so the
  counters match the old app exactly.
* **Frontend timestamps are `MM/DD/YYYY`.** Unlike the Lima parser there is no
  day/month fallback — a day-first frontend log silently parses to nothing.
  Worth knowing when reading customer logs.
* **Correlation depends on `login:` appearing on follow-up lines.** Without it
  `_active_trx` is never set and the transaction silently drops out of the
  session list.

## Next steps

1. Run the parity suite against scrubbed **real** customer logs; synthetic logs
   only prove the code paths I could imagine match.
2. Wire `install()` into `main.py` and ship a beta behind an env flag.
3. Update `updater/github_updater.py`: with a signed exe the VBS/BAT/`schtasks`
   chain is unnecessary and should be deleted.
