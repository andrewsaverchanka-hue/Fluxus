"""Drop-in replacements for the Python analyzers, backed by the Rust core.

The GUI does:

    analyzer = LogAnalyzer()
    analyzer.parse_log_file(path)
    analyzer.build_sessions()
    stats = analyzer.get_stats()          # 19-tuple
    w2_ll.correlate_with_frontend(w2_front.sessions)

These classes implement that contract exactly, but the parsing happens in
Rust. Sessions are lightweight Python objects hydrated from the Rust JSON, so
the rest of the GUI (tables, result window, serializer) keeps working unchanged.

Enable per-run with an env var in the GUI, or import these directly.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

import fluxus_native

__all__ = [
    "AnalysisSerializer",
    "LimaAnalyzer",
    "W2FrontendAdapter",
    "W2LowLevelAdapter",
    "install",
]


# --------------------------------------------------------------------------
# hydration: Rust returns JSON, but the GUI expects the same Python types the
# old parsers produced — datetime objects and numeric dict keys, not strings.
# --------------------------------------------------------------------------

def _dt(value):
    """String -> datetime (leaves datetime/None untouched).

    Two formats occur: ISO from Rust (`2026-11-13T10:49:30`) and the
    human format the Lima collections carry (`13/11/2026 10:49:30`).
    """
    if not value or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.strptime(value, "%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return value


def _num(key):
    """'20' -> 20, '3.0' -> 3.0 (what the Python parser produced)."""
    if isinstance(key, (int, float)):
        return key
    try:
        return int(key)
    except (TypeError, ValueError):
        pass
    try:
        return float(key)
    except (TypeError, ValueError):
        return key


def _numeric_keys(mapping):
    return {_num(k): v for k, v in (mapping or {}).items()}


def _box_key(key):
    """'1|20' -> (1, 20), the tuple key the Python stats used."""
    if not isinstance(key, str) or "|" not in key:
        return key
    a, _, b = key.partition("|")
    return (_num(a), _num(b))


def _hydrate_lima_event(ev):
    if "dt_obj" in ev:
        ev["dt_obj"] = _dt(ev["dt_obj"])
    return ev


def _hydrate_frontend_data(d):
    """In-place: datetimes + numeric keys for a frontend parse payload."""
    for sess in d.get("sessions") or []:
        sess["start_dt"] = _dt(sess.get("start_dt_iso"))
        sess["end_dt"] = _dt(sess.get("end_dt_iso"))
        for ev in sess.get("events") or []:
            ev["dt"] = _dt(ev.get("dt"))
    for cash in d.get("cash_events") or []:
        cash["dt"] = _dt(cash.get("dt_iso"))
        for key in ("boxes_delta", "reject_delta", "raw_by_nominal",
                    "loaded_by_nominal"):
            if key in cash:
                cash[key] = _numeric_keys(cash.get(key))
    st = d.get("stats") or {}
    st["denom_dispensed"] = _numeric_keys(st.get("denom_dispensed"))
    st["boxes_by_device"] = {
        dev: {_box_key(k): v for k, v in (boxes or {}).items()}
        for dev, boxes in (st.get("boxes_by_device") or {}).items()
    }
    return d


def _hydrate_lowlevel_data(d):
    """In-place: datetimes for a low-level payload (sessions + health)."""
    for sess in d.get("sessions") or []:
        sess["start_dt"] = _dt(sess.get("start_dt_iso"))
        sess["end_dt"] = _dt(sess.get("end_dt_iso"))
        for ev in sess.get("events") or []:
            ev["dt"] = _dt(ev.get("dt"))
    for records in (d.get("lcdm_health") or [],
                    d.get("lcdm_health_changes") or []):
        for entry in records:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                state = entry[1]
                if isinstance(state, dict) and "dt" in state:
                    state["dt"] = _dt(state["dt"])
    return d


# --------------------------------------------------------------------------
# streaming support: the Rust API takes a path, so a stream is spooled once
# --------------------------------------------------------------------------

def _materialise(source):
    """Return (path, temp_path_or_None) for a path or a file-like object."""
    if hasattr(source, "read"):
        fd = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        try:
            data = source.read()
            if isinstance(data, bytes):
                data = data.decode("utf-8", "ignore")
            fd.write(data)
        finally:
            fd.close()
        return Path(fd.name), Path(fd.name)
    return Path(source), None


# --------------------------------------------------------------------------
# Lima
# --------------------------------------------------------------------------

class BanknoteSession:
    __slots__ = (
        "session_id", "events", "start_ts", "first_date", "first_time", "uuid",
        "status", "denomination", "currency", "device_model", "device_serial",
        "closed", "error_reason",
    )

    def __init__(self, d):
        for k in self.__slots__:
            setattr(self, k, d.get(k))
        self.events = d.get("events") or []

    _DENOM_MAP = {100: 1, 500: 5, 1000: 10, 2000: 20, 5000: 50, 10000: 100}

    def get_display_denom(self):
        if not self.denomination:
            return 0
        return self._DENOM_MAP.get(self.denomination, self.denomination / 100)


class LimaAnalyzer:
    """Port of `parsers.lima_parser.LogAnalyzer` backed by Rust."""

    def __init__(self):
        self.sessions = []
        self.stats = {}
        self.stats_by_device = {}
        self.device_models = {}
        self.collections = []
        self.debug_collections = []
        self.debug_info = []
        self.debug_stacked = []
        self.bnv_events_count = {}
        self.bnv_events_by_type = {}
        self.bnv_events = []
        self._data = None
        self._path = None
        # GUI feeds one file per call into the SAME analyzer and expects the
        # results to accumulate (Python parses into the same object). So we
        # collect paths and parse them all at once in build_sessions().
        self._paths = []
        self._temp_paths = []
        self._parser = fluxus_native.LimaParser()

    def parse_log_file(self, file_path_or_stream, progress_callback=None,
                       display_name=None):
        path, temp = _materialise(file_path_or_stream)
        self._path = path
        self._paths.append(str(path))
        if temp is not None:
            self._temp_paths.append(temp)
        # Разбираем сразу: это даёт честный прогресс по файлам.
        self._parser.add_file(str(path))

    def _cleanup_temp(self):
        for temp in self._temp_paths:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        self._temp_paths.clear()

    def parse_log_files(self, paths):
        """Пакетный разбор (файлы разбираются параллельно на стороне Rust)."""
        self._paths = [str(p) for p in paths]
        if not self._paths:
            return
        self._parser.add_files_parallel(self._paths)

    def build_sessions(self):
        if self._paths:
            raw = self._parser.result_json(True)
            self._data = json.loads(raw)
        if self._data is None:      # данные могли прийти из .anl.gz
            return
        d = self._data

        # --- hydrate types the GUI relies on ---
        for sess in d["sessions"]:
            for ev in sess.get("events") or []:
                _hydrate_lima_event(ev)
        d["bnv_events"] = [_hydrate_lima_event(e) for e in d.get("bnv_events") or []]
        for coll in d["collections"]:
            coll["dt"] = _dt(coll.get("dt"))
            coll["notes"] = _numeric_keys(coll.get("notes"))
        for key in ("first_collection", "last_collection"):
            coll = d.get(key)
            if coll:
                coll["dt"] = _dt(coll.get("dt"))
                coll["notes"] = _numeric_keys(coll.get("notes"))
        if d.get("between_stats"):
            d["between_stats"] = {
                curr: _numeric_keys(denoms)
                for curr, denoms in d["between_stats"].items()
            }
        d["stats"] = {c: _numeric_keys(v) for c, v in (d.get("stats") or {}).items()}
        d["stats_by_device"] = {
            model: {
                serial: {c: _numeric_keys(v) for c, v in (currs or {}).items()}
                for serial, currs in (serials or {}).items()
            }
            for model, serials in (d.get("stats_by_device") or {}).items()
        }

        self.sessions = [BanknoteSession(s) for s in d["sessions"]]
        self.stats = d["stats"]
        self.stats_by_device = d["stats_by_device"]
        self.device_models = d["device_models"]
        self.collections = d["collections"]
        self.debug_collections = d["debug_collections"]
        self.debug_info = d["debug_info"]
        self.debug_stacked = d["debug_stacked"]
        self.bnv_events = d["bnv_events"]
        self.bnv_events_count = d["bnv_events_count"]
        self.bnv_events_by_type = d["bnv_events_by_type"]

    def get_between_collections_stats(self):
        d = self._data or {}
        return (
            # Python returns None for all of these when there are no
            # collections; keep that shape rather than substituting zeros.
            d.get("between_stats"), d.get("between_count"),
            d.get("between_sum"), d.get("first_collection"),
            d.get("last_collection"),
        )

    def get_stats(self):
        d = self._data or {}
        (between_stats, between_count, between_sum,
         first_coll, last_coll) = self.get_between_collections_stats()
        return (
            self.stats, d.get("total_count", 0), d.get("total_sum", 0),
            d.get("lines_found", 0), d.get("lines_parsed", 0),
            self.debug_info, self.sessions, self.debug_stacked,
            between_stats, between_count, between_sum,
            first_coll, last_coll,
            self.collections, self.debug_collections,
            self.bnv_events_count, self.stats_by_device,
            self.device_models, self.bnv_events_by_type,
        )


# --------------------------------------------------------------------------
# W2 shared
# --------------------------------------------------------------------------

class _Session:
    """Attribute bag with the handful of helper methods the GUI calls."""

    def __init__(self, d, path=None):
        self._d = d
        self._path = path
        for k, v in d.items():
            setattr(self, k, v)

    def __getattr__(self, item):
        # GUI tolerates missing optional attributes better than AttributeError
        return None

    def duration_sec(self):
        if self.start_dt_iso and self.end_dt_iso:
            from datetime import datetime
            a = datetime.fromisoformat(self.start_dt_iso)
            b = datetime.fromisoformat(self.end_dt_iso)
            return (b - a).total_seconds()
        return 0


class W2WithdrawSession(_Session):
    def has_suspicious_match(self):
        return any(bool(f) for f in (self.suspicion_flags or []))

    def suspicious_count(self):
        return sum(1 for f in (self.suspicion_flags or []) if f)

    def all_suspicion_flags(self):
        out = []
        for f in (self.suspicion_flags or []):
            out.extend(f)
        return out


class W2LowLevelSession(_Session):
    def total_requested(self):
        return sum(c.get("amount", 0) for c in (self.commands or []))

    def total_dispensed(self):
        return sum(r.get("dispensed", 0) for r in (self.results or []))

    def total_rejected(self):
        return sum(r.get("rejected", 0) for r in (self.results or []))


class W2CashEvent(_Session):
    def date_str(self):
        return self.dt_iso[8:10] + "/" + self.dt_iso[5:7] + "/" + self.dt_iso[:4]

    def time_str(self):
        return self.dt_iso[11:19]

    def is_outflow(self):
        return self.event_type in ("collection", "withdraw_report")

    def is_inflow(self):
        return self.event_type == "cash_in"

    def type_display(self):
        return {
            "collection": "Инкассация",
            "withdraw_report": "Изъятие (Withdraw)",
            "cash_in": "Пополнение (Cash in)",
        }.get(self.event_type, self.event_type)


# --------------------------------------------------------------------------
# W2 frontend
# --------------------------------------------------------------------------

class W2FrontendAdapter:
    def __init__(self):
        self.sessions = []
        self.cash_events = []
        self.dispensers = {}
        self.lines_processed = 0
        self.lines_matched = 0
        self._data = None
        self._path = None
        self._paths = []
        self._temp_paths = []
        self._parser = fluxus_native.FrontendParser()

    def parse_log_file(self, file_path_or_stream, progress_callback=None,
                       display_name=None):
        path, temp = _materialise(file_path_or_stream)
        self._path = path
        self._paths.append(str(path))
        if temp is not None:
            self._temp_paths.append(temp)
        # Разбираем сразу: это даёт честный прогресс по файлам.
        self._parser.add_file(str(path))

    def _cleanup_temp(self):
        for temp in self._temp_paths:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        self._temp_paths.clear()

    def parse_log_files(self, paths):
        """Пакетный разбор (frontend — последовательно, он и так быстрый)."""
        for path in paths:
            self.parse_log_file(path)

    def build_sessions(self):
        if getattr(self, "_batch_parsed", False) and self._data is not None:
            pass
        elif self._paths:
            raw = self._parser.result_json(True)
            self._data = json.loads(raw)
        if self._data is None:      # данные могли прийти из .anl.gz
            return
        d = self._data

        _hydrate_frontend_data(d)

        self.sessions = [W2WithdrawSession(s, self._path) for s in d["sessions"]]
        for sess in self.sessions:
            sess._paths = list(self._paths)
            sess._owner = self
        self.cash_events = [W2CashEvent(c, self._path) for c in d["cash_events"]]
        self.dispensers = d["stats"]["dispensers"]
        self.lines_processed = d["stats"]["lines_processed"]
        self.lines_matched = d["stats"]["lines_matched"]

    def get_last_cash_event(self, before_dt, event_type=None):
        best = None
        for e in self.cash_events:
            from datetime import datetime
            dt = datetime.fromisoformat(e.dt_iso)
            if dt < before_dt and (event_type is None or e.event_type == event_type):
                if best is None or dt > datetime.fromisoformat(best.dt_iso):
                    best = e
        return best

    def get_stats(self):
        return self._data["stats"] if self._data else {}


# --------------------------------------------------------------------------
# W2 low level + correlation
# --------------------------------------------------------------------------

class W2LowLevelAdapter:
    def __init__(self):
        self.sessions = []
        self.lcdm_health = []
        self.lcdm_health_changes = []
        self.dispensers = {}
        self.lines_processed = 0
        self.lines_matched = 0
        self._data = None
        self._path = None
        self._paths = []
        self._temp_paths = []
        self._parser = fluxus_native.LowLevelParser()

    def parse_log_file(self, file_path_or_stream, progress_callback=None,
                       display_name=None):
        path, temp = _materialise(file_path_or_stream)
        self._path = path
        self._paths.append(str(path))
        if temp is not None:
            self._temp_paths.append(temp)
        # Разбираем сразу: это даёт честный прогресс по файлам.
        self._parser.add_file(str(path))

    def _cleanup_temp(self):
        for temp in self._temp_paths:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        self._temp_paths.clear()

    def parse_log_files(self, paths):
        """Пакетный разбор (файлы разбираются параллельно на стороне Rust)."""
        self._paths = [str(p) for p in paths]
        if not self._paths:
            return
        self._parser.add_files_parallel(self._paths)

    def build_sessions(self):
        if self._paths:
            raw = self._parser.result_json(True)
            self._data = json.loads(raw)
        if self._data is None:      # данные могли прийти из .anl.gz
            return
        d = self._data

        _hydrate_lowlevel_data(d)

        self.sessions = [W2LowLevelSession(s, self._path) for s in d["sessions"]]
        self.lcdm_health = d["lcdm_health"]
        self.lcdm_health_changes = d["lcdm_health_changes"]
        self.dispensers = d["dispensers"]
        self.lines_processed = d["lines_processed"]
        self.lines_matched = d["lines_matched"]

    def correlate_with_frontend(self, frontend_sessions,
                                start_window_before_sec=10,
                                end_window_after_sec=60,
                                next_session_buffer_sec=2):
        """Correlate natively, then hydrate the Python session objects.

        The signature matches the Python analyzer; the GUI passes the list of
        frontend sessions, so we take the frontend log path from the first
        session and re-run the pair through Rust.
        """
        if not frontend_sessions:
            return
        owner = getattr(frontend_sessions[0], "_owner", None)
        if owner is not None and getattr(owner, "_paths", None) and self._paths:
            data = json.loads(
                fluxus_native.correlate_parsers(
                    owner._parser, self._parser, True)
            )
        else:
            front_paths = getattr(frontend_sessions[0], "_paths", None)
            if not front_paths:
                front_path = getattr(frontend_sessions[0], "_path", None)
                front_paths = [str(front_path)] if front_path else []
            if not front_paths or not self._paths:
                return
            data = json.loads(
                fluxus_native.parse_w2_correlated_files(
                    list(front_paths), list(self._paths), True)
            )
        ll_data = _hydrate_lowlevel_data(data["lowlevel"])
        ll_payload = ll_data["sessions"]
        fe_payload = data["frontend"]["sessions"]

        # Rebuild LL sessions from the correlated payload so indices line up.
        self.sessions = [W2LowLevelSession(s, self._path) for s in ll_payload]
        self.lcdm_health = ll_data.get("lcdm_health") or []
        self.lcdm_health_changes = ll_data.get("lcdm_health_changes") or []
        self.dispensers = ll_data.get("dispensers") or {}
        self.lines_processed = ll_data.get("lines_processed", 0)
        self.lines_matched = ll_data.get("lines_matched", 0)

        for sess, payload in zip(frontend_sessions, fe_payload):
            sess.lowlevel_sessions = [
                self.sessions[i] for i in payload.get("lowlevel_sessions") or []
                if 0 <= i < len(self.sessions)
            ]
            sess.suspicion_flags = payload.get("suspicion_flags") or []
        self._data = ll_data

    def get_stats(self):
        if not self._data:
            return {}
        from collections import defaultdict

        # Descriptions for LCDM codes come from the project's own tables when
        # they are importable; the counters are the important part.
        classify_status = classify_4000 = None
        try:
            from core.utils import (classify_lcdm_status,
                                    classify_lcdm4000_code)
            classify_status, classify_4000 = classify_lcdm_status, classify_lcdm4000_code
        except Exception:  # noqa: BLE001 - adapter also runs standalone
            pass

        by_model = defaultdict(int)
        status_counts = defaultdict(int)
        lcdm_codes = defaultdict(int)
        for s in self.sessions:
            by_model[s.model or "unknown"] += 1
            status_counts[s.status] += 1
            if s.lcdm_status_code is not None:
                desc = ""
                if classify_status:
                    desc, _ = classify_status(s.lcdm_status_code)
                lcdm_codes[f"0x{s.lcdm_status_code:02X} {desc}".strip()] += 1
            elif s.lcdm_device_code is not None:
                desc = ""
                if classify_4000:
                    desc, _ = classify_4000(s.lcdm_device_code, s.error_detail)
                lcdm_codes[f"code={s.lcdm_device_code} {desc}".strip()] += 1

        return {
            "total": len(self.sessions),
            "by_model": dict(by_model),
            "status_counts": dict(status_counts),
            "lcdm_codes_count": dict(lcdm_codes),
            "dispensers": self.dispensers,
            "lines_processed": self.lines_processed,
            "lines_matched": self.lines_matched,
            "health_records": len(self.lcdm_health),
            "health_changes": len(self.lcdm_health_changes),
        }


# --------------------------------------------------------------------------
# .anl.gz bundles
# --------------------------------------------------------------------------

class AnalysisSerializer:
    """Port of `parsers.serializer.AnalysisSerializer`, backed by Rust.

    `save` re-parses the three logs natively from the paths the analyzers were
    created from. `load` returns the same `(lima, frontend, lowlevel, metadata)`
    tuple the Python implementation returns, with correlation links restored.
    """

    VERSION = 2

    @staticmethod
    def save(file_path, analyzer, w2_analyzer, w2_lowlevel, source_files):
        groups = [getattr(a, "_paths", None)
                  for a in (analyzer, w2_analyzer, w2_lowlevel)]
        if any(not g for g in groups):
            raise RuntimeError(
                "Rust serializer needs analyzers created by parse_log_file(); "
                "loaded bundles cannot be re-saved without their source logs"
            )
        fluxus_native.save_bundle_files(
            str(file_path), list(groups[0]), list(groups[1]), list(groups[2]),
            [str(p) for p in source_files],
        )

    @staticmethod
    def load(file_path):
        data = json.loads(fluxus_native.load_bundle_json(str(file_path)))

        lima = LimaAnalyzer()
        lima._data = data["lima"]
        lima.build_sessions()

        frontend = W2FrontendAdapter()
        frontend._data = data["frontend"]
        frontend.build_sessions()

        lowlevel = W2LowLevelAdapter()
        lowlevel._data = data["lowlevel"]
        lowlevel.build_sessions()

        for sess, payload in zip(frontend.sessions, data["frontend"]["sessions"]):
            sess.lowlevel_sessions = [
                lowlevel.sessions[i]
                for i in (payload.get("lowlevel_sessions") or [])
                if 0 <= i < len(lowlevel.sessions)
            ]

        return lima, frontend, lowlevel, data["metadata"]


def install(patch_module=None):
    """Monkey-patch the parsers package with the Rust-backed classes.

    Call before the GUI imports the pages, e.g. from main.py:

        from fluxus_adapter import install; install()
    """
    import parsers.lima_parser as lima
    import parsers.w2_frontend_parser as fe
    import parsers.w2_lowlevel_parser as ll
    import parsers.serializer as ser

    lima.LogAnalyzer = LimaAnalyzer
    fe.W2FrontendAnalyzer = W2FrontendAdapter
    ll.W2LowLevelAnalyzer = W2LowLevelAdapter
    ser.AnalysisSerializer = AnalysisSerializer
    return True
