//! Constants and lookup tables (port of `core/constants.py`).

use std::collections::HashMap;
use std::sync::OnceLock;

/// Raw denomination found in the log -> denomination in dollars.
/// Port of `DENOM_MAP`.
pub const DENOM_MAP: [(i64, i64); 6] = [
    (100, 1),
    (500, 5),
    (1000, 10),
    (2000, 20),
    (5000, 50),
    (10000, 100),
];

/// Look up a raw denomination. Returns `None` when it is not in the map,
/// in which case Python falls back to `raw / 100`.
pub fn denom_lookup(raw: i64) -> Option<i64> {
    DENOM_MAP.iter().find(|(k, _)| *k == raw).map(|(_, v)| *v)
}

/// Python's `DENOM_MAP.get(raw, raw / 100)` returns an `int` for mapped
/// values and a `float` for the fallback (true division). JSON object keys
/// inherit that distinction (`"1"` vs `"3.0"`), so we reproduce it exactly.
pub fn denom_val(raw: i64) -> f64 {
    match denom_lookup(raw) {
        Some(v) => v as f64,
        None => raw as f64 / 100.0,
    }
}

/// The same value as a JSON-key string, formatted the way Python's
/// `json.dumps` renders the key (`1` -> "1", `3.0` -> "3.0").
pub fn denom_key(raw: i64) -> String {
    match denom_lookup(raw) {
        Some(v) => v.to_string(),
        None => format_py_float(raw as f64 / 100.0),
    }
}

/// Render an f64 the way Python's `repr(float)` does for the values we
/// care about: shortest round-trip form, always with a `.0` for integers.
pub fn format_py_float(v: f64) -> String {
    if v.fract() == 0.0 && v.abs() < 1e16 {
        format!("{:.1}", v)
    } else {
        let s = format!("{}", v);
        s
    }
}

/// Thresholds for frontend <-> low-level correlation (seconds).
pub const SUSPICION_START_GAP_SEC: i64 = 30;
pub const SUSPICION_RETRY_GAP_SEC: i64 = 60;

/// BNV events that are treated as errors and mark a session as ERROR.
pub const BNV_ERROR_EVENTS: [&str; 4] = [
    "Safe Note Jam",
    "Unsafe Note Jam",
    "Fraud Attempt",
    "Stacker Full",
];

/// Known BNV event names (the display names live in the Python GUI layer).
pub const BNV_EVENT_NAMES: [&str; 16] = [
    "Slave Reset",
    "Note Rejecting",
    "Note Stacked",
    "Fraud Attempt",
    "Stacker Full",
    "Note Cleared To Cash Box",
    "Note Stacking",
    "Safe Note Jam",
    "Unsafe Note Jam",
    "Disabled",
    "Note Cleared From Front",
    "Channel Disable",
    "Credit Note",
    "Note Rejected",
    "Initialising",
    "Read Note",
];

pub fn is_bnv_event(name: &str) -> bool {
    BNV_EVENT_NAMES.contains(&name)
}

pub fn is_bnv_error(name: &str) -> bool {
    BNV_ERROR_EVENTS.contains(&name)
}

/// LCDM2000 status codes -> (description, category).
pub fn lcdm_status_codes() -> &'static HashMap<u8, (&'static str, &'static str)> {
    static TABLE: OnceLock<HashMap<u8, (&'static str, &'static str)>> = OnceLock::new();
    TABLE.get_or_init(|| {
        HashMap::from([
            (0x30, ("Good", "OK")),
            (0x31, ("Normal stop", "OK")),
            (0x32, ("Pickup error", "FAILED_HARDWARE")),
            (0x33, ("JAM at CHK1,2 Sensor", "FAILED_JAM")),
            (0x34, ("Overflow bill", "FAILED_HARDWARE")),
            (0x35, ("JAM at EXIT or EJT Sensor", "FAILED_JAM")),
            (0x36, ("JAM at DIV Sensor", "FAILED_JAM")),
            (0x37, ("Undefined command", "FAILED_HARDWARE")),
            (0x38, ("Upper Bill-End", "FAILED_EMPTY")),
            (0x3A, ("Counting Error (CHK3,4 - DIV)", "FAILED_HARDWARE")),
            (0x3B, ("Note request error", "FAILED_HARDWARE")),
            (0x3C, ("Counting Error (DIV - EJT)", "FAILED_HARDWARE")),
            (0x3D, ("Counting Error (EJT - EXIT)", "FAILED_HARDWARE")),
            (0x3F, ("Reject Tray not recognized", "FAILED_HARDWARE")),
            (0x40, ("Lower Bill-End", "FAILED_EMPTY")),
            (0x41, ("Motor Stop", "FAILED_HARDWARE")),
            (0x42, ("JAM at DIV Sensor", "FAILED_JAM")),
            (0x43, ("Timeout (DIV -> EJT)", "FAILED_TIMEOUT")),
            (0x44, ("Over Reject", "FAILED_HARDWARE")),
            (0x45, ("Upper Cassette not recognized", "FAILED_HARDWARE")),
            (0x46, ("Lower Cassette not recognized", "FAILED_HARDWARE")),
            (0x47, ("Dispensing timeout", "FAILED_TIMEOUT")),
            (0x48, ("JAM at EJT Sensor", "FAILED_JAM")),
            (0x49, ("Diverter solenoid error", "FAILED_HARDWARE")),
            (0x4A, ("SOL Sensor error", "FAILED_HARDWARE")),
            (0x4C, ("JAM at CHK3,4 Sensor", "FAILED_JAM")),
            (0x4E, ("Purge error (Jam at Div)", "FAILED_JAM")),
        ])
    })
}
