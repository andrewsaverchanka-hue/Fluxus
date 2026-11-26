//! W2 frontend analyzer (skills-battle-frontend log).
//!
//! Faithful port of `parsers/w2_frontend_parser.py`.
//!
//! Porting note: four Python regexes use a lookahead (`(?=\\?"|$)`) to stop at
//! the next escaped-or-plain quote. Rust's `regex` crate has no lookahead, so
//! the equivalent is a greedy-to-quote capture plus a trailing-backslash trim,
//! which reproduces the same boundaries for JSON-escaped payloads.

use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::OnceLock;

use chrono::{NaiveDate, NaiveDateTime};
use regex::{Regex, RegexBuilder};
use serde::Serialize;
use serde_json::{json, Map, Value};

use crate::utils::for_each_line;
use crate::w2_models::{BoxEntry, JsonMap, W2CashEvent, W2Event, W2WithdrawSession};

fn map_from(pairs: Vec<(&str, Value)>) -> JsonMap {
    let mut m = Map::new();
    for (k, v) in pairs {
        m.insert(k.to_string(), v);
    }
    m
}

// Two patterns are kept for parity with the Python module even though the
// current code paths don't consult them.
#[allow(dead_code)]
struct FrontendRegexes {
    ts: Regex,
    payout_trx: Regex,
    coupon_low: Regex,
    group_uuid: Regex,
    payout_status: Regex,
    amount_low: Regex,
    payout_api_trx: Regex,
    payout_api_login: Regex,
    insert_trx: Regex,
    insert_sum: Regex,
    insert_user: Regex,
    active_login: Regex,
    update_id: Regex,
    dispense_commands: Regex,
    dispense_snapshot: Regex,
    dispensed_obj: Regex,
    model: Regex,
    device: Regex,
    vendor: Regex,
    box_rows: Regex,
    update_sum: Regex,
    update_status: Regex,
    error_trx: Regex,
    create_coupon_value: Regex,
    complete_trx: Regex,
    last_login: Regex,
    // cash events
    out_by_notes: Regex,
    taken_disp: Regex,
    taken_reject: Regex,
    added_boxes: Regex,
    total_sum: Regex,
    total_out: Regex,
    total_loaded: Regex,
    in_by_dates: Regex,
    note_entry: Regex,
    box_entry: Regex,
    // stats normalization
    susp_gap: Regex,
    susp_bank: Regex,
    susp_vs: Regex,
}

impl FrontendRegexes {
    fn get() -> &'static FrontendRegexes {
        static RE: OnceLock<FrontendRegexes> = OnceLock::new();
        RE.get_or_init(|| {
            let ci = |p: &str| RegexBuilder::new(p).case_insensitive(true).build().unwrap();
            FrontendRegexes {
                ts: Regex::new(
                    r"\[(\d{1,2})/(\d{1,2})/(\d{4}),\s*(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)\]",
                )
                .unwrap(),
                payout_trx: Regex::new(r#""trx"\s*:\s*"([0-9a-f\-]{36})""#).unwrap(),
                coupon_low: Regex::new(r#""coupon"\s*:\s*\{\s*"low"\s*:\s*(-?\d+)"#).unwrap(),
                group_uuid: Regex::new(r#""groupUuid"\s*:\s*"(\w+)""#).unwrap(),
                payout_status: Regex::new(r#""payoutCoupon"\s*:\s*\{\s*"status"\s*:\s*"(\w+)""#)
                    .unwrap(),
                amount_low: Regex::new(r#""amount"\s*:\s*\{\s*"low"\s*:\s*(-?\d+)"#).unwrap(),
                payout_api_trx: Regex::new(r"trx\s+([0-9a-f\-]{36})").unwrap(),
                payout_api_login: Regex::new(r"login:\s*(\d+)").unwrap(),
                insert_trx: Regex::new(r"`trx`\s*=\s*'([0-9a-f\-]{36})'").unwrap(),
                insert_sum: Regex::new(r"`sum`\s*=\s*(\d+)").unwrap(),
                insert_user: Regex::new(r"`user_login`\s*=\s*'(\d+)'").unwrap(),
                active_login: Regex::new(r"login:\s*'(\d+)'").unwrap(),
                update_id: Regex::new(r"where id\s*=\s*(\d+)").unwrap(),
                dispense_commands: Regex::new(r"commands\s+(\[.*?\])").unwrap(),
                dispense_snapshot: Regex::new(r"nominal and current count\s+(\[\[.*?\]\])")
                    .unwrap(),
                dispensed_obj: Regex::new(r#""dispensed"\s*:\s*(\{[^{}]*\})"#).unwrap(),
                model: Regex::new(r#""model"\s*:\s*"([^"]+)""#).unwrap(),
                device: Regex::new(r#""device"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)""#).unwrap(),
                vendor: Regex::new(r#""vendor"\s*:\s*"([^"]+)""#).unwrap(),
                box_rows: Regex::new(r"\((\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)").unwrap(),
                update_sum: Regex::new(r"`sum`\s*=\s*(\d+)").unwrap(),
                update_status: Regex::new(r"`status`\s*=\s*'(\w+)'").unwrap(),
                error_trx: Regex::new(r"trx[:\s]+([0-9a-f\-]{36})").unwrap(),
                create_coupon_value: Regex::new(r#""value"\s*:\s*\{\s*"low"\s*:\s*(-?\d+)"#)
                    .unwrap(),
                complete_trx: Regex::new(r"transactionId:\s*([0-9a-f\-]{36})").unwrap(),
                last_login: Regex::new(r"login:\s*(\d+)").unwrap(),
                out_by_notes: ci(r#"outByNotes["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*)"#),
                taken_disp: ci(
                    r#"takenFromDispenserBoxes["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*)"#,
                ),
                taken_reject: ci(
                    r#"takenFromRejectedBox["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*)"#,
                ),
                added_boxes: ci(
                    r#"addedToDispenserBoxes["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*)"#,
                ),
                total_sum: ci(r#"totalSum["\s]*(?:,\s*"value"\s*)?[":=\s]+"?\$?(\d+)"#),
                total_out: ci(r#"totalOut["\s]*(?:,\s*"value"\s*)?[":=\s]+"?\$?(\d+)"#),
                in_by_dates: ci(
                    r#"inByDatesAndNotes["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*)"#,
                ),
                total_loaded: ci(r#"totalLoaded["\s]*(?:,\s*"value"\s*)?[":=\s]+"?\$?(\d+)"#),
                note_entry: Regex::new(r"\$(\d+)\s*\*\s*(\d+)\s*=\s*\$?(\d+)").unwrap(),
                box_entry: ci(
                    r"Box\s+(\d+)[^\n]*\n+(?:Inserted:\s*\n+)?\$(\d+)\s*\*\s*(\d+)\s*=\s*\$?(\d+)",
                ),
                susp_gap: Regex::new(r"[+-]?\d+s").unwrap(),
                susp_bank: Regex::new(r"bank \d+(,\d+)*").unwrap(),
                susp_vs: Regex::new(r"vs \[[^\]]+\]").unwrap(),
            }
        })
    }
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct BoxAgg {
    pub dispensed: i64,
    pub rejected: i64,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct FrontendStats {
    pub total: i64,
    pub status_counts: BTreeMap<String, i64>,
    pub denom_dispensed: BTreeMap<String, i64>,
    pub boxes_by_device: BTreeMap<String, BTreeMap<String, BoxAgg>>,
    pub error_reasons: BTreeMap<String, i64>,
    pub hardware_reasons: BTreeMap<String, i64>,
    pub jam_reasons: BTreeMap<String, i64>,
    pub empty_reasons: BTreeMap<String, i64>,
    pub timeout_reasons: BTreeMap<String, i64>,
    pub coupon_reasons: BTreeMap<String, i64>,
    pub total_requested_cents: i64,
    pub total_dispensed_cents: i64,
    pub dispensers: BTreeMap<String, Value>,
    pub lines_processed: i64,
    pub lines_matched: i64,
    pub suspicious_total: i64,
    pub suspicion_reasons: BTreeMap<String, i64>,
    pub cash_events_count: i64,
    pub cash_events_by_type: BTreeMap<String, i64>,
}

#[derive(Debug, Serialize)]
pub struct W2FrontendResult {
    pub sessions: Vec<W2WithdrawSession>,
    pub cash_events: Vec<W2CashEvent>,
    pub stats: FrontendStats,
}

pub struct W2FrontendAnalyzer {
    pub sessions: Vec<W2WithdrawSession>,
    index_by_trx: HashMap<String, usize>,
    pub dispensers: BTreeMap<String, Value>,
    pub lines_processed: i64,
    pub lines_matched: i64,
    active_trx_by_user: HashMap<String, String>,
    active_trx: Option<String>,
    tt_id_to_trx: HashMap<i64, String>,
    pub cash_events: Vec<W2CashEvent>,
    seen_cash_events: HashMap<(String, String), ()>,
}

impl Default for W2FrontendAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl W2FrontendAnalyzer {
    pub fn new() -> Self {
        W2FrontendAnalyzer {
            sessions: Vec::new(),
            index_by_trx: HashMap::new(),
            dispensers: BTreeMap::new(),
            lines_processed: 0,
            lines_matched: 0,
            active_trx_by_user: HashMap::new(),
            active_trx: None,
            tt_id_to_trx: HashMap::new(),
            cash_events: Vec::new(),
            seen_cash_events: HashMap::new(),
        }
    }

    pub fn parse_file(&mut self, path: &Path) -> std::io::Result<()> {
        for_each_line(path, |line| self.process_line(line))
    }

    pub fn parse_str(&mut self, content: &str) {
        for line in content.split('\n') {
            let line = line.strip_suffix('\r').unwrap_or(line);
            self.process_line(line);
        }
    }

    fn extract_dt(&self, line: &str) -> Option<NaiveDateTime> {
        let c = FrontendRegexes::get().ts.captures(line)?;
        let month: u32 = c[1].parse().ok()?;
        let day: u32 = c[2].parse().ok()?;
        let year: i32 = c[3].parse().ok()?;
        let mut h: u32 = c[4].parse().ok()?;
        let mi: u32 = c[5].parse().ok()?;
        let s: u32 = c[6].parse().ok()?;
        let ampm = &c[7];
        if ampm == "PM" && h != 12 {
            h += 12;
        }
        if ampm == "AM" && h == 12 {
            h = 0;
        }
        NaiveDate::from_ymd_opt(year, month, day).and_then(|d| d.and_hms_opt(h, mi, s))
    }

    fn ensure_session(&mut self, trx: &str) -> usize {
        if let Some(&idx) = self.index_by_trx.get(trx) {
            return idx;
        }
        let idx = self.sessions.len();
        self.sessions.push(W2WithdrawSession::new(trx));
        self.index_by_trx.insert(trx.to_string(), idx);
        idx
    }

    fn extract_login(&self, line: &str) -> Option<String> {
        FrontendRegexes::get()
            .active_login
            .captures(line)
            .map(|c| c[1].to_string())
    }

    fn resolve_trx(&self, login: Option<String>) -> Option<String> {
        match login {
            Some(l) => self.active_trx_by_user.get(&l).cloned(),
            None => self.active_trx.clone(),
        }
    }

    fn event(&self, kind: &str, dt: NaiveDateTime, line: &str, n: usize, data: JsonMap) -> W2Event {
        let raw: String = line.trim().chars().take(n).collect();
        W2Event::new(kind, dt, raw, data)
    }

    pub fn process_line(&mut self, line: &str) {
        self.lines_processed += 1;
        let Some(dt) = self.extract_dt(line) else {
            return;
        };
        let re = FrontendRegexes::get();

        // ---- payoutCoupon ----
        if line.contains("payoutCoupon") && (line.contains("status") || line.contains("coupon")) {
            if let Some(c) = re.payout_trx.captures(line) {
                let trx = c[1].to_string();
                let idx = self.ensure_session(&trx);
                if let Some(m) = re.coupon_low.captures(line) {
                    let v: i64 = m[1].parse().unwrap_or(0);
                    self.sessions[idx].coupon = Some(v.abs().to_string());
                }
                if let Some(m) = re.group_uuid.captures(line) {
                    self.sessions[idx].group_uuid = Some(m[1].to_string());
                }
                if let Some(m) = re.payout_status.captures(line) {
                    self.sessions[idx].payout_coupon_status = Some(m[1].to_string());
                }
                if let Some(m) = re.amount_low.captures(line) {
                    let v: i64 = m[1].parse().unwrap_or(0);
                    self.sessions[idx].requested_amount = v.abs();
                }
                let status = self.sessions[idx].payout_coupon_status.clone();
                let amount = self.sessions[idx].requested_amount;
                let ev = self.event(
                    "payoutCoupon",
                    dt,
                    line,
                    200,
                    map_from(vec![
                        (
                            "status",
                            match status {
                                Some(s) => json!(s),
                                None => Value::Null,
                            },
                        ),
                        ("amount", json!(amount)),
                    ]),
                );
                self.sessions[idx].add_event(ev);
                self.active_trx = Some(trx);
                self.lines_matched += 1;
            }
            return;
        }

        if line.contains("payoutCouponAPI") {
            if let Some(c) = re.payout_api_trx.captures(line) {
                let trx = c[1].to_string();
                let idx = self.ensure_session(&trx);
                if let Some(m) = re.payout_api_login.captures(line) {
                    let login = m[1].to_string();
                    self.sessions[idx].user_login = Some(login.clone());
                    self.active_trx_by_user.insert(login, trx.clone());
                }
                let ev = self.event("payoutCouponAPI", dt, line, 200, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        if line.contains("INSERT INTO terminal_balances") {
            if let Some(c) = re.insert_trx.captures(line) {
                let trx = c[1].to_string();
                let idx = self.ensure_session(&trx);
                if let Some(m) = re.insert_sum.captures(line) {
                    self.sessions[idx].requested_amount = m[1].parse().unwrap_or(0);
                }
                if let Some(m) = re.insert_user.captures(line) {
                    let login = m[1].to_string();
                    self.sessions[idx].user_login = Some(login.clone());
                    self.active_trx_by_user.insert(login, trx.clone());
                }
                let ev = self.event("insertBalance", dt, line, 150, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        if line.contains("UPDATE terminal_balances") && line.contains("in_progress") {
            let id_m = re.update_id.captures(line);
            let login = self.extract_login(line);
            let trx = self.resolve_trx(login);
            if let (Some(trx), Some(c)) = (trx, id_m) {
                let idx = self.ensure_session(&trx);
                self.sessions[idx].balance_id = c[1].parse::<i64>().ok();
                let ev = self.event("balanceInProgress", dt, line, 150, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        if line.contains("INSERT INTO terminal_transactions ")
            || line.contains("INSERT INTO terminal_transactions SET")
        {
            if let Some(c) = re.insert_trx.captures(line) {
                let trx = c[1].to_string();
                let idx = self.ensure_session(&trx);
                let ev = self.event("insertTransaction", dt, line, 150, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        // ---- DispenserService.dispense ----
        if line.contains("DispenserService.dispense") {
            let login = self.extract_login(line);
            let Some(trx) = self.resolve_trx(login) else {
                return;
            };
            let idx = self.ensure_session(&trx);
            if let Some(c) = re.dispense_commands.captures(line) {
                if let Ok(Value::Array(cmds)) = serde_json::from_str::<Value>(&c[1]) {
                    self.sessions[idx].commands = cmds.clone();
                    if !cmds.is_empty() && self.sessions[idx].dispenser_device_id.is_none() {
                        self.sessions[idx].dispenser_device_id = cmds[0]
                            .get("deviceId")
                            .and_then(Value::as_str)
                            .map(str::to_string);
                    }
                }
            }
            if let Some(m) = re.dispense_snapshot.captures(line) {
                if let Ok(Value::Array(snap)) = serde_json::from_str::<Value>(&m[1]) {
                    self.sessions[idx].cassette_snapshot = snap;
                }
            }
            let ev = self.event("dispenseCommand", dt, line, 300, Map::new());
            self.sessions[idx].add_event(ev);
            self.lines_matched += 1;
            return;
        }

        // ---- processNewEvent dispensed ----
        if line.contains("processNewEvent") && line.contains("\"type\":16") {
            let login = self.extract_login(line);
            let Some(trx) = self.resolve_trx(login) else {
                return;
            };
            let idx = self.ensure_session(&trx);
            let mut disp = Value::Object(Map::new());
            if let Some(m) = re.dispensed_obj.captures(line) {
                if let Ok(v) = serde_json::from_str::<Value>(&m[1]) {
                    disp = v;
                }
            }
            let model = re.model.captures(line).map(|m| m[1].to_string());
            let device = re.device.captures(line).map(|m| m[1].to_string());
            let vendor = re.vendor.captures(line).map(|m| m[1].to_string());
            if let Some(dev_id) = device {
                self.sessions[idx].dispenser_device_id = Some(dev_id.clone());
                if let Some(m) = &model {
                    self.sessions[idx].dispenser_model = Some(m.clone());
                }
                if let Some(v) = &vendor {
                    self.sessions[idx].dispenser_vendor = Some(v.clone());
                }
                let m = self.sessions[idx]
                    .dispenser_model
                    .clone()
                    .unwrap_or_else(|| "?".into());
                let v = self.sessions[idx]
                    .dispenser_vendor
                    .clone()
                    .unwrap_or_else(|| "?".into());
                self.dispensers
                    .entry(dev_id)
                    .or_insert_with(|| json!({"model": m, "vendor": v}));
            }
            self.sessions[idx].dispensed_events.push(disp.clone());
            if disp.get("statusDetail").is_some() {
                self.sessions[idx].retry_count += 1;
            }
            let s = &self.sessions[idx];
            let _ = s;
            let data = map_from(vec![
                (
                    "bankIndex",
                    disp.get("bankIndex").cloned().unwrap_or(Value::Null),
                ),
                (
                    "requested",
                    disp.get("notesAmountRequested")
                        .cloned()
                        .unwrap_or(Value::Null),
                ),
                (
                    "dispensed",
                    disp.get("notesAmountDispensed")
                        .cloned()
                        .unwrap_or(json!(0)),
                ),
                (
                    "statusDetail",
                    disp.get("statusDetail").cloned().unwrap_or(Value::Null),
                ),
            ]);
            let ev = self.event("dispensedEvent", dt, line, 300, data);
            self.sessions[idx].add_event(ev);
            self.lines_matched += 1;
            return;
        }

        if line.contains("INSERT INTO terminal_transactions_box") {
            let login = self.extract_login(line);
            let Some(trx) = self.resolve_trx(login) else {
                return;
            };
            let idx = self.ensure_session(&trx);
            let rows: Vec<BoxEntry> = re
                .box_rows
                .captures_iter(line)
                .map(|c| BoxEntry {
                    tt_id: c[1].parse().unwrap_or(0),
                    r#box: c[2].parse().unwrap_or(0),
                    nominal: c[3].parse().unwrap_or(0),
                    count: c[4].parse().unwrap_or(0),
                    is_rejected: c[5].parse().unwrap_or(0),
                })
                .collect();
            for row in rows {
                self.sessions[idx].transaction_id = Some(row.tt_id);
                self.tt_id_to_trx.insert(row.tt_id, trx.clone());
                self.sessions[idx].boxes.push(row);
            }
            let ev = self.event("insertBox", dt, line, 200, Map::new());
            self.sessions[idx].add_event(ev);
            self.lines_matched += 1;
            return;
        }

        if line.contains("UPDATE terminal_transactions")
            && line.contains("status")
            && (line.contains("ok") || line.contains("refund"))
        {
            let sum_m = re.update_sum.captures(line).map(|c| c[1].to_string());
            let id_m = re.update_id.captures(line).map(|c| c[1].to_string());
            let status_m = re.update_status.captures(line).map(|c| c[1].to_string());
            if let Some(id_s) = id_m {
                let tt_id: i64 = id_s.parse().unwrap_or(0);
                let trx = self.tt_id_to_trx.get(&tt_id).cloned().or_else(|| {
                    let login = self.extract_login(line);
                    self.resolve_trx(login)
                });
                if let Some(trx) = trx {
                    let idx = self.ensure_session(&trx);
                    self.sessions[idx].transaction_id = Some(tt_id);
                    if let Some(s) = sum_m {
                        self.sessions[idx].dispensed_amount = s.parse().unwrap_or(0);
                    }
                    let data = map_from(vec![(
                        "db_status",
                        match status_m {
                            Some(s) => json!(s),
                            None => Value::Null,
                        },
                    )]);
                    let ev = self.event("updateTransaction", dt, line, 200, data);
                    self.sessions[idx].add_event(ev);
                    self.lines_matched += 1;
                }
            }
            return;
        }

        if line.contains("cannotContinue") && line.contains('{') {
            let login = self.extract_login(line);
            if let Some(trx) = self.resolve_trx(login) {
                let idx = self.ensure_session(&trx);
                let ev = self.event("cannotContinue", dt, line, 300, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        if line.contains("Error occurs while withdrawing") {
            if let Some(c) = re.error_trx.captures(line) {
                let trx = c[1].to_string();
                let idx = self.ensure_session(&trx);
                let ev = self.event("errorOccurs", dt, line, 250, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        if line.contains("createCoupon") && line.contains("status") {
            let login = self.extract_login(line);
            let trx = self.resolve_trx(login);
            let val_m = re
                .create_coupon_value
                .captures(line)
                .map(|c| c[1].to_string());
            if let (Some(trx), Some(v)) = (trx, val_m) {
                let idx = self.ensure_session(&trx);
                let v: i64 = v.parse().unwrap_or(0);
                let coupon = v.abs().to_string();
                self.sessions[idx].recovery_coupon = Some(coupon.clone());
                let data = map_from(vec![("value", json!(coupon))]);
                let ev = self.event("createCoupon", dt, line, 250, data);
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        // ===== Cash events =====
        if line.contains("COLLECTION REPORT")
            || line.contains("Withdraw report")
            || line.contains("Cash in")
            || line.contains("takenFromDispenserBoxes")
            || line.contains("addedToDispenserBoxes")
            || line.contains("outByNotes")
        {
            self.try_parse_cash_event(line, dt);
            // Python returns here: a collection/withdraw print is not a
            // receipt print, even if the line also contains "printSlip"
            // and the prefix word "balance".
            return;
        }

        if line.contains("printSlip") && (line.contains("BARCODE") || line.contains("balance")) {
            let login = self.extract_login(line);
            if let Some(trx) = self.resolve_trx(login) {
                let idx = self.ensure_session(&trx);
                self.sessions[idx].receipt_printed = true;
                let ev = self.event("printSlip", dt, line, 150, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        if line.contains("Complete transaction") {
            if let Some(c) = re.complete_trx.captures(line) {
                let trx = c[1].to_string();
                let idx = self.ensure_session(&trx);
                let ev = self.event("completeTransaction", dt, line, 200, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

        if line.contains("Redirect to HOME") && line.contains("withdraw") {
            let login = self.extract_login(line);
            if let Some(trx) = self.resolve_trx(login) {
                let idx = self.ensure_session(&trx);
                let is_failed = line.contains("failed withdraw");
                let kind = if is_failed {
                    "redirectFailed"
                } else {
                    "redirectOk"
                };
                let ev = self.event(kind, dt, line, 150, Map::new());
                self.sessions[idx].add_event(ev);
                self.lines_matched += 1;
            }
            return;
        }

    }

    // ===================== cash events =====================

    fn detect_cash_event_type(&self, line: &str) -> Option<&'static str> {
        if line.contains("COLLECTION REPORT") {
            return Some(W2CashEvent::TYPE_COLLECTION);
        }
        if line.contains("Withdraw report") || line.contains("takenFromDispenserBoxes") {
            return Some(W2CashEvent::TYPE_WITHDRAW);
        }
        if line.contains("Cash in") || line.contains("addedToDispenserBoxes") {
            return Some(W2CashEvent::TYPE_CASH_IN);
        }
        if line.contains("outByNotes") {
            return Some(W2CashEvent::TYPE_COLLECTION);
        }
        None
    }

    fn unescape_placeholder(s: &str) -> String {
        s.replace("\\n", "\n").replace("\\\"", "\"")
    }

    /// Python captures lazily up to `\\?"` or end-of-string; the Rust pattern
    /// captures to the next quote, so a trailing escape backslash must go.
    fn clean_capture(s: &str) -> &str {
        s.strip_suffix('\\').unwrap_or(s)
    }

    fn parse_notes_by_nominal(text: &str) -> BTreeMap<i64, i64> {
        let mut result = BTreeMap::new();
        for c in FrontendRegexes::get().note_entry.captures_iter(text) {
            let nominal: i64 = c[1].parse().unwrap_or(0);
            let count: i64 = c[2].parse().unwrap_or(0);
            *result.entry(nominal).or_insert(0) += count;
        }
        result
    }

    fn parse_boxes_with_nominal(text: &str) -> BTreeMap<i64, i64> {
        let mut result = BTreeMap::new();
        for c in FrontendRegexes::get().box_entry.captures_iter(text) {
            let box_num: i64 = c[1].parse().unwrap_or(0);
            let cnt: i64 = c[3].parse().unwrap_or(0);
            *result.entry(box_num).or_insert(0) += cnt;
        }
        result
    }

    fn try_parse_cash_event(&mut self, line: &str, dt: NaiveDateTime) {
        let Some(event_type) = self.detect_cash_event_type(line) else {
            return;
        };
        let key = (
            event_type.to_string(),
            dt.format("%Y-%m-%dT%H:%M:%S").to_string(),
        );
        if self.seen_cash_events.contains_key(&key) {
            return;
        }
        let unescaped = Self::unescape_placeholder(line);
        let re = FrontendRegexes::get();
        let mut event = W2CashEvent::new(event_type, dt);

        if event_type == W2CashEvent::TYPE_CASH_IN {
            if let Some(c) = re.added_boxes.captures(&unescaped) {
                let payload = Self::unescape_placeholder(Self::clean_capture(&c[1]));
                event.boxes_delta = Self::parse_boxes_with_nominal(&payload);
                if let Some(t) = re.total_sum.captures(&unescaped) {
                    event.total_cents = t[1].parse::<i64>().unwrap_or(0) * 100;
                }
                event.raw_details = payload.chars().take(500).collect();
            }
        } else if event_type == W2CashEvent::TYPE_WITHDRAW {
            if let Some(c) = re.taken_disp.captures(&unescaped) {
                let payload = Self::unescape_placeholder(Self::clean_capture(&c[1]));
                let raw_boxes = Self::parse_boxes_with_nominal(&payload);
                event.boxes_delta = raw_boxes.into_iter().map(|(b, c)| (b, -c)).collect();
                let p: String = payload.chars().take(300).collect();
                event.raw_details = format!("Dispenser: {}", p);
            }
            if let Some(c) = re.taken_reject.captures(&unescaped) {
                let payload = Self::unescape_placeholder(Self::clean_capture(&c[1]));
                event.reject_delta = Self::parse_notes_by_nominal(&payload);
                let p: String = payload.chars().take(200).collect();
                if !event.raw_details.is_empty() {
                    event.raw_details.push_str(&format!(" | Reject: {}", p));
                } else {
                    let p300: String = payload.chars().take(300).collect();
                    event.raw_details = format!("Reject: {}", p300);
                }
            }
            if let Some(t) = re.total_sum.captures(&unescaped) {
                event.total_cents = t[1].parse::<i64>().unwrap_or(0) * 100;
            }
        } else if event_type == W2CashEvent::TYPE_COLLECTION {
            if let Some(c) = re.out_by_notes.captures(&unescaped) {
                let payload = Self::unescape_placeholder(Self::clean_capture(&c[1]));
                event.raw_by_nominal = Self::parse_notes_by_nominal(&payload);
                let p: String = payload.chars().take(300).collect();
                event.raw_details = format!("OUT: {}", p);
            }
            if let Some(t) = re.total_out.captures(&unescaped) {
                event.total_cents = t[1].parse::<i64>().unwrap_or(0) * 100;
            }
            // Refill side of the report: cash loaded into the dispensers
            // during the same period.
            if let Some(t) = re.total_loaded.captures(&unescaped) {
                event.total_loaded_cents = t[1].parse::<i64>().unwrap_or(0) * 100;
            }
            if let Some(c) = re.in_by_dates.captures(&unescaped) {
                let payload = Self::unescape_placeholder(Self::clean_capture(&c[1]));
                event.loaded_by_nominal = Self::parse_notes_by_nominal(&payload);
            }
        }

        if !event.boxes_delta.is_empty()
            || !event.reject_delta.is_empty()
            || !event.raw_by_nominal.is_empty()
            || !event.loaded_by_nominal.is_empty()
            || event.total_loaded_cents != 0
            || event.total_cents != 0
        {
            self.cash_events.push(event);
            self.seen_cash_events.insert(key, ());
            self.lines_matched += 1;
        }
    }

    pub fn get_last_cash_event(
        &self,
        before_dt: NaiveDateTime,
        event_type: Option<&str>,
    ) -> Option<&W2CashEvent> {
        self.cash_events
            .iter()
            .filter(|e| e.dt < before_dt && event_type.map_or(true, |t| e.event_type == t))
            .max_by_key(|e| e.dt)
    }

    pub fn build_sessions(&mut self) {
        for s in self.sessions.iter_mut() {
            s.finalize_status();
        }
        self.sessions.sort_by_key(|s| s.start_dt);
        self.cash_events.sort_by_key(|e| e.dt);
    }

    pub fn stats(&self) -> FrontendStats {
        let mut st = FrontendStats::default();
        st.total = self.sessions.len() as i64;
        st.dispensers = self.dispensers.clone();
        st.lines_processed = self.lines_processed;
        st.lines_matched = self.lines_matched;
        st.cash_events_count = self.cash_events.len() as i64;
        // Python always emits all three keys, even when zero.
        for k in [
            W2CashEvent::TYPE_COLLECTION,
            W2CashEvent::TYPE_WITHDRAW,
            W2CashEvent::TYPE_CASH_IN,
        ] {
            st.cash_events_by_type.insert(k.to_string(), 0);
        }
        for e in &self.cash_events {
            *st.cash_events_by_type
                .entry(e.event_type.clone())
                .or_insert(0) += 1;
        }

        for s in &self.sessions {
            *st.status_counts.entry(s.status.clone()).or_insert(0) += 1;
            st.total_requested_cents += s.requested_amount;
            st.total_dispensed_cents += s.dispensed_amount;
            if let Some(detail) = &s.error_detail {
                *st.error_reasons.entry(detail.clone()).or_insert(0) += 1;
                match s.status.as_str() {
                    "FAILED_HARDWARE" => {
                        *st.hardware_reasons.entry(detail.clone()).or_insert(0) += 1
                    }
                    "FAILED_JAM" => *st.jam_reasons.entry(detail.clone()).or_insert(0) += 1,
                    "FAILED_EMPTY" => *st.empty_reasons.entry(detail.clone()).or_insert(0) += 1,
                    "FAILED_TIMEOUT" => *st.timeout_reasons.entry(detail.clone()).or_insert(0) += 1,
                    _ => {}
                }
            }
            if s.status == "FAILED_COUPON" {
                if let Some(c) = &s.payout_coupon_status {
                    *st.coupon_reasons.entry(c.clone()).or_insert(0) += 1;
                }
            }
            let dev = s.dispenser_device_id.clone().unwrap_or_else(|| "?".into());
            for b in &s.boxes {
                let key = format!("{}|{}", b.r#box, b.nominal);
                let agg = st
                    .boxes_by_device
                    .entry(dev.clone())
                    .or_default()
                    .entry(key)
                    .or_default();
                if b.is_rejected == 2 {
                    agg.dispensed += b.count;
                    *st.denom_dispensed.entry(b.nominal.to_string()).or_insert(0) += b.count;
                } else if b.is_rejected == 1 {
                    agg.rejected += b.count;
                }
            }
            if s.has_suspicious_match() {
                st.suspicious_total += 1;
                let re = FrontendRegexes::get();
                for flags in &s.suspicion_flags {
                    for flag in flags {
                        let norm = re.susp_vs.replace_all(flag, "vs [...]");
                        let norm = re.susp_bank.replace_all(&norm, "bank N");
                        let norm = re.susp_gap.replace_all(&norm, "Ns");
                        *st.suspicion_reasons.entry(norm.to_string()).or_insert(0) += 1;
                    }
                }
            }
        }
        st
    }

    pub fn result(&self) -> W2FrontendResult {
        W2FrontendResult {
            sessions: self.sessions.clone(),
            cash_events: self.cash_events.clone(),
            stats: self.stats(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collection_report_carries_loaded_refill_data() {
        let line = concat!(
            "[11/17/2026, 7:39:34 AM]: -> {\"printSlip\":{\"placeholders\":[",
            "{\"name\":\"totalOut\",\"value\":\"$14380\"},",
            "{\"name\":\"outByNotes\",\"value\":\"$5*504 = $2520\\n\\n$20*593 = $11860\"},",
            "{\"name\":\"totalLoaded\",\"value\":\"$17510\"},",
            "{\"name\":\"inByDatesAndNotes\",\"value\":\"2026-11-10 09:19:49\\n$5*750 = $3750\\n\\n$20*688 = $13760\"}",
            "],\"rawTemplate\":\"COLLECTION REPORT\"}}}"
        );
        let mut a = W2FrontendAnalyzer::new();
        a.process_line(line);
        assert_eq!(a.cash_events.len(), 1, "collection event not created");
        let e = &a.cash_events[0];
        assert_eq!(e.event_type, W2CashEvent::TYPE_COLLECTION);
        assert_eq!(e.total_cents, 1_438_000);
        assert_eq!(e.total_loaded_cents, 1_751_000);
        assert_eq!(e.raw_by_nominal.get(&5), Some(&504));
        assert_eq!(e.raw_by_nominal.get(&20), Some(&593));
        assert_eq!(e.loaded_by_nominal.get(&5), Some(&750));
        assert_eq!(e.loaded_by_nominal.get(&20), Some(&688));
    }
}
