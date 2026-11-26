//! Analysis bundle serialization (`.anl.gz`).
//!
//! Port of `parsers/serializer.py`. The format is a compatibility contract:
//! files written by this module must load in the Python app, and files written
//! by the Python app must round-trip through this module unchanged. Both
//! directions are covered by `tools/serializer_parity.py`.
//!
//! Format version 2:
//! ```text
//! { "version": 2, "saved_at": iso, "source_files": [...],
//!   "lima": {...}, "w2_frontend": {...}, "w2_lowlevel": {...} }
//! ```

use std::collections::BTreeMap;
use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;

use chrono::{Local, NaiveDateTime};
use flate2::read::MultiGzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use serde_json::{json, Map, Value};

use crate::lima::LogAnalyzer;
use crate::models::{BanknoteSession, Event};
use crate::w2_frontend::W2FrontendAnalyzer;
use crate::w2_lowlevel::W2LowLevelAnalyzer;
use crate::w2_models::{
    BoxEntry, JsonMap, W2CashEvent, W2Event, W2LowLevelSession, W2WithdrawSession,
};

pub const VERSION: i64 = 2;

pub struct Loaded {
    pub lima: LogAnalyzer,
    pub frontend: W2FrontendAnalyzer,
    pub lowlevel: W2LowLevelAnalyzer,
    pub version: i64,
    pub saved_at: Option<String>,
    pub source_files: Vec<String>,
}

fn io_err(msg: impl std::fmt::Display) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, msg.to_string())
}

fn iso(dt: Option<NaiveDateTime>) -> Value {
    match dt {
        Some(d) => json!(d.format("%Y-%m-%dT%H:%M:%S").to_string()),
        None => Value::Null,
    }
}

fn parse_iso(s: Option<&str>) -> Option<NaiveDateTime> {
    let s = s?;
    NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S")
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f"))
        .ok()
}

/// Python's `total_sum` is an int when every denomination was mapped and a
/// float otherwise. We only have f64, so emit an integer when it is integral —
/// that matches the common case byte for byte.
fn num(v: f64) -> Value {
    if v.fract() == 0.0 && v.abs() < 9e15 {
        json!(v as i64)
    } else {
        json!(v)
    }
}

fn str_or_null(v: &Option<String>) -> Value {
    match v {
        Some(s) => json!(s),
        None => Value::Null,
    }
}

fn int_or_null(v: Option<i64>) -> Value {
    match v {
        Some(i) => json!(i),
        None => Value::Null,
    }
}

// ------------------------------------------------------------------ events

/// Lima event dict. Python builds two different shapes depending on `is_bnv`.
fn lima_event_value(e: &Event) -> Value {
    let mut m = Map::new();
    m.insert("timestamp".into(), json!(e.timestamp));
    m.insert("date".into(), str_or_null(&e.date));
    m.insert("time".into(), str_or_null(&e.time));
    if e.from_bnv_error {
        // Python's injected BNV-error event has no dt_obj/raw/device fields.
        m.insert("event_type".into(), json!(e.event_type));
        m.insert("banknote_uuid".into(), str_or_null(&e.banknote_uuid));
        m.insert("banknote_id".into(), json!(e.banknote_id));
        m.insert("banknote_raw".into(), int_or_null(e.banknote_raw));
        m.insert("currency".into(), str_or_null(&e.currency));
        m.insert("description".into(), str_or_null(&e.description));
        m.insert("is_bnv".into(), json!(e.is_bnv));
        return Value::Object(m);
    }
    m.insert("dt_obj".into(), iso(e.dt));
    m.insert("event_type".into(), json!(e.event_type));
    if e.is_bnv {
        m.insert("event_name".into(), str_or_null(&e.event_name));
        m.insert("validator_type".into(), str_or_null(&e.validator_type));
    } else {
        m.insert("device_model".into(), str_or_null(&e.device_model));
        m.insert("device_serial".into(), str_or_null(&e.device_serial));
        m.insert("device_vendor".into(), str_or_null(&e.device_vendor));
    }
    m.insert("banknote_uuid".into(), str_or_null(&e.banknote_uuid));
    m.insert("banknote_id".into(), json!(e.banknote_id));
    m.insert("banknote_raw".into(), int_or_null(e.banknote_raw));
    m.insert("currency".into(), str_or_null(&e.currency));
    m.insert("description".into(), str_or_null(&e.description));
    m.insert("raw".into(), json!(e.raw));
    m.insert("is_bnv".into(), json!(e.is_bnv));
    Value::Object(m)
}

fn w2_event_value(e: &W2Event, with_date_time: bool) -> Value {
    let mut m = Map::new();
    m.insert("kind".into(), json!(e.kind));
    m.insert("dt".into(), json!(e.dt_iso));
    if with_date_time {
        m.insert("date".into(), json!(e.date));
        m.insert("time".into(), json!(e.time));
    }
    m.insert("raw".into(), json!(e.raw));
    for (k, v) in &e.data {
        m.insert(k.clone(), v.clone());
    }
    Value::Object(m)
}

// ------------------------------------------------------------------- save

fn lima_value(a: &LogAnalyzer) -> Value {
    let sessions: Vec<Value> = a
        .sessions
        .iter()
        .map(|s| {
            json!({
                "session_id": s.session_id,
                "events": s.events.iter().map(lima_event_value).collect::<Vec<_>>(),
                "start_ts": s.start_ts,
                "first_date": s.first_date,
                "first_time": s.first_time,
                "uuid": s.uuid,
                "status": s.status,
                "denomination": int_or_null(s.denomination),
                "currency": str_or_null(&s.currency),
                "device_model": s.device_model,
                "device_serial": s.device_serial,
                "closed": s.closed,
                "error_reason": str_or_null(&s.error_reason),
            })
        })
        .collect();

    let collections: Vec<Value> = a
        .collections
        .iter()
        .map(|c| {
            json!({
                "dt": iso(c.dt_val),
                "date_str": c.date_str,
                "time_str": c.time_str,
                "notes": c.notes,
                "raw": c.raw,
            })
        })
        .collect();

    json!({
        "sessions": sessions,
        "bnv_events": a.bnv_events.iter().map(lima_event_value).collect::<Vec<_>>(),
        "collections": collections,
        "device_models": a.device_models,
        "stats": a.stats,
        "stats_by_device": a.stats_by_device,
        "bnv_events_count": a.bnv_events_count,
        "bnv_events_by_type": a.bnv_events_by_type,
        "total_count": a.total_count,
        "total_sum": num(a.total_sum),
        "lines_found": a.lines_found,
        "lines_parsed": a.lines_parsed,
    })
}

fn frontend_value(a: &W2FrontendAnalyzer, ll_len: usize) -> Value {
    let sessions: Vec<Value> = a
        .sessions
        .iter()
        .map(|s| {
            let ll_indices: Vec<usize> = s
                .lowlevel_sessions
                .iter()
                .copied()
                .filter(|&i| i < ll_len)
                .collect();
            json!({
                "trx": s.trx,
                "events": s.events.iter().map(|e| w2_event_value(e, true)).collect::<Vec<_>>(),
                "start_dt": iso(s.start_dt),
                "end_dt": iso(s.end_dt),
                "start_date_str": s.start_date_str,
                "start_time_str": s.start_time_str,
                "coupon": str_or_null(&s.coupon),
                "user_login": str_or_null(&s.user_login),
                "group_uuid": str_or_null(&s.group_uuid),
                "balance_id": int_or_null(s.balance_id),
                "transaction_id": int_or_null(s.transaction_id),
                "requested_amount": s.requested_amount,
                "dispensed_amount": s.dispensed_amount,
                "commands": s.commands,
                "dispensed_events": s.dispensed_events,
                "boxes": s.boxes,
                "cassette_snapshot": s.cassette_snapshot,
                "status": s.status,
                "error_detail": str_or_null(&s.error_detail),
                "retry_count": s.retry_count,
                "payout_coupon_status": str_or_null(&s.payout_coupon_status),
                "recovery_coupon": str_or_null(&s.recovery_coupon),
                "receipt_printed": s.receipt_printed,
                "dispenser_device_id": str_or_null(&s.dispenser_device_id),
                "dispenser_model": str_or_null(&s.dispenser_model),
                "dispenser_vendor": str_or_null(&s.dispenser_vendor),
                "lowlevel_indices": ll_indices,
                "suspicion_flags": s.suspicion_flags,
                "cassette_mismatch": s.cassette_mismatch,
                "cassette_mismatch_details": s.cassette_mismatch_details,
                "cassette_mismatch_collective": s.cassette_mismatch_collective,
                "cassette_mismatch_group_size": s.cassette_mismatch_group_size,
            })
        })
        .collect();

    let cash: Vec<Value> = a
        .cash_events
        .iter()
        .map(|e| {
            let map_str_keys = |m: &BTreeMap<i64, i64>| -> Map<String, Value> {
                m.iter().map(|(k, v)| (k.to_string(), json!(v))).collect()
            };
            json!({
                "event_type": e.event_type,
                "dt": iso(Some(e.dt)),
                "boxes_delta": map_str_keys(&e.boxes_delta),
                "reject_delta": map_str_keys(&e.reject_delta),
                "raw_by_nominal": map_str_keys(&e.raw_by_nominal),
                "loaded_by_nominal": map_str_keys(&e.loaded_by_nominal),
                "total_loaded_cents": e.total_loaded_cents,
                "total_cents": e.total_cents,
                "raw_details": e.raw_details,
            })
        })
        .collect();

    json!({
        "sessions": sessions,
        "cash_events": cash,
        "dispensers": a.dispensers,
        "lines_processed": a.lines_processed,
        "lines_matched": a.lines_matched,
    })
}

fn lowlevel_value(a: &W2LowLevelAnalyzer) -> Value {
    let sessions: Vec<Value> = a
        .sessions
        .iter()
        .map(|s| {
            json!({
                "model": str_or_null(&s.model),
                "req_id": int_or_null(s.req_id),
                "job_id": int_or_null(s.job_id),
                "idx": str_or_null(&s.idx),
                "serial_path": str_or_null(&s.serial_path),
                "start_dt": iso(s.start_dt),
                "end_dt": iso(s.end_dt),
                "start_date_str": s.start_date_str,
                "start_time_str": s.start_time_str,
                "commands": s.commands,
                "results": s.results,
                "status": s.status,
                "error_detail": str_or_null(&s.error_detail),
                "lcdm_status_code": int_or_null(s.lcdm_status_code.map(i64::from)),
                "lcdm_device_code": int_or_null(s.lcdm_device_code),
                "events": s.events.iter().map(|e| w2_event_value(e, false)).collect::<Vec<_>>(),
                "closed": s.closed,
            })
        })
        .collect();

    let health: Vec<Value> = a.lcdm_health.iter().map(|(k, v)| json!([k, v])).collect();
    let health_changes: Vec<Value> = a
        .lcdm_health_changes
        .iter()
        .map(|(k, v)| json!([k, v]))
        .collect();

    json!({
        "sessions": sessions,
        "lcdm_health": health,
        "lcdm_health_changes": health_changes,
        "dispensers": a.dispensers,
        "lines_processed": a.lines_processed,
        "lines_matched": a.lines_matched,
    })
}

/// Build the full bundle document.
pub fn bundle_value(
    lima: &LogAnalyzer,
    frontend: &W2FrontendAnalyzer,
    lowlevel: &W2LowLevelAnalyzer,
    source_files: &[String],
) -> Value {
    let now = Local::now().naive_local();
    json!({
        "version": VERSION,
        "saved_at": now.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        "source_files": source_files,
        "lima": lima_value(lima),
        "w2_frontend": frontend_value(frontend, lowlevel.sessions.len()),
        "w2_lowlevel": lowlevel_value(lowlevel),
    })
}

pub fn save(
    path: &Path,
    lima: &LogAnalyzer,
    frontend: &W2FrontendAnalyzer,
    lowlevel: &W2LowLevelAnalyzer,
    source_files: &[String],
) -> std::io::Result<()> {
    let value = bundle_value(lima, frontend, lowlevel, source_files);
    let text = serde_json::to_string(&value).map_err(io_err)?;
    let file = File::create(path)?;
    let mut enc = GzEncoder::new(file, Compression::default());
    enc.write_all(text.as_bytes())?;
    enc.finish()?;
    Ok(())
}

// ------------------------------------------------------------------- load

fn read_bundle(path: &Path) -> std::io::Result<Value> {
    let file = File::open(path)?;
    let mut dec = MultiGzDecoder::new(file);
    let mut text = String::new();
    dec.read_to_string(&mut text)?;
    serde_json::from_str(&text).map_err(io_err)
}

fn get<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    v.get(key).filter(|x| !x.is_null())
}

fn s_opt(v: &Value, key: &str) -> Option<String> {
    get(v, key).and_then(|x| x.as_str()).map(str::to_string)
}

fn i_opt(v: &Value, key: &str) -> Option<i64> {
    get(v, key).and_then(|x| x.as_i64())
}

fn i_or(v: &Value, key: &str, default: i64) -> i64 {
    i_opt(v, key).unwrap_or(default)
}

fn b_or(v: &Value, key: &str, default: bool) -> bool {
    get(v, key).and_then(|x| x.as_bool()).unwrap_or(default)
}

fn str_map(v: Option<&Value>) -> BTreeMap<String, i64> {
    let mut out = BTreeMap::new();
    if let Some(Value::Object(m)) = v {
        for (k, val) in m {
            if let Some(n) = val.as_i64() {
                out.insert(k.clone(), n);
            }
        }
    }
    out
}

fn nested_str_map(v: Option<&Value>) -> BTreeMap<String, BTreeMap<String, i64>> {
    let mut out = BTreeMap::new();
    if let Some(Value::Object(m)) = v {
        for (k, inner) in m {
            out.insert(k.clone(), str_map(Some(inner)));
        }
    }
    out
}

fn json_map(v: &Value) -> JsonMap {
    v.as_object().cloned().unwrap_or_default()
}

fn map_vec(v: &Value, key: &str) -> Vec<JsonMap> {
    v.get(key)
        .and_then(Value::as_array)
        .map(|a| a.iter().map(json_map).collect())
        .unwrap_or_default()
}

fn value_vec(v: &Value, key: &str) -> Vec<Value> {
    v.get(key)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

fn lima_event_from(v: &Value) -> Event {
    Event {
        timestamp: i_or(v, "timestamp", 0),
        date: s_opt(v, "date"),
        time: s_opt(v, "time"),
        dt: parse_iso(s_opt(v, "dt_obj").as_deref()),
        event_type: s_opt(v, "event_type").unwrap_or_default(),
        event_name: s_opt(v, "event_name"),
        validator_type: s_opt(v, "validator_type"),
        banknote_uuid: s_opt(v, "banknote_uuid"),
        banknote_id: s_opt(v, "banknote_id").unwrap_or_else(|| "N/A".into()),
        banknote_raw: i_opt(v, "banknote_raw"),
        currency: s_opt(v, "currency"),
        description: s_opt(v, "description"),
        device_model: s_opt(v, "device_model"),
        device_serial: s_opt(v, "device_serial"),
        device_vendor: s_opt(v, "device_vendor"),
        raw: s_opt(v, "raw").unwrap_or_default(),
        is_bnv: b_or(v, "is_bnv", false),
        // Python's injected error events are identifiable by their reduced
        // key set; recover the flag so a re-save reproduces them exactly.
        from_bnv_error: b_or(v, "is_bnv", false)
            && get(v, "dt_obj").is_none()
            && get(v, "raw").is_none(),
    }
}

fn w2_event_from(v: &Value, with_date_time: bool) -> W2Event {
    let dt = parse_iso(s_opt(v, "dt").as_deref()).unwrap_or_else(|| {
        NaiveDateTime::parse_from_str("1970-01-01T00:00:00", "%Y-%m-%dT%H:%M:%S").unwrap()
    });
    let date = if with_date_time {
        s_opt(v, "date").unwrap_or_else(|| dt.format("%d/%m/%Y").to_string())
    } else {
        dt.format("%d/%m/%Y").to_string()
    };
    let time = if with_date_time {
        s_opt(v, "time").unwrap_or_else(|| dt.format("%H:%M:%S").to_string())
    } else {
        dt.format("%H:%M:%S").to_string()
    };
    let mut data = Map::new();
    if let Some(Value::Object(m)) = Some(v) {
        for (k, val) in m {
            if !matches!(k.as_str(), "kind" | "dt" | "date" | "time" | "raw") {
                data.insert(k.clone(), val.clone());
            }
        }
    }
    W2Event {
        kind: s_opt(v, "kind").unwrap_or_default(),
        dt_iso: dt.format("%Y-%m-%dT%H:%M:%S").to_string(),
        dt,
        date,
        time,
        raw: s_opt(v, "raw").unwrap_or_default(),
        data,
    }
}

fn banknote_session_from(v: &Value) -> BanknoteSession {
    let events: Vec<Event> = value_vec(v, "events").iter().map(lima_event_from).collect();
    BanknoteSession {
        session_id: i_or(v, "session_id", 0) as usize,
        events,
        start_ts: i_or(v, "start_ts", 0),
        first_date: s_opt(v, "first_date").unwrap_or_else(|| "?".into()),
        first_time: s_opt(v, "first_time").unwrap_or_else(|| "?".into()),
        uuid: s_opt(v, "uuid").unwrap_or_else(|| "N/A".into()),
        status: s_opt(v, "status").unwrap_or_else(|| "INCOMPLETE".into()),
        denomination: i_opt(v, "denomination"),
        currency: s_opt(v, "currency"),
        device_model: s_opt(v, "device_model").unwrap_or_else(|| "Unknown".into()),
        device_serial: s_opt(v, "device_serial").unwrap_or_else(|| "N/A".into()),
        closed: b_or(v, "closed", false),
        error_reason: s_opt(v, "error_reason"),
    }
}

impl BoxEntry {
    fn from_value(v: &Value) -> Self {
        BoxEntry {
            tt_id: i_or(v, "tt_id", 0),
            r#box: i_or(v, "box", 0),
            nominal: i_or(v, "nominal", 0),
            count: i_or(v, "count", 0),
            is_rejected: i_or(v, "is_rejected", 0),
        }
    }
}

fn frontend_session_from(v: &Value, ll_len: usize) -> W2WithdrawSession {
    let mut s = W2WithdrawSession::new(&s_opt(v, "trx").unwrap_or_default());
    s.events = value_vec(v, "events")
        .iter()
        .map(|e| w2_event_from(e, true))
        .collect();
    s.start_dt = parse_iso(s_opt(v, "start_dt").as_deref());
    s.end_dt = parse_iso(s_opt(v, "end_dt").as_deref());
    s.start_dt_iso = s
        .start_dt
        .map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string());
    s.end_dt_iso = s.end_dt.map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string());
    s.start_date_str = s_opt(v, "start_date_str").unwrap_or_else(|| "?".into());
    s.start_time_str = s_opt(v, "start_time_str").unwrap_or_else(|| "?".into());
    s.coupon = s_opt(v, "coupon");
    s.user_login = s_opt(v, "user_login");
    s.group_uuid = s_opt(v, "group_uuid");
    s.balance_id = i_opt(v, "balance_id");
    s.transaction_id = i_opt(v, "transaction_id");
    s.requested_amount = i_or(v, "requested_amount", 0);
    s.dispensed_amount = i_or(v, "dispensed_amount", 0);
    s.commands = value_vec(v, "commands");
    s.dispensed_events = value_vec(v, "dispensed_events");
    s.boxes = value_vec(v, "boxes")
        .iter()
        .map(BoxEntry::from_value)
        .collect();
    s.cassette_snapshot = value_vec(v, "cassette_snapshot");
    s.status = s_opt(v, "status").unwrap_or_else(|| "INCOMPLETE".into());
    s.error_detail = s_opt(v, "error_detail");
    s.retry_count = i_or(v, "retry_count", 0);
    s.payout_coupon_status = s_opt(v, "payout_coupon_status");
    s.recovery_coupon = s_opt(v, "recovery_coupon");
    s.receipt_printed = b_or(v, "receipt_printed", false);
    s.dispenser_device_id = s_opt(v, "dispenser_device_id");
    s.dispenser_model = s_opt(v, "dispenser_model");
    s.dispenser_vendor = s_opt(v, "dispenser_vendor");
    s.cassette_mismatch = b_or(v, "cassette_mismatch", false);
    if let Some(Value::Object(m)) = get(v, "cassette_mismatch_details") {
        for (k, val) in m {
            s.cassette_mismatch_details.insert(k.clone(), val.clone());
        }
    }
    s.cassette_mismatch_collective = b_or(v, "cassette_mismatch_collective", false);
    s.cassette_mismatch_group_size = i_or(v, "cassette_mismatch_group_size", 1);
    s.suspicion_flags = value_vec(v, "suspicion_flags")
        .iter()
        .map(|f| {
            f.as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default()
        })
        .collect();
    s.lowlevel_sessions = value_vec(v, "lowlevel_indices")
        .iter()
        .filter_map(Value::as_u64)
        .map(|i| i as usize)
        .filter(|&i| i < ll_len)
        .collect();
    while s.suspicion_flags.len() < s.lowlevel_sessions.len() {
        s.suspicion_flags.push(Vec::new());
    }
    s
}

fn cash_event_from(v: &Value) -> W2CashEvent {
    let int_map = |key: &str| -> BTreeMap<i64, i64> {
        let mut out = BTreeMap::new();
        if let Some(Value::Object(m)) = get(v, key) {
            for (k, val) in m {
                if let (Ok(ki), Some(n)) = (k.parse::<i64>(), val.as_i64()) {
                    out.insert(ki, n);
                }
            }
        }
        out
    };
    let dt = parse_iso(s_opt(v, "dt").as_deref()).unwrap_or_else(|| {
        NaiveDateTime::parse_from_str("1970-01-01T00:00:00", "%Y-%m-%dT%H:%M:%S").unwrap()
    });
    W2CashEvent {
        event_type: s_opt(v, "event_type").unwrap_or_else(|| "collection".into()),
        dt,
        dt_iso: dt.format("%Y-%m-%dT%H:%M:%S").to_string(),
        timestamp: 0.0,
        boxes_delta: int_map("boxes_delta"),
        reject_delta: int_map("reject_delta"),
        raw_by_nominal: int_map("raw_by_nominal"),
        loaded_by_nominal: int_map("loaded_by_nominal"),
        total_loaded_cents: i_or(v, "total_loaded_cents", 0),
        total_cents: i_or(v, "total_cents", 0),
        raw_details: s_opt(v, "raw_details").unwrap_or_default(),
    }
}

fn lowlevel_session_from(v: &Value) -> W2LowLevelSession {
    let mut s = W2LowLevelSession {
        model: s_opt(v, "model"),
        req_id: i_opt(v, "req_id"),
        job_id: i_opt(v, "job_id"),
        idx: s_opt(v, "idx"),
        serial_path: s_opt(v, "serial_path"),
        start_dt: parse_iso(s_opt(v, "start_dt").as_deref()),
        end_dt: parse_iso(s_opt(v, "end_dt").as_deref()),
        start_dt_iso: None,
        end_dt_iso: None,
        start_date_str: s_opt(v, "start_date_str").unwrap_or_else(|| "?".into()),
        start_time_str: s_opt(v, "start_time_str").unwrap_or_else(|| "?".into()),
        commands: map_vec(v, "commands"),
        results: map_vec(v, "results"),
        status: s_opt(v, "status").unwrap_or_else(|| "INCOMPLETE".into()),
        error_detail: s_opt(v, "error_detail"),
        lcdm_status_code: i_opt(v, "lcdm_status_code").map(|i| i as u8),
        lcdm_device_code: i_opt(v, "lcdm_device_code"),
        events: value_vec(v, "events")
            .iter()
            .map(|e| w2_event_from(e, false))
            .collect(),
        closed: b_or(v, "closed", false),
    };
    s.start_dt_iso = s
        .start_dt
        .map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string());
    s.end_dt_iso = s.end_dt.map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string());
    s
}

fn health_vec(v: &Value, key: &str) -> Vec<(String, Value)> {
    v.get(key)
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|pair| {
                    let arr = pair.as_array()?;
                    Some((
                        arr.first()?.as_str()?.to_string(),
                        arr.get(1).cloned().unwrap_or(Value::Null),
                    ))
                })
                .collect()
        })
        .unwrap_or_default()
}

pub fn load(path: &Path) -> std::io::Result<Loaded> {
    let data = read_bundle(path)?;
    let version = data.get("version").and_then(Value::as_i64).unwrap_or(0);
    if version != 1 && version != VERSION {
        return Err(io_err(format!(
            "unsupported analysis version {} (expected 1 or {})",
            version, VERSION
        )));
    }

    let lima_d = data.get("lima").cloned().unwrap_or_else(|| json!({}));
    let mut lima = LogAnalyzer::new();
    lima.sessions = value_vec(&lima_d, "sessions")
        .iter()
        .map(banknote_session_from)
        .collect();
    lima.bnv_events = value_vec(&lima_d, "bnv_events")
        .iter()
        .map(lima_event_from)
        .collect();
    lima.collections = value_vec(&lima_d, "collections")
        .iter()
        .map(|c| {
            let dt_val = parse_iso(s_opt(c, "dt").as_deref());
            crate::lima::Collection {
                dt: dt_val.map(|d| d.format("%d/%m/%Y %H:%M:%S").to_string()),
                dt_val,
                date_str: s_opt(c, "date_str").unwrap_or_default(),
                time_str: s_opt(c, "time_str").unwrap_or_default(),
                notes: str_map(get(c, "notes")),
                raw: s_opt(c, "raw").unwrap_or_default(),
            }
        })
        .collect();
    if let Some(Value::Object(m)) = get(&lima_d, "device_models") {
        for (serial, dm) in m {
            lima.device_models.insert(
                serial.clone(),
                crate::lima::DeviceModelInfo {
                    model: s_opt(dm, "model").unwrap_or_default(),
                    vendor: s_opt(dm, "vendor").unwrap_or_else(|| "?".into()),
                },
            );
        }
    }
    lima.stats = nested_str_map(get(&lima_d, "stats"));
    if let Some(Value::Object(models)) = get(&lima_d, "stats_by_device") {
        for (model, serials) in models {
            if let Some(Value::Object(smap)) = Some(serials) {
                for (serial, currs) in smap {
                    let mut curr_out = BTreeMap::new();
                    if let Some(Value::Object(cmap)) = Some(currs) {
                        for (curr, denoms) in cmap {
                            curr_out.insert(curr.clone(), str_map(Some(denoms)));
                        }
                    }
                    lima.stats_by_device
                        .entry(model.clone())
                        .or_default()
                        .insert(serial.clone(), curr_out);
                }
            }
        }
    }
    lima.bnv_events_count = str_map(get(&lima_d, "bnv_events_count"));
    if let Some(Value::Object(m)) = get(&lima_d, "bnv_events_by_type") {
        for (k, v) in m {
            lima.bnv_events_by_type.insert(k.clone(), str_map(Some(v)));
        }
    }
    lima.total_count = i_or(&lima_d, "total_count", 0);
    lima.total_sum = get(&lima_d, "total_sum")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    lima.lines_found = i_or(&lima_d, "lines_found", 0);
    lima.lines_parsed = i_or(&lima_d, "lines_parsed", 0);

    // Low-level first: frontend sessions reference it by index.
    let ll_d = data
        .get("w2_lowlevel")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let mut lowlevel = W2LowLevelAnalyzer::new();
    lowlevel.sessions = value_vec(&ll_d, "sessions")
        .iter()
        .map(lowlevel_session_from)
        .collect();
    lowlevel.lcdm_health = health_vec(&ll_d, "lcdm_health");
    lowlevel.lcdm_health_changes = health_vec(&ll_d, "lcdm_health_changes");
    lowlevel.dispensers = get(&ll_d, "dispensers")
        .and_then(Value::as_object)
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();
    lowlevel.lines_processed = i_or(&ll_d, "lines_processed", 0);
    lowlevel.lines_matched = i_or(&ll_d, "lines_matched", 0);

    let fe_d = data
        .get("w2_frontend")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let mut frontend = W2FrontendAnalyzer::new();
    frontend.sessions = value_vec(&fe_d, "sessions")
        .iter()
        .map(|s| frontend_session_from(s, lowlevel.sessions.len()))
        .collect();
    frontend.cash_events = value_vec(&fe_d, "cash_events")
        .iter()
        .map(cash_event_from)
        .collect();
    frontend.cash_events.sort_by_key(|e| e.dt);
    frontend.dispensers = get(&fe_d, "dispensers")
        .and_then(Value::as_object)
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();
    frontend.lines_processed = i_or(&fe_d, "lines_processed", 0);
    frontend.lines_matched = i_or(&fe_d, "lines_matched", 0);

    Ok(Loaded {
        lima,
        frontend,
        lowlevel,
        version,
        saved_at: s_opt(&data, "saved_at"),
        source_files: value_vec(&data, "source_files")
            .iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_a_minimal_bundle() {
        let mut lima = LogAnalyzer::new();
        lima.parse_str(
            "15/10 14:11:07 DBG type: DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_STACKED \
             banknote: { banknote: 2000 } currency: \"USD\" banknote_uuid: \"u1\" \
             device: { id: \"SN1\", model: \"BV20\" } timestamp_ms: 1730000000000",
        );
        lima.build_sessions();

        let mut ll = W2LowLevelAnalyzer::new();
        ll.parse_str(
            "15/10 14:11:07 DBG serialPath=/dev/ttyS0 Handle Dispense Request: reqID=1 commands=1",
        );
        ll.build_sessions();

        let mut fe = W2FrontendAnalyzer::new();
        fe.parse_str("[10/15/2026, 8:01:08 AM] DEBUG payoutCouponAPI trx 7f000001-c1a2-4b3d-9e8f-000000000001 login: 5");
        fe.build_sessions();

        let path = std::env::temp_dir().join("fluxus_roundtrip_test.anl.gz");
        save(&path, &lima, &fe, &ll, &["a.log".to_string()]).unwrap();
        let loaded = load(&path).unwrap();

        assert_eq!(loaded.version, VERSION);
        assert_eq!(loaded.lima.sessions.len(), 1);
        assert_eq!(loaded.lima.total_count, 1);
        assert_eq!(loaded.lowlevel.sessions.len(), 1);
        assert_eq!(loaded.frontend.sessions.len(), 1);
        let _ = std::fs::remove_file(&path);
    }
}
