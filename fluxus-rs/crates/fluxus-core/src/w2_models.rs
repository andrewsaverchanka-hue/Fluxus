//! W2 models (port of the W2 part of `core/models.py`).
//!
//! Python stores events/results as ad-hoc dicts; we keep the same shape with
//! `serde_json::Map` so the parsed output is structurally identical and the
//! differential test can compare it field by field.

use std::collections::BTreeMap;

use chrono::Local;
use chrono::{NaiveDateTime, TimeZone};
use serde::Serialize;
use serde_json::{Map, Value};

use crate::constants::{denom_lookup, denom_val};

pub type JsonMap = Map<String, Value>;

/// One frontend/low-level event: `kind`, timestamp plus arbitrary payload.
#[derive(Debug, Clone, Serialize)]
pub struct W2Event {
    pub kind: String,
    #[serde(rename = "dt")]
    pub dt_iso: String,
    #[serde(skip)]
    pub dt: NaiveDateTime,
    pub date: String,
    pub time: String,
    pub raw: String,
    #[serde(flatten)]
    pub data: JsonMap,
}

impl W2Event {
    pub fn new(kind: &str, dt: NaiveDateTime, raw: String, data: JsonMap) -> Self {
        W2Event {
            kind: kind.to_string(),
            dt_iso: dt.format("%Y-%m-%dT%H:%M:%S").to_string(),
            dt,
            date: dt.format("%d/%m/%Y").to_string(),
            time: dt.format("%H:%M:%S").to_string(),
            raw,
            data,
        }
    }
}

/// A box row inserted by the frontend (`terminal_transactions_box`).
#[derive(Debug, Clone, Serialize)]
pub struct BoxEntry {
    pub tt_id: i64,
    pub r#box: i64,
    pub nominal: i64,
    pub count: i64,
    pub is_rejected: i64,
}

/// Cash event (collection / withdraw_report / cash_in).
/// Port of `W2CashEvent`.
#[derive(Debug, Clone, Serialize)]
pub struct W2CashEvent {
    pub event_type: String,
    #[serde(skip)]
    pub dt: NaiveDateTime,
    pub dt_iso: String,
    pub timestamp: f64,
    pub boxes_delta: BTreeMap<i64, i64>,
    pub reject_delta: BTreeMap<i64, i64>,
    pub raw_by_nominal: BTreeMap<i64, i64>,
    /// Cash loaded into dispensers during the period (collection report's
    /// "TOTAL LOADED" / "inByDatesAndNotes"). This is the refill side.
    pub loaded_by_nominal: BTreeMap<i64, i64>,
    pub total_loaded_cents: i64,
    pub total_cents: i64,
    pub raw_details: String,
}

impl W2CashEvent {
    pub const TYPE_COLLECTION: &'static str = "collection";
    pub const TYPE_WITHDRAW: &'static str = "withdraw_report";
    pub const TYPE_CASH_IN: &'static str = "cash_in";

    pub fn new(event_type: &str, dt: NaiveDateTime) -> Self {
        W2CashEvent {
            event_type: event_type.to_string(),
            dt,
            dt_iso: dt.format("%Y-%m-%dT%H:%M:%S").to_string(),
            timestamp: local_timestamp(&dt) as f64,
            boxes_delta: BTreeMap::new(),
            reject_delta: BTreeMap::new(),
            raw_by_nominal: BTreeMap::new(),
            loaded_by_nominal: BTreeMap::new(),
            total_loaded_cents: 0,
            total_cents: 0,
            raw_details: String::new(),
        }
    }

    pub fn is_outflow(&self) -> bool {
        self.event_type == Self::TYPE_COLLECTION || self.event_type == Self::TYPE_WITHDRAW
    }

    pub fn is_inflow(&self) -> bool {
        self.event_type == Self::TYPE_CASH_IN
    }

    pub fn date_str(&self) -> String {
        self.dt.format("%d/%m/%Y").to_string()
    }

    pub fn time_str(&self) -> String {
        self.dt.format("%H:%M:%S").to_string()
    }

    pub fn type_display(&self) -> &'static str {
        match self.event_type.as_str() {
            Self::TYPE_COLLECTION => "Инкассация",
            Self::TYPE_WITHDRAW => "Изъятие (Withdraw)",
            Self::TYPE_CASH_IN => "Пополнение (Cash in)",
            _ => "",
        }
    }
}

fn local_timestamp(dt: &NaiveDateTime) -> i64 {
    match Local.from_local_datetime(dt).single() {
        Some(local) => local.timestamp(),
        None => dt.and_utc().timestamp(),
    }
}

/// Low-level dispenser session (LCDM2000 / LCDM4000).
/// Port of `W2LowLevelSession`.
#[derive(Debug, Clone, Serialize)]
pub struct W2LowLevelSession {
    pub model: Option<String>,
    pub req_id: Option<i64>,
    pub job_id: Option<i64>,
    pub idx: Option<String>,
    pub serial_path: Option<String>,
    #[serde(skip)]
    pub start_dt: Option<NaiveDateTime>,
    #[serde(skip)]
    pub end_dt: Option<NaiveDateTime>,
    pub start_dt_iso: Option<String>,
    pub end_dt_iso: Option<String>,
    pub start_date_str: String,
    pub start_time_str: String,
    pub commands: Vec<JsonMap>,
    pub results: Vec<JsonMap>,
    pub status: String,
    pub error_detail: Option<String>,
    pub lcdm_status_code: Option<u8>,
    pub lcdm_device_code: Option<i64>,
    pub events: Vec<W2Event>,
    pub closed: bool,
}

impl Default for W2LowLevelSession {
    fn default() -> Self {
        W2LowLevelSession {
            model: None,
            req_id: None,
            job_id: None,
            idx: None,
            serial_path: None,
            start_dt: None,
            end_dt: None,
            start_dt_iso: None,
            end_dt_iso: None,
            start_date_str: "?".into(),
            start_time_str: "?".into(),
            commands: Vec::new(),
            results: Vec::new(),
            status: "INCOMPLETE".into(),
            error_detail: None,
            lcdm_status_code: None,
            lcdm_device_code: None,
            events: Vec::new(),
            closed: false,
        }
    }
}

impl W2LowLevelSession {
    pub fn add_event(&mut self, kind: &str, dt: NaiveDateTime, raw: String, payload: JsonMap) {
        if self.start_dt.is_none() || dt < self.start_dt.unwrap() {
            self.start_dt = Some(dt);
            self.start_date_str = dt.format("%d/%m/%Y").to_string();
            self.start_time_str = dt.format("%H:%M:%S").to_string();
        }
        if self.end_dt.is_none() || dt > self.end_dt.unwrap() {
            self.end_dt = Some(dt);
        }
        self.start_dt_iso = self
            .start_dt
            .map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string());
        self.end_dt_iso = self
            .end_dt
            .map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string());
        self.events.push(W2Event::new(kind, dt, raw, payload));
    }

    pub fn total_requested(&self) -> i64 {
        self.commands
            .iter()
            .filter_map(|c| c.get("amount").and_then(Value::as_i64))
            .sum()
    }

    pub fn total_dispensed(&self) -> i64 {
        self.results
            .iter()
            .filter_map(|r| r.get("dispensed").and_then(Value::as_i64))
            .sum()
    }

    pub fn total_rejected(&self) -> i64 {
        self.results
            .iter()
            .filter_map(|r| r.get("rejected").and_then(Value::as_i64))
            .sum()
    }

    pub fn finalize_status(&mut self) {
        if self.results.is_empty() {
            if !self.commands.is_empty() {
                self.status = "INCOMPLETE".into();
            }
            return;
        }

        let error_pos = self.results.iter().position(|r| {
            let has_error = r.get("error").map(|v| !v.is_null()).unwrap_or(false);
            if has_error {
                return true;
            }
            match r.get("status_code") {
                Some(Value::Null) | None => false,
                Some(v) => {
                    let code = v.as_i64().unwrap_or(-1);
                    code != 0x30 && code != 0x31
                }
            }
        });

        if let Some(pos) = error_pos {
            let r = self.results[pos].clone();
            let status_code = r.get("status_code").and_then(Value::as_i64);
            let error = r.get("error").and_then(Value::as_str).map(str::to_string);
            if let Some(code) = status_code {
                let (desc, cat) = crate::utils::classify_lcdm_status(code as u8);
                self.status = cat.to_string();
                self.error_detail = Some(desc);
                self.lcdm_status_code = Some(code as u8);
            } else if let Some(err) = error {
                self.error_detail = Some(err.clone());
                self.status = crate::utils::classify_dispense_error(Some(&err)).to_string();
                if let Some(dc) = r.get("device_code").and_then(Value::as_i64) {
                    self.lcdm_device_code = Some(dc);
                }
            }
            return;
        }

        let req = self.total_requested();
        let disp = self.total_dispensed();
        if disp >= req && req > 0 {
            self.status = "OK".into();
        } else if disp > 0 {
            self.status = "PARTIAL".into();
        } else {
            self.status = "INCOMPLETE".into();
        }
    }

    pub fn duration_sec(&self) -> f64 {
        match (self.start_dt, self.end_dt) {
            (Some(a), Some(b)) => (b - a).num_milliseconds() as f64 / 1000.0,
            _ => 0.0,
        }
    }
}

/// Frontend withdraw session (skills-battle-frontend log).
/// Port of `W2WithdrawSession`.
#[derive(Debug, Clone, Serialize)]
pub struct W2WithdrawSession {
    pub trx: String,
    pub events: Vec<W2Event>,
    #[serde(skip)]
    pub start_dt: Option<NaiveDateTime>,
    #[serde(skip)]
    pub end_dt: Option<NaiveDateTime>,
    pub start_dt_iso: Option<String>,
    pub end_dt_iso: Option<String>,
    pub start_date_str: String,
    pub start_time_str: String,
    pub coupon: Option<String>,
    pub user_login: Option<String>,
    pub group_uuid: Option<String>,
    pub balance_id: Option<i64>,
    pub transaction_id: Option<i64>,
    pub requested_amount: i64,
    pub dispensed_amount: i64,
    pub commands: Vec<Value>,
    pub dispensed_events: Vec<Value>,
    pub boxes: Vec<BoxEntry>,
    pub cassette_snapshot: Vec<Value>,
    pub status: String,
    pub error_detail: Option<String>,
    pub retry_count: i64,
    pub payout_coupon_status: Option<String>,
    pub recovery_coupon: Option<String>,
    pub receipt_printed: bool,
    pub dispenser_device_id: Option<String>,
    pub dispenser_model: Option<String>,
    pub dispenser_vendor: Option<String>,
    /// Indices into the analyzer's low-level session list.
    pub lowlevel_sessions: Vec<usize>,
    pub suspicion_flags: Vec<Vec<String>>,
    pub cassette_mismatch: bool,
    pub cassette_mismatch_details: BTreeMap<String, Value>,
    pub cassette_mismatch_collective: bool,
    pub cassette_mismatch_group_size: i64,
}

impl W2WithdrawSession {
    pub fn new(trx: &str) -> Self {
        W2WithdrawSession {
            trx: trx.to_string(),
            events: Vec::new(),
            start_dt: None,
            end_dt: None,
            start_dt_iso: None,
            end_dt_iso: None,
            start_date_str: "?".into(),
            start_time_str: "?".into(),
            coupon: None,
            user_login: None,
            group_uuid: None,
            balance_id: None,
            transaction_id: None,
            requested_amount: 0,
            dispensed_amount: 0,
            commands: Vec::new(),
            dispensed_events: Vec::new(),
            boxes: Vec::new(),
            cassette_snapshot: Vec::new(),
            status: "INCOMPLETE".into(),
            error_detail: None,
            retry_count: 0,
            payout_coupon_status: None,
            recovery_coupon: None,
            receipt_printed: false,
            dispenser_device_id: None,
            dispenser_model: None,
            dispenser_vendor: None,
            lowlevel_sessions: Vec::new(),
            suspicion_flags: Vec::new(),
            cassette_mismatch: false,
            cassette_mismatch_details: BTreeMap::new(),
            cassette_mismatch_collective: false,
            cassette_mismatch_group_size: 1,
        }
    }

    pub fn add_event(&mut self, event: W2Event) {
        if self.start_dt.is_none() || event.dt < self.start_dt.unwrap() {
            self.start_dt = Some(event.dt);
            self.start_date_str = event.date.clone();
            self.start_time_str = event.time.clone();
        }
        if self.end_dt.is_none() || event.dt > self.end_dt.unwrap() {
            self.end_dt = Some(event.dt);
        }
        self.start_dt_iso = self
            .start_dt
            .map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string());
        self.end_dt_iso = self
            .end_dt
            .map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string());
        self.events.push(event);
    }

    pub fn finalize_status(&mut self) {
        if let Some(st) = &self.payout_coupon_status {
            if st != "ok" {
                self.status = "FAILED_COUPON".into();
                return;
            }
        }
        if self.dispensed_amount > 0 && self.dispensed_amount >= self.requested_amount {
            self.status = "OK".into();
            return;
        }
        if self.dispensed_amount > 0 && self.dispensed_amount < self.requested_amount {
            self.status = "PARTIAL".into();
            return;
        }
        for de in &self.dispensed_events {
            if let Some(detail) = de.get("statusDetail").and_then(Value::as_str) {
                let cls = crate::utils::classify_dispense_error(Some(detail));
                self.status = cls.to_string();
                self.error_detail = Some(detail.to_string());
                return;
            }
        }
        if self
            .events
            .iter()
            .any(|e| matches!(e.kind.as_str(), "cannotContinue" | "errorOccurs"))
        {
            self.status = "FAILED_HARDWARE".into();
            return;
        }
        self.status = "INCOMPLETE".into();
    }

    pub fn duration_sec(&self) -> f64 {
        match (self.start_dt, self.end_dt) {
            (Some(a), Some(b)) => (b - a).num_milliseconds() as f64 / 1000.0,
            _ => 0.0,
        }
    }

    pub fn has_suspicious_match(&self) -> bool {
        self.suspicion_flags.iter().any(|f| !f.is_empty())
    }

    pub fn suspicious_count(&self) -> usize {
        self.suspicion_flags
            .iter()
            .filter(|f| !f.is_empty())
            .count()
    }

    pub fn all_suspicion_flags(&self) -> Vec<String> {
        self.suspicion_flags.iter().flatten().cloned().collect()
    }

    pub fn display_denom(&self, raw: i64) -> f64 {
        match denom_lookup(raw) {
            Some(v) => v as f64,
            None => denom_val(raw),
        }
    }
}
