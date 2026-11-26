//! Lima / devices_server log analyzer.
//!
//! Faithful port of `parsers/lima_parser.py`. The structure mirrors the Python
//! class one-to-one so the two can be compared line by line during review.
//! Deliberate deviations:
//!   * regexes are compiled once (`Regexes::get`) instead of per line,
//!   * `strip_ansi` returns a `Cow` and is skipped when no ESC byte is present,
//!   * maps are `BTreeMap` for deterministic JSON output.

use std::collections::{BTreeMap, HashSet};
use std::path::Path;
use std::sync::OnceLock;

use chrono::{Datelike, Local, NaiveDate, NaiveDateTime};
use regex::Regex;
use serde::Serialize;

use crate::constants::{denom_key, denom_val, is_bnv_error, is_bnv_event};
use crate::models::{BanknoteSession, Event};
use crate::utils::{for_each_line_bytes, strip_ansi, truncate_chars};

/// All regexes, compiled once.
struct Regexes {
    datetime: Regex,
    banknote_struct: Regex,
    banknote: Regex,
    currency: [Regex; 3],
    uuid: [Regex; 2],
    banknote_id: Regex,
    model: [Regex; 2],
    serial: [Regex; 2],
    device_id: Regex,
    vendor: [Regex; 2],
    notes_quoted: Regex,
    notes_bare: Regex,
    notes_item: Regex,
    event_quoted: Regex,
    event_bare: Regex,
    bn_type: Regex,
    bn_event: Regex,
    description: [Regex; 2],
    timestamp_ms: Regex,
}

impl Regexes {
    fn get() -> &'static Regexes {
        static RE: OnceLock<Regexes> = OnceLock::new();
        RE.get_or_init(|| Regexes {
            datetime: Regex::new(r"\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}").unwrap(),
            banknote_struct: Regex::new(r"banknote:\s*\{\s*banknote:\s*(\d+)").unwrap(),
            banknote: Regex::new(r"banknote:\s*(\d+)").unwrap(),
            currency: [
                Regex::new(r#"currency:\s*\\?"(\w+)\\?""#).unwrap(),
                Regex::new(r#"currency:\s*"(\w+)""#).unwrap(),
                Regex::new(r"currency:\s*(\w+)").unwrap(),
            ],
            uuid: [
                Regex::new(r#"banknote_uuid:\s*\\?"([^\\"]+)\\?""#).unwrap(),
                Regex::new(r#"banknote_uuid:\s*"([^"]+)""#).unwrap(),
            ],
            banknote_id: Regex::new(r"banknote_id:\s*(\d+)").unwrap(),
            model: [
                Regex::new(r#"model:\s*\\?"([^\\"]+)\\?""#).unwrap(),
                Regex::new(r#"model:\s*"([^"]+)""#).unwrap(),
            ],
            serial: [
                Regex::new(r#"serial_number:\s*\\?"([^\\"]+)\\?""#).unwrap(),
                Regex::new(r#"serial_number:\s*"([^"]+)""#).unwrap(),
            ],
            device_id: Regex::new(r#"device:\s*\{\s*id:\s*\\?"([^\\"]+)\\?""#).unwrap(),
            vendor: [
                Regex::new(r#"vendor:\s*\\?"([^\\"]+)\\?""#).unwrap(),
                Regex::new(r#"vendor:\s*"([^"]+)""#).unwrap(),
            ],
            notes_quoted: Regex::new(r#"notes\s*=\s*"([^"]*)""#).unwrap(),
            notes_bare: Regex::new(r"notes\s*=\s*([^\s,;]+)").unwrap(),
            notes_item: Regex::new(r"\$(\d+)\s*x\s*(\d+)").unwrap(),
            event_quoted: Regex::new(r#"event=\s*"([^"]+)""#).unwrap(),
            event_bare: Regex::new(r"event=\s*([^\s]+)").unwrap(),
            bn_type: Regex::new(r"type:\s*(DEVICE_EVENT_TYPE_BILL_ACCEPTOR_\w+)").unwrap(),
            bn_event: Regex::new(r"event=\s*(DEVICE_EVENT_TYPE_BILL_ACCEPTOR_\w+)").unwrap(),
            description: [
                Regex::new(r#"description:\s*\\?"([^\\"]+)\\?""#).unwrap(),
                Regex::new(r#"description:\s*"([^"]+)""#).unwrap(),
            ],
            timestamp_ms: Regex::new(r"timestamp_ms:\s*(\d+)").unwrap(),
        })
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Collection {
    pub dt: Option<String>,
    #[serde(skip)]
    pub dt_val: Option<NaiveDateTime>,
    pub date_str: String,
    pub time_str: String,
    pub notes: BTreeMap<String, i64>,
    pub raw: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct DebugCollection {
    pub line: String,
    pub notes_text: String,
    pub has_collection: bool,
    pub has_print: bool,
    pub has_notes: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct DebugStacked {
    pub line: String,
    pub banknote_raw: Option<i64>,
    pub currency: Option<String>,
    pub event_type: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct DeviceModelInfo {
    pub model: String,
    pub vendor: String,
}

/// JSON shape consumed by the differential test and (later) by PyO3.
#[derive(Debug, Serialize)]
pub struct LimaResult {
    pub stats: BTreeMap<String, BTreeMap<String, i64>>,
    pub stats_by_device:
        BTreeMap<String, BTreeMap<String, BTreeMap<String, BTreeMap<String, i64>>>>,
    pub device_models: BTreeMap<String, DeviceModelInfo>,
    pub total_count: i64,
    pub total_sum: f64,
    pub lines_found: i64,
    pub lines_parsed: i64,
    pub sessions: Vec<BanknoteSession>,
    /// BNV events are kept separately from sessions, exactly like Python;
    /// the GUI reads this list directly.
    pub bnv_events: Vec<Event>,
    pub collections: Vec<Collection>,
    pub debug_collections: Vec<DebugCollection>,
    pub debug_info: Vec<String>,
    pub debug_stacked: Vec<DebugStacked>,
    pub bnv_events_count: BTreeMap<String, i64>,
    pub bnv_events_by_type: BTreeMap<String, BTreeMap<String, i64>>,
    pub between_stats: Option<BTreeMap<String, BTreeMap<String, i64>>>,
    pub between_count: Option<i64>,
    pub between_sum: Option<f64>,
    pub first_collection: Option<Collection>,
    pub last_collection: Option<Collection>,
}

#[derive(Debug, Clone)]
struct DateTimeParts {
    date: String,
    time: String,
    dt: NaiveDateTime,
}

pub struct LogAnalyzer {
    pub(crate) stats: BTreeMap<String, BTreeMap<String, i64>>,
    pub(crate) stats_by_device:
        BTreeMap<String, BTreeMap<String, BTreeMap<String, BTreeMap<String, i64>>>>,
    pub(crate) device_models: BTreeMap<String, DeviceModelInfo>,
    pub(crate) total_count: i64,
    pub(crate) total_sum: f64,
    pub(crate) lines_found: i64,
    pub(crate) lines_parsed: i64,
    debug_info: Vec<String>,
    debug_stacked: Vec<DebugStacked>,
    all_events: Vec<Event>,
    pub sessions: Vec<BanknoteSession>,
    pub(crate) collections: Vec<Collection>,
    /// (dt, notes) pairs already seen, for O(1) de-duplication.
    /// Python does a linear scan here, which is quadratic on large logs.
    collection_keys: HashSet<(String, String)>,
    /// (dt, raw denomination, currency) of every STACKED event. The Python
    /// code re-scans `all_events`; we keep a compact copy because
    /// `build_sessions` consumes the full event list.
    stacked_for_between: Vec<(Option<NaiveDateTime>, i64, String)>,
    debug_collections: Vec<DebugCollection>,
    pub(crate) bnv_events: Vec<Event>,
    pub(crate) bnv_events_count: BTreeMap<String, i64>,
    pub(crate) bnv_events_by_type: BTreeMap<String, BTreeMap<String, i64>>,
}

impl Default for LogAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl LogAnalyzer {
    pub fn new() -> Self {
        LogAnalyzer {
            stats: BTreeMap::new(),
            stats_by_device: BTreeMap::new(),
            device_models: BTreeMap::new(),
            total_count: 0,
            total_sum: 0.0,
            lines_found: 0,
            lines_parsed: 0,
            debug_info: Vec::new(),
            debug_stacked: Vec::new(),
            all_events: Vec::new(),
            sessions: Vec::new(),
            collections: Vec::new(),
            collection_keys: HashSet::new(),
            stacked_for_between: Vec::new(),
            debug_collections: Vec::new(),
            bnv_events: Vec::new(),
            bnv_events_count: BTreeMap::new(),
            bnv_events_by_type: BTreeMap::new(),
        }
    }

    /// Parse a log file (plain or `.gz`).
    pub fn parse_file(&mut self, path: &Path) -> std::io::Result<()> {
        // Байтовый префильтр: в реальных логах нужные строки — доли процента,
        // остальные не нужно ни в UTF-8 проверять, ни в текст превращать.
        for_each_line_bytes(path, |bytes| {
            let interesting = memchr::memmem::find(bytes, b"DEVICE_EVENT_TYPE_BILL_ACCEPTOR").is_some()
                || memchr::memmem::find(bytes, b"COLLECTION REPORT").is_some()
                || memchr::memmem::find(bytes, b"BanknoteValidator").is_some()
                || memchr::memmem::find(bytes, b"notes=").is_some();
            if !interesting {
                return;
            }
            let text = String::from_utf8_lossy(bytes);
            self.process_line(&text);
        })
    }

    /// Parse an in-memory log (used by tests and by PyO3 streaming callers).
    pub fn parse_str(&mut self, content: &str) {
        for line in content.split('\n') {
            let line = line.strip_suffix('\r').unwrap_or(line);
            self.process_line(line);
        }
    }

    // ---------------------------------------------------------------- dates

    fn extract_datetime(&self, line: &str) -> Option<DateTimeParts> {
        // The Python original calls datetime.now() for every matching line,
        // which is a surprisingly hot path (timezone lookup each time). The
        // year only matters for grouping, so cache it at process start.
        static YEAR: OnceLock<i32> = OnceLock::new();
        let year = *YEAR.get_or_init(|| Local::now().year());

        let m = Regexes::get().datetime.find(line)?;
        let s = m.as_str().as_bytes();
        // dd/mm HH:MM:SS
        if s.len() != 14 || s[2] != b'/' || s[5] != b' ' || s[8] != b':' || s[11] != b':' {
            return None;
        }
        let num = |i: usize| -> Option<u32> {
            let a = *s.get(i)? as char;
            let b = *s.get(i + 1)? as char;
            if !a.is_ascii_digit() || !b.is_ascii_digit() {
                return None;
            }
            Some((a as u32 - '0' as u32) * 10 + (b as u32 - '0' as u32))
        };
        let (d1, d2) = (num(0)?, num(3)?);
        let (hh, mm, ss) = (num(6)?, num(9)?, num(12)?);

        // Python tries "%Y %d/%m" first, then "%Y %m/%d".
        let dt = NaiveDate::from_ymd_opt(year, d2, d1)
            .and_then(|d| d.and_hms_opt(hh, mm, ss))
            .or_else(|| {
                NaiveDate::from_ymd_opt(year, d1, d2).and_then(|d| d.and_hms_opt(hh, mm, ss))
            })?;

        Some(DateTimeParts {
            date: dt.format("%d/%m/%Y").to_string(),
            time: dt.format("%H:%M:%S").to_string(),
            dt,
        })
    }

    // --------------------------------------------------------- field parsers

    fn parse_banknote_raw(&self, line: &str) -> Option<i64> {
        let re = Regexes::get();
        if let Some(c) = re.banknote_struct.captures(line) {
            return c.get(1)?.as_str().parse().ok();
        }
        if let Some(c) = re.banknote.captures(line) {
            let val: i64 = c.get(1)?.as_str().parse().ok()?;
            if crate::constants::denom_lookup(val).is_some() || val >= 100 {
                return Some(val);
            }
        }
        None
    }

    fn parse_currency(&self, line: &str) -> Option<String> {
        for re in &Regexes::get().currency {
            if let Some(c) = re.captures(line) {
                return Some(c.get(1)?.as_str().to_string());
            }
        }
        None
    }

    fn parse_banknote_uuid(&self, line: &str) -> Option<String> {
        for re in &Regexes::get().uuid {
            if let Some(c) = re.captures(line) {
                return Some(c.get(1)?.as_str().to_string());
            }
        }
        None
    }

    fn parse_banknote_id(&self, line: &str) -> Option<String> {
        let c = Regexes::get().banknote_id.captures(line)?;
        Some(c.get(1)?.as_str().to_string())
    }

    fn parse_device_model(&self, line: &str) -> Option<String> {
        for re in &Regexes::get().model {
            if let Some(c) = re.captures(line) {
                return Some(c.get(1)?.as_str().trim().to_string());
            }
        }
        None
    }

    fn parse_device_serial(&self, line: &str) -> Option<String> {
        for re in &Regexes::get().serial {
            if let Some(c) = re.captures(line) {
                return Some(c.get(1)?.as_str().trim().to_string());
            }
        }
        if let Some(c) = Regexes::get().device_id.captures(line) {
            return Some(c.get(1)?.as_str().trim().to_string());
        }
        None
    }

    fn parse_device_vendor(&self, line: &str) -> Option<String> {
        for re in &Regexes::get().vendor {
            if let Some(c) = re.captures(line) {
                return Some(c.get(1)?.as_str().trim().to_string());
            }
        }
        None
    }

    fn parse_description(&self, line: &str) -> String {
        for re in &Regexes::get().description {
            if let Some(c) = re.captures(line) {
                return c.get(1).map(|m| m.as_str().to_string()).unwrap_or_default();
            }
        }
        String::new()
    }

    // ----------------------------------------------------------- collections

    fn process_collection(&mut self, clean: &str, dt: Option<&NaiveDateTime>) {
        let Some(dt) = dt else { return };
        let re = Regexes::get();
        let notes_text = if let Some(c) = re.notes_quoted.captures(clean) {
            c.get(1).map(|m| m.as_str().to_string()).unwrap_or_default()
        } else if let Some(c) = re.notes_bare.captures(clean) {
            c.get(1).map(|m| m.as_str().to_string()).unwrap_or_default()
        } else {
            String::new()
        };

        let mut notes: BTreeMap<String, i64> = BTreeMap::new();
        for c in re.notes_item.captures_iter(&notes_text) {
            let nominal: i64 = c.get(1).unwrap().as_str().parse().unwrap_or(0);
            let count: i64 = c.get(2).unwrap().as_str().parse().unwrap_or(0);
            notes.insert(nominal.to_string(), count);
        }

        if !notes.is_empty() {
            let coll = Collection {
                dt: Some(dt.format("%d/%m/%Y %H:%M:%S").to_string()),
                dt_val: Some(*dt),
                date_str: dt.format("%d/%m/%Y").to_string(),
                time_str: dt.format("%H:%M:%S").to_string(),
                notes,
                raw: truncate_chars(clean, 300),
            };
            let notes_key = coll
                .notes
                .iter()
                .map(|(k, v)| format!("{}={}", k, v))
                .collect::<Vec<_>>()
                .join(",");
            let key = (coll.dt.clone().unwrap_or_default(), notes_key);
            if self.collection_keys.insert(key) {
                self.collections.push(coll);
            }
        } else if self.debug_collections.len() < 5 {
            self.debug_collections.push(DebugCollection {
                line: truncate_chars(clean, 300),
                notes_text: truncate_chars(&notes_text, 150),
                has_collection: clean.contains("COLLECTION REPORT"),
                has_print: clean.contains("Print"),
                has_notes: clean.contains("notes="),
            });
        }
    }

    // ------------------------------------------------------------ BNV events

    fn process_bnv_event(&mut self, line: &str, clean: &str) -> Option<Event> {
        let validator_type = if clean.contains("BanknoteValidatorBV") {
            "BV20/30/50"
        } else if clean.contains("BanknoteValidator") {
            "NV9/other"
        } else {
            return None;
        };

        let re = Regexes::get();
        let event_name = if let Some(c) = re.event_quoted.captures(clean) {
            c.get(1)?.as_str().to_string()
        } else if let Some(c) = re.event_bare.captures(clean) {
            c.get(1)?.as_str().to_string()
        } else {
            return None;
        };
        if !is_bnv_event(&event_name) {
            return None;
        }

        let parts = self.extract_datetime(line);
        let ts_match = re.timestamp_ms.captures(line);
        let timestamp = match ts_match {
            Some(c) => c.get(1)?.as_str().parse().unwrap_or(0),
            None => parts
                .as_ref()
                .map(|p| Event::dt_timestamp(&p.dt))
                .unwrap_or(0),
        };

        *self.bnv_events_count.entry(event_name.clone()).or_insert(0) += 1;
        *self
            .bnv_events_by_type
            .entry(validator_type.to_string())
            .or_default()
            .entry(event_name.clone())
            .or_insert(0) += 1;

        Some(Event {
            timestamp,
            date: parts.as_ref().map(|p| p.date.clone()),
            time: parts.as_ref().map(|p| p.time.clone()),
            dt: parts.as_ref().map(|p| p.dt),
            event_type: format!("BNV_{}", event_name),
            event_name: Some(event_name.clone()),
            validator_type: Some(validator_type.to_string()),
            banknote_uuid: None,
            banknote_id: "N/A".into(),
            banknote_raw: None,
            currency: None,
            description: Some(event_name),
            device_model: None,
            device_serial: None,
            device_vendor: None,
            raw: truncate_chars(clean, 200),
            is_bnv: true,
            from_bnv_error: false,
        })
    }

    // ------------------------------------------------------------ line parse

    pub fn process_line(&mut self, line: &str) {
        let clean = strip_ansi(line);
        let re = Regexes::get();

        let is_collection = clean.contains("COLLECTION REPORT")
            || (clean.contains("Print")
                && clean.contains("notes=")
                && clean.contains('$')
                && re.notes_item.is_match(&clean));
        if is_collection {
            let parts = self.extract_datetime(line);
            let dt = parts.as_ref().map(|p| p.dt);
            self.process_collection(&clean, dt.as_ref());
        }

        if line.contains("DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_") {
            self.lines_found += 1;
            if self.debug_info.len() < 3 {
                self.debug_info.push(line.to_string());
            }

            let event_type = re
                .bn_type
                .captures(line)
                .or_else(|| re.bn_event.captures(line))
                .and_then(|c| c.get(1).map(|m| m.as_str().to_string()));
            let Some(event_type) = event_type else { return };

            let banknote_raw = self.parse_banknote_raw(line);
            let currency = self.parse_currency(line);
            let banknote_uuid = self.parse_banknote_uuid(line);
            let banknote_id = self.parse_banknote_id(line);
            let device_model = self.parse_device_model(line);
            let device_serial = self.parse_device_serial(line);
            let device_vendor = self.parse_device_vendor(line);

            if let (Some(serial), Some(model)) = (&device_serial, &device_model) {
                self.device_models.insert(
                    serial.clone(),
                    DeviceModelInfo {
                        model: model.clone(),
                        vendor: device_vendor.clone().unwrap_or_else(|| "?".into()),
                    },
                );
            }

            let description = self.parse_description(line);
            let parts = self.extract_datetime(line);
            let timestamp = re
                .timestamp_ms
                .captures(line)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse::<i64>().ok())
                .unwrap_or(0);

            let event = Event {
                timestamp,
                date: parts.as_ref().map(|p| p.date.clone()),
                time: parts.as_ref().map(|p| p.time.clone()),
                dt: parts.as_ref().map(|p| p.dt),
                event_type: event_type.clone(),
                banknote_uuid,
                banknote_id: banknote_id.unwrap_or_else(|| "N/A".into()),
                banknote_raw,
                currency: currency.clone(),
                description: Some(description),
                device_model: device_model.clone(),
                device_serial: device_serial.clone(),
                device_vendor,
                raw: truncate_chars(line, 250),
                is_bnv: false,
                from_bnv_error: false,
                event_name: None,
                validator_type: None,
            };

            if event_type.contains("STACKED") {
                if let (Some(raw), Some(curr)) = (banknote_raw, currency.clone()) {
                    if let Some(p) = parts.as_ref() {
                        self.stacked_for_between
                            .push((Some(p.dt), raw, curr.clone()));
                    } else {
                        self.stacked_for_between.push((None, raw, curr.clone()));
                    }
                    let key = denom_key(raw);
                    let val = denom_val(raw);
                    *self
                        .stats
                        .entry(curr.clone())
                        .or_default()
                        .entry(key.clone())
                        .or_insert(0) += 1;

                    let model_key = device_model.clone().unwrap_or_else(|| "Unknown".into());
                    let serial_key = device_serial.clone().unwrap_or_else(|| "N/A".into());
                    *self
                        .stats_by_device
                        .entry(model_key)
                        .or_default()
                        .entry(serial_key)
                        .or_default()
                        .entry(curr.clone())
                        .or_default()
                        .entry(key)
                        .or_insert(0) += 1;

                    self.total_count += 1;
                    self.total_sum += val;
                    self.lines_parsed += 1;
                } else if self.debug_stacked.len() < 5 {
                    self.debug_stacked.push(DebugStacked {
                        line: truncate_chars(line, 200),
                        banknote_raw,
                        currency: currency.clone(),
                        event_type: event_type.clone(),
                    });
                }
            }
            self.all_events.push(event);
            return;
        }

        if let Some(bnv_event) = self.process_bnv_event(line, &clean) {
            self.bnv_events.push(bnv_event);
        }
    }

    // -------------------------------------------------------------- sessions

    pub fn build_sessions(&mut self) {
        let mut events = std::mem::take(&mut self.all_events);
        // `bnv_events` must survive: the serializer and the GUI read it after
        // session building, exactly like Python.
        let mut bnv = std::mem::take(&mut self.bnv_events);
        events.sort_by_key(|e| e.timestamp);
        bnv.sort_by_key(|e| e.timestamp);

        // Python builds [('EVENT', e) ...] + [('BNV', b) ...] and then does a
        // *stable* sort by timestamp, so events win ties.
        let mut merged: Vec<(bool, Event)> = Vec::with_capacity(events.len() + bnv.len());
        merged.extend(events.into_iter().map(|e| (false, e)));
        merged.extend(bnv.iter().cloned().map(|e| (true, e)));
        merged.sort_by_key(|(_, e)| e.timestamp);

        self.sessions = Vec::new();
        // Insertion-ordered map, matching Python's dict ordering.
        let mut current: Vec<(String, BanknoteSession)> = Vec::new();

        for (is_bnv, ev) in merged {
            if is_bnv {
                let vt = ev.validator_type.clone();
                let name = ev.event_name.clone();
                for (_, sess) in current.iter_mut() {
                    if sess.closed {
                        continue;
                    }
                    let model_match = match vt.as_deref() {
                        Some("BV20/30/50") => sess.device_model.contains("BV"),
                        Some(_) => !sess.device_model.contains("BV"),
                        None => true,
                    };
                    if !model_match {
                        continue;
                    }
                    if let Some(name) = name.as_deref() {
                        if is_bnv_error(name) {
                            sess.mark_error(name);
                            sess.add_event(Event {
                                timestamp: ev.timestamp,
                                date: ev.date.clone(),
                                time: ev.time.clone(),
                                event_type: ev.event_type.clone(),
                                banknote_uuid: None,
                                banknote_id: "N/A".into(),
                                banknote_raw: None,
                                currency: None,
                                description: Some(format!("[BNV ERROR] {}", name)),
                                is_bnv: true,
                                from_bnv_error: true,
                                ..Default::default()
                            });
                        }
                    }
                }
                continue;
            }

            let serial = ev
                .device_serial
                .clone()
                .unwrap_or_else(|| "N/A".to_string());
            let is_final = ev.is_final();

            if ev.event_type.contains("INSERTED") && ev.banknote_uuid.is_some() {
                let pos = current.iter().position(|(k, _)| *k == serial);
                if let Some(idx) = pos {
                    let existing = &mut current[idx].1;
                    if !existing.closed && Some(existing.uuid.clone()) == ev.banknote_uuid {
                        existing.add_event(ev);
                        continue;
                    }
                    let old = std::mem::replace(
                        &mut current[idx].1,
                        BanknoteSession::new(self.sessions.len(), &ev),
                    );
                    self.sessions.push(old);
                } else {
                    current.push((serial, BanknoteSession::new(self.sessions.len(), &ev)));
                }
                continue;
            }

            let pos = current.iter().position(|(k, _)| *k == serial);
            if let Some(idx) = pos {
                let sess = &mut current[idx].1;
                sess.add_event(ev);
                if is_final {
                    let (_, done) = current.remove(idx);
                    self.sessions.push(done);
                }
            } else {
                let new_session = BanknoteSession::new(self.sessions.len(), &ev);
                if is_final {
                    self.sessions.push(new_session);
                } else {
                    current.push((serial, new_session));
                }
            }
        }

        self.bnv_events = bnv;

        for (_, sess) in current {
            self.sessions.push(sess);
        }
        for (i, s) in self.sessions.iter_mut().enumerate() {
            s.session_id = i;
        }
    }

    // ---------------------------------------------------------------- stats

    fn between_collections_stats(
        &self,
    ) -> (
        Option<BTreeMap<String, BTreeMap<String, i64>>>,
        Option<i64>,
        Option<f64>,
        Option<Collection>,
        Option<Collection>,
    ) {
        // Python returns None for every field when there are no collections.
        if self.collections.is_empty() {
            return (None, None, None, None, None);
        }
        let mut sorted: Vec<&Collection> = self.collections.iter().collect();
        sorted.sort_by(|a, b| a.dt_val.cmp(&b.dt_val));
        let first = sorted.first().unwrap();
        let last = sorted.last().unwrap();
        let (Some(first_dt), Some(last_dt)) = (first.dt_val, last.dt_val) else {
            return (
                None,
                Some(0),
                Some(0.0),
                Some((*first).clone()),
                Some((*last).clone()),
            );
        };

        let mut between: BTreeMap<String, BTreeMap<String, i64>> = BTreeMap::new();
        let mut count = 0i64;
        let mut sum = 0.0f64;
        for (dt, raw, curr) in &self.stacked_for_between {
            let Some(dt) = dt else { continue };
            if *dt <= first_dt || *dt > last_dt {
                continue;
            }
            *between
                .entry(curr.clone())
                .or_default()
                .entry(denom_key(*raw))
                .or_insert(0) += 1;
            count += 1;
            sum += denom_val(*raw);
        }
        (
            Some(between),
            Some(count),
            Some(sum),
            Some((*first).clone()),
            Some((*last).clone()),
        )
    }

    /// Слияние независимого разбора (параллельный режим). Сессии строятся
    /// позже, один раз, по объединённому списку событий.
    pub fn merge(&mut self, mut other: LogAnalyzer) {
        self.all_events.append(&mut other.all_events);
        self.bnv_events.append(&mut other.bnv_events);
        self.stacked_for_between.append(&mut other.stacked_for_between);

        for coll in other.collections.drain(..) {
            if let Some(key) = self.collection_key(&coll) {
                if self.collection_keys.insert(key) {
                    self.collections.push(coll);
                }
            }
        }

        for (curr, denoms) in other.stats {
            let entry = self.stats.entry(curr).or_default();
            for (k, v) in denoms {
                *entry.entry(k).or_insert(0) += v;
            }
        }
        for (model, serials) in other.stats_by_device {
            let m = self.stats_by_device.entry(model).or_default();
            for (serial, currs) in serials {
                let c = m.entry(serial).or_default();
                for (curr, denoms) in currs {
                    let cd = c.entry(curr).or_default();
                    for (k, v) in denoms {
                        *cd.entry(k).or_insert(0) += v;
                    }
                }
            }
        }
        for (k, v) in other.device_models {
            self.device_models.entry(k).or_insert(v);
        }
        for (k, v) in other.bnv_events_count {
            *self.bnv_events_count.entry(k).or_insert(0) += v;
        }
        for (k, inner) in other.bnv_events_by_type {
            let e = self.bnv_events_by_type.entry(k).or_default();
            for (name, cnt) in inner {
                *e.entry(name).or_insert(0) += cnt;
            }
        }
        self.total_count += other.total_count;
        self.total_sum += other.total_sum;
        self.lines_found += other.lines_found;
        self.lines_parsed += other.lines_parsed;

        if self.debug_info.len() < 3 {
            self.debug_info.append(&mut other.debug_info);
            self.debug_info.truncate(3);
        }
        if self.debug_stacked.len() < 5 {
            self.debug_stacked.append(&mut other.debug_stacked);
            self.debug_stacked.truncate(5);
        }
        if self.debug_collections.len() < 5 {
            self.debug_collections.append(&mut other.debug_collections);
            self.debug_collections.truncate(5);
        }
    }

    fn collection_key(&self, coll: &Collection) -> Option<(String, String)> {
        let notes_key = coll
            .notes
            .iter()
            .map(|(k, v)| format!("{}={}", k, v))
            .collect::<Vec<_>>()
            .join(",");
        Some((coll.dt.clone().unwrap_or_default(), notes_key))
    }

    /// Параллельный разбор набора файлов.
    pub fn parse_many(paths: &[std::path::PathBuf]) -> std::io::Result<Self> {
        use rayon::prelude::*;
        let parts: Vec<Self> = paths
            .par_iter()
            .map(|path| {
                let mut a = LogAnalyzer::new();
                a.parse_file(path)?;
                Ok(a)
            })
            .collect::<std::io::Result<Vec<_>>>()?;

        let mut merged = LogAnalyzer::new();
        for part in parts {
            merged.merge(part);
        }
        Ok(merged)
    }

    pub fn lines_found(&self) -> i64 {
        self.lines_found
    }

    pub fn result(&self) -> LimaResult {
        let (between, between_count, between_sum, first, last) = self.between_collections_stats();
        LimaResult {
            stats: self.stats.clone(),
            stats_by_device: self.stats_by_device.clone(),
            device_models: self.device_models.clone(),
            total_count: self.total_count,
            total_sum: self.total_sum,
            lines_found: self.lines_found,
            lines_parsed: self.lines_parsed,
            sessions: self.sessions.clone(),
            bnv_events: self.bnv_events.clone(),
            collections: self.collections.clone(),
            debug_collections: self.debug_collections.clone(),
            debug_info: self.debug_info.clone(),
            debug_stacked: self.debug_stacked.clone(),
            bnv_events_count: self.bnv_events_count.clone(),
            bnv_events_by_type: self.bnv_events_by_type.clone(),
            between_stats: between,
            between_count,
            between_sum,
            first_collection: first,
            last_collection: last,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_stacked_banknote() {
        let line = "15/10 14:11:07 DBG type: DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_STACKED \
                    banknote: { banknote: 2000 } currency: \"USD\" \
                    banknote_uuid: \"uuid-1\" device: { id: \"SN123\", model: \"BV20\", \
                    vendor: \"CashCode\" } timestamp_ms: 1730000000000";
        let mut a = LogAnalyzer::new();
        a.process_line(line);
        a.build_sessions();
        let r = a.result();
        assert_eq!(r.total_count, 1);
        assert_eq!(r.total_sum, 20.0);
        assert_eq!(r.lines_found, 1);
        assert_eq!(r.stats["USD"]["20"], 1);
        assert_eq!(r.device_models["SN123"].model, "BV20");
        assert_eq!(r.sessions.len(), 1);
        assert_eq!(r.sessions[0].status, "STACKED");
    }

    #[test]
    fn collection_line_extracts_notes() {
        let line = "15/10 14:11:07 INF COLLECTION REPORT Print notes=\"$20 x 5 $10 x 2\"";
        let mut a = LogAnalyzer::new();
        a.process_line(line);
        let r = a.result();
        assert_eq!(r.collections.len(), 1);
        assert_eq!(r.collections[0].notes["20"], 5);
        assert_eq!(r.collections[0].notes["10"], 2);
    }

    #[test]
    fn bnv_error_marks_open_session() {
        let mut a = LogAnalyzer::new();
        a.process_line("15/10 14:11:07 DBG type: DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_INSERTED device: { id: \"SN1\", model: \"BV20\" } banknote_uuid: \"u1\"");
        a.process_line("15/10 14:11:08 DBG BanknoteValidatorBV event= \"Fraud Attempt\"");
        a.build_sessions();
        let r = a.result();
        assert_eq!(r.sessions.len(), 1);
        assert_eq!(r.sessions[0].status, "ERROR");
        assert_eq!(r.sessions[0].error_reason.as_deref(), Some("Fraud Attempt"));
        assert_eq!(r.bnv_events_count["Fraud Attempt"], 1);
    }
}
