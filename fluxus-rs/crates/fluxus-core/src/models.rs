//! Data models (port of `core/models.py`, Lima part).
//!
//! The Python version uses plain dicts for events; we use a struct. Field
//! names match the Python keys so the serializer can stay symmetric.

use chrono::Local;
use chrono::{NaiveDateTime, TimeZone};
use serde::Serialize;

use crate::constants::{denom_lookup, denom_val};

/// One log event. Mirrors the Python dict built in `LogAnalyzer._process_line`
/// and `_process_bnv_event`.
#[derive(Debug, Clone, Default, Serialize)]
pub struct Event {
    pub timestamp: i64,
    pub date: Option<String>,
    pub time: Option<String>,
    #[serde(skip)]
    pub dt: Option<NaiveDateTime>,
    pub event_type: String,
    pub event_name: Option<String>,
    pub validator_type: Option<String>,
    pub banknote_uuid: Option<String>,
    pub banknote_id: String,
    pub banknote_raw: Option<i64>,
    pub currency: Option<String>,
    pub description: Option<String>,
    pub device_model: Option<String>,
    pub device_serial: Option<String>,
    pub device_vendor: Option<String>,
    pub raw: String,
    pub is_bnv: bool,
    /// True for the synthetic event that `build_sessions` injects when a BNV
    /// error closes a session. Python builds that one with a reduced key set,
    /// and the serializer reproduces it exactly.
    #[serde(skip)]
    pub from_bnv_error: bool,
}

impl Event {
    pub fn is_final(&self) -> bool {
        let et = &self.event_type;
        et.ends_with("_STACKED") || et.ends_with("_REJECTED") || et.ends_with("_CREDIT")
    }

    /// Python: `int(dt_obj.timestamp())` (local time).
    pub fn dt_timestamp(dt: &NaiveDateTime) -> i64 {
        match Local.from_local_datetime(dt).single() {
            Some(local) => local.timestamp(),
            None => dt.and_utc().timestamp(),
        }
    }
}

/// Lifecycle of a single banknote on a Lima validator.
/// Port of `BanknoteSession`.
#[derive(Debug, Clone, Serialize)]
pub struct BanknoteSession {
    pub session_id: usize,
    pub events: Vec<Event>,
    pub start_ts: i64,
    pub first_date: String,
    pub first_time: String,
    pub uuid: String,
    pub status: String,
    pub denomination: Option<i64>,
    pub currency: Option<String>,
    pub device_model: String,
    pub device_serial: String,
    pub closed: bool,
    pub error_reason: Option<String>,
}

impl BanknoteSession {
    pub fn new(session_id: usize, first_event: &Event) -> Self {
        let mut s = BanknoteSession {
            session_id,
            events: vec![first_event.clone()],
            start_ts: first_event.timestamp,
            first_date: first_event.date.clone().unwrap_or_else(|| "?".into()),
            first_time: first_event.time.clone().unwrap_or_else(|| "?".into()),
            uuid: first_event
                .banknote_uuid
                .clone()
                .or_else(|| {
                    if first_event.banknote_id.is_empty() {
                        None
                    } else {
                        Some(first_event.banknote_id.clone())
                    }
                })
                .unwrap_or_else(|| "N/A".into()),
            status: "INCOMPLETE".into(),
            denomination: first_event.banknote_raw,
            currency: first_event.currency.clone(),
            device_model: first_event
                .device_model
                .clone()
                .unwrap_or_else(|| "Unknown".into()),
            device_serial: first_event
                .device_serial
                .clone()
                .unwrap_or_else(|| "N/A".into()),
            closed: false,
            error_reason: None,
        };
        s.update_status(first_event);
        s
    }

    pub fn add_event(&mut self, event: Event) {
        if event.banknote_raw.is_some() && self.denomination.is_none() {
            self.denomination = event.banknote_raw;
        }
        if event.currency.is_some() && self.currency.is_none() {
            self.currency = event.currency.clone();
        }
        if event.device_model.is_some() && self.device_model == "Unknown" {
            self.device_model = event.device_model.clone().unwrap();
        }
        if event.device_serial.is_some() && self.device_serial == "N/A" {
            self.device_serial = event.device_serial.clone().unwrap();
        }
        self.update_status(&event);
        self.events.push(event);
    }

    fn update_status(&mut self, event: &Event) {
        if event.is_bnv {
            return;
        }
        let et = &event.event_type;
        if et.contains("STACKED") {
            self.status = "STACKED".into();
            self.closed = true;
        } else if et.contains("CREDIT") {
            self.status = "STACKED".into();
            self.closed = true;
        } else if et.contains("REJECTED") {
            self.status = "REJECTED".into();
            self.closed = true;
        } else if self.status != "ERROR" {
            if et.contains("STACKING") {
                self.status = "STACKING".into();
            } else if et.contains("INSERTED") && self.status == "INCOMPLETE" {
                self.status = "INSERTED".into();
            }
        }
    }

    pub fn mark_error(&mut self, bnv_event_name: &str) {
        if !self.closed {
            self.status = "ERROR".into();
            self.error_reason = Some(bnv_event_name.to_string());
        }
    }

    /// Python: `DENOM_MAP.get(denomination, denomination / 100)`.
    pub fn display_denom(&self) -> f64 {
        match self.denomination {
            Some(raw) if raw != 0 => denom_val(raw),
            _ => 0.0,
        }
    }

    pub fn display_denom_is_mapped(&self) -> bool {
        self.denomination
            .map(|raw| denom_lookup(raw).is_some())
            .unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(ts: i64, et: &str) -> Event {
        Event {
            timestamp: ts,
            event_type: et.to_string(),
            banknote_id: "N/A".into(),
            ..Default::default()
        }
    }

    #[test]
    fn stack_closes_session() {
        let first = event(1, "DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_INSERTED");
        let mut s = BanknoteSession::new(0, &first);
        assert_eq!(s.status, "INSERTED");
        s.add_event(event(2, "DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_STACKED"));
        assert_eq!(s.status, "STACKED");
        assert!(s.closed);
    }

    #[test]
    fn error_does_not_reopen_closed_session() {
        let first = event(1, "DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_STACKED");
        let mut s = BanknoteSession::new(0, &first);
        s.mark_error("Fraud Attempt");
        assert_eq!(s.status, "STACKED");
        assert!(s.error_reason.is_none());
    }

    #[test]
    fn denom_key_matches_python_formatting() {
        assert_eq!(denom_val(2000), 20.0);
        assert_eq!(denom_val(300), 3.0);
        assert_eq!(crate::constants::denom_key(2000), "20");
        assert_eq!(crate::constants::denom_key(300), "3.0");
    }
}
