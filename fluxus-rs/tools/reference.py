#!/usr/bin/env python3
"""Reference runner: parses a log with the *existing Python* code and dumps the
same JSON shape the Rust CLI produces.

Usage:
    reference.py <log> [--kind lima|frontend|lowlevel]

This is the oracle for the migration. Any behavioural difference between
Python and Rust shows up as a diff in `compare.py`.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2] / "fluxus-py"
sys.path.insert(0, str(REPO))

from parsers.lima_parser import LogAnalyzer  # noqa: E402
from parsers.w2_frontend_parser import W2FrontendAnalyzer  # noqa: E402
from parsers.w2_lowlevel_parser import W2LowLevelAnalyzer  # noqa: E402


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None


def nested_int_dict(d):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[str(k)] = nested_int_dict(v)
        else:
            out[str(k)] = v
    return out


# ---------------------------------------------------------------- lima

def event_dict(e):
    return {
        "timestamp": e.get("timestamp", 0),
        "date": e.get("date"),
        "time": e.get("time"),
        "event_type": e.get("event_type", ""),
        "event_name": e.get("event_name"),
        "validator_type": e.get("validator_type"),
        "banknote_uuid": e.get("banknote_uuid"),
        "banknote_id": e.get("banknote_id", "N/A"),
        "banknote_raw": e.get("banknote_raw"),
        "currency": e.get("currency"),
        "description": e.get("description"),
        "device_model": e.get("device_model"),
        "device_serial": e.get("device_serial"),
        "device_vendor": e.get("device_vendor"),
        "raw": e.get("raw", ""),
        "is_bnv": e.get("is_bnv", False),
    }


def session_dict(s):
    return {
        "session_id": s.session_id,
        "start_ts": s.start_ts,
        "first_date": s.first_date,
        "first_time": s.first_time,
        "uuid": s.uuid,
        "status": s.status,
        "denomination": s.denomination,
        "currency": s.currency,
        "device_model": s.device_model,
        "device_serial": s.device_serial,
        "closed": s.closed,
        "error_reason": s.error_reason,
        "events": [event_dict(e) for e in s.events],
    }


def collection_dict(c):
    return {
        "dt": c["dt"].strftime("%d/%m/%Y %H:%M:%S"),
        "date_str": c["date_str"],
        "time_str": c["time_str"],
        "notes": {str(k): v for k, v in c["notes"].items()},
        "raw": c["raw"],
    }


def run_lima(path):
    analyzer = LogAnalyzer()
    analyzer.parse_log_file(path)
    analyzer.build_sessions()
    (stats, total_count, total_sum, lines_found, lines_parsed, debug_info,
     sessions, debug_stacked, between_stats, between_count, between_sum,
     first_coll, last_coll, collections, debug_collections,
     bnv_events_count, stats_by_device, device_models,
     bnv_events_by_type) = analyzer.get_stats()
    return {
        "stats": nested_int_dict(stats),
        "stats_by_device": nested_int_dict(stats_by_device),
        "device_models": {str(k): v for k, v in device_models.items()},
        "total_count": total_count,
        "total_sum": total_sum,
        "lines_found": lines_found,
        "lines_parsed": lines_parsed,
        "sessions": [session_dict(s) for s in sessions],
        "bnv_events": [event_dict(e) for e in analyzer.bnv_events],
        "collections": [collection_dict(c) for c in collections],
        "debug_collections": debug_collections,
        "debug_info": debug_info,
        "debug_stacked": debug_stacked,
        "bnv_events_count": nested_int_dict(bnv_events_count),
        "bnv_events_by_type": nested_int_dict(bnv_events_by_type),
        "between_stats": nested_int_dict(between_stats) if between_stats is not None else None,
        "between_count": between_count,
        "between_sum": between_sum,
        "first_collection": collection_dict(first_coll) if first_coll is not None else None,
        "last_collection": collection_dict(last_coll) if last_coll is not None else None,
    }


# ---------------------------------------------------------------- w2 shared

def w2_event_dict(e):
    # Low-level events only carry `dt`; frontend ones also carry date/time.
    dt = e.get("dt")
    out = {
        "kind": e["kind"],
        "dt": iso(dt),
        "date": e.get("date") or dt.strftime("%d/%m/%Y"),
        "time": e.get("time") or dt.strftime("%H:%M:%S"),
        "raw": e["raw"],
    }
    for k, v in e.items():
        if k not in ("kind", "dt", "date", "time", "raw"):
            out[k] = v
    return out


def lowlevel_payload(a):
    sessions = []
    for s in a.sessions:
        sessions.append({
            "model": s.model,
            "req_id": s.req_id,
            "job_id": s.job_id,
            "idx": s.idx,
            "serial_path": s.serial_path,
            "start_date_str": s.start_date_str,
            "start_time_str": s.start_time_str,
            "commands": s.commands,
            "results": s.results,
            "status": s.status,
            "error_detail": s.error_detail,
            "lcdm_status_code": s.lcdm_status_code,
            "lcdm_device_code": s.lcdm_device_code,
            "events": [w2_event_dict(e) for e in s.events],
            "closed": s.closed,
            "start_dt_iso": iso(s.start_dt),
            "end_dt_iso": iso(s.end_dt),
        })
    return {
        "sessions": sessions,
        "lcdm_health": [[k, dict(v, dt=iso(v["dt"]))] for k, v in a.lcdm_health],
        "lcdm_health_changes": [
            [k, dict(v, dt=iso(v["dt"]))] for k, v in a.lcdm_health_changes
        ],
        "dispensers": a.dispensers,
        "lines_processed": a.lines_processed,
        "lines_matched": a.lines_matched,
    }


def run_lowlevel(path):
    a = W2LowLevelAnalyzer()
    a.parse_log_file(path)
    a.build_sessions()
    return lowlevel_payload(a)


def run_correlated(front_path, low_path):
    fe = W2FrontendAnalyzer()
    fe.parse_log_file(front_path)
    fe.build_sessions()
    ll = W2LowLevelAnalyzer()
    ll.parse_log_file(low_path)
    ll.build_sessions()
    ll.correlate_with_frontend(fe.sessions)
    index = {id(s): i for i, s in enumerate(ll.sessions)}
    payload = frontend_payload(fe)
    for sess, raw in zip(payload["sessions"], fe.sessions):
        sess["lowlevel_sessions"] = [index[id(x)] for x in raw.lowlevel_sessions]
    return {"frontend": payload, "lowlevel": lowlevel_payload(ll)}


def frontend_payload(a):
    sessions = []
    for s in a.sessions:
        sessions.append({
            "trx": s.trx,
            "events": [w2_event_dict(e) for e in s.events],
            "start_dt_iso": iso(s.start_dt),
            "end_dt_iso": iso(s.end_dt),
            "start_date_str": s.start_date_str,
            "start_time_str": s.start_time_str,
            "coupon": s.coupon,
            "user_login": s.user_login,
            "group_uuid": s.group_uuid,
            "balance_id": s.balance_id,
            "transaction_id": s.transaction_id,
            "requested_amount": s.requested_amount,
            "dispensed_amount": s.dispensed_amount,
            "commands": s.commands,
            "dispensed_events": s.dispensed_events,
            "boxes": s.boxes,
            "cassette_snapshot": s.cassette_snapshot,
            "status": s.status,
            "error_detail": s.error_detail,
            "retry_count": s.retry_count,
            "payout_coupon_status": s.payout_coupon_status,
            "recovery_coupon": s.recovery_coupon,
            "receipt_printed": s.receipt_printed,
            "dispenser_device_id": s.dispenser_device_id,
            "dispenser_model": s.dispenser_model,
            "dispenser_vendor": s.dispenser_vendor,
            "lowlevel_sessions": [],
            "suspicion_flags": s.suspicion_flags,
            "cassette_mismatch": s.cassette_mismatch,
            "cassette_mismatch_details": {
                str(k): v for k, v in s.cassette_mismatch_details.items()
            },
            "cassette_mismatch_collective": s.cassette_mismatch_collective,
            "cassette_mismatch_group_size": s.cassette_mismatch_group_size,
        })

    cash = []
    for e in a.cash_events:
        cash.append({
            "event_type": e.event_type,
            "dt_iso": iso(e.dt),
            "timestamp": e.timestamp,
            "boxes_delta": {str(k): v for k, v in e.boxes_delta.items()},
            "reject_delta": {str(k): v for k, v in e.reject_delta.items()},
            "raw_by_nominal": {str(k): v for k, v in e.raw_by_nominal.items()},
            "total_cents": e.total_cents,
            "raw_details": e.raw_details,
        })

    st = a.get_stats()
    st["boxes_by_device"] = {
        dev: {f"{k[0]}|{k[1]}": v for k, v in boxes.items()}
        for dev, boxes in st["boxes_by_device"].items()
    }
    return {
        "sessions": sessions,
        "cash_events": cash,
        "stats": nested_int_dict(st),
    }


def run_frontend(path):
    a = W2FrontendAnalyzer()
    a.parse_log_file(path)
    a.build_sessions()
    return frontend_payload(a)


def main():
    args = sys.argv[1:]
    kind = "lima"
    low = None
    if "--kind" in args:
        i = args.index("--kind")
        kind = args[i + 1]
        del args[i:i + 2]
    if "--low" in args:
        i = args.index("--low")
        low = args[i + 1]
        del args[i:i + 2]
    if not args:
        print("usage: reference.py <log> [--kind ...] [--low <lowlevel log>]", file=sys.stderr)
        return 2
    path = args[0]

    if kind == "correlate":
        result = run_correlated(path, low)
    else:
        result = {
            "lima": run_lima,
            "frontend": run_frontend,
            "lowlevel": run_lowlevel,
        }[kind](path)

    json.dump(result, sys.stdout, sort_keys=True, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
