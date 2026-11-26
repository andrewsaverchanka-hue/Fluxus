//! W2 low-level dispenser analyzer (LCDM2000 / LCDM4000).
//!
//! Faithful port of `parsers/w2_lowlevel_parser.py`. Structure and regex names
//! mirror the Python class so the two can be diffed side by side.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::OnceLock;

use chrono::{NaiveDate, NaiveDateTime};
use regex::Regex;
use serde::Serialize;
use serde_json::{json, Map, Value};

use crate::constants::{SUSPICION_RETRY_GAP_SEC, SUSPICION_START_GAP_SEC};
use crate::utils::{classify_lcdm4000_code, for_each_line, for_each_line_bytes, strip_ansi};
use crate::w2_models::{JsonMap, W2LowLevelSession, W2WithdrawSession};

pub struct LowLevelRegexes {
    dt: Regex,
    handle_header: Regex,
    handle_cmd: Regex,
    worker_start: Regex,
    start: Regex,
    raw_hex: Regex,
    sensors: Regex,
    status: Regex,
    cmd_ok: Regex,
    cmd_err: Regex,
    cmd_err_text: Regex,
    settled: Regex,
    event_ok: Regex,
    job_done: Regex,
    lcdm4000_seq_start: Regex,
    lcdm4000_write_payload: Regex,
    lcdm4000_err: Regex,
    lcdm4000_idx: Regex,
    lcdm4000_note_result: Regex,
    lcdm4000_note_desc: Regex,
    lcdm4000_note_counts: Regex,
    lcdm4000_stop: Regex,
    lcdm4000_finished: Regex,
    lcdm4000_status_sensors: Regex,
    lcdm4000_status_cassette: Regex,
    serial_path: Regex,
}

impl LowLevelRegexes {
    pub fn get() -> &'static LowLevelRegexes {
        static RE: OnceLock<LowLevelRegexes> = OnceLock::new();
        RE.get_or_init(|| LowLevelRegexes {
            dt: Regex::new(r"(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})").unwrap(),
            handle_header: Regex::new(r"Handle Dispense Request:\s+reqID=(\d+)\s+commands=(\d+)").unwrap(),
            handle_cmd: Regex::new(
                r"Handle Dispense Request:\s+reqID=(\d+)\s+cmdIndex=(\d+)\s+bankIndex=(\d+)\s+amount=(\d+)",
            )
            .unwrap(),
            worker_start: Regex::new(r"Dispense worker:\s+job start\s+reqID=(\d+)\s+jobID=(\d+)").unwrap(),
            start: Regex::new(
                r"Dispense:\s+start\s+reqID=(\d+)\s+jobID=(\d+)\s+cmdIndex=(\d+)\s+bankIndex=(\d+)\s+amount=(\d+)",
            )
            .unwrap(),
            raw_hex: Regex::new(r"Dispense:\s+raw response\s+.*?hex=([0-9a-fA-F]+)").unwrap(),
            sensors: Regex::new(
                r"Dispense:\s+upper CHK=(\d+)\s+EXIT=(\d+);\s+lower CHK=(\d+)\s+EXIT=(\d+)",
            )
            .unwrap(),
            status: Regex::new(r"Dispense:\s+status=0x([0-9a-fA-F]+)\s+\(([^)]+)\)").unwrap(),
            cmd_ok: Regex::new(r"CommandDispense:\s+chk=(\d+)\s+disp=(\d+)\s+rej=(\d+)").unwrap(),
            cmd_err: Regex::new(
                r"Dispense:\s+error\s+reqID=(\d+)\s+jobID=(\d+)\s+cmdIndex=(\d+)\s+bankIndex=(\d+)\s+amount=(\d+)\s+chkSensor=(\d+)\s+dispensed=(\d+)\s+rejected=(\d+)",
            )
            .unwrap(),
            cmd_err_text: Regex::new(r#"error="([^"]+)""#).unwrap(),
            settled: Regex::new(
                r"Dispense:\s+settled\s+reqID=(\d+)\s+jobID=(\d+)\s+cmdIndex=(\d+)\s+bankIndex=(\d+)\s+amount=(\d+)\s+attempts=(\d+)",
            )
            .unwrap(),
            event_ok: Regex::new(
                r"Dispense:\s+event OK\s+reqID=(\d+)\s+jobID=(\d+)\s+cmdIndex=(\d+)\s+bankIndex=(\d+)\s+amount=(\d+)\s+requested=(\d+)\s+chk=(\d+)\s+dispensed=(\d+)\s+rejected=(\d+)",
            )
            .unwrap(),
            job_done: Regex::new(r"Dispense worker:\s+job done\s+reqID=(\d+)\s+jobID=(\d+)").unwrap(),
            lcdm4000_seq_start: Regex::new(
                r"LCDM4000 CCTalk:\s+Dispense sequence: start command.*?bankIndex=(\d+).*?(?:notesAmount|amount)=(\d+)",
            )
            .unwrap(),
            lcdm4000_write_payload: Regex::new(
                r"LCDM4000 CCTalk:\s+Processor:\s+Write:\s+Dispense.*?payload=([0-9a-fA-F]+)",
            )
            .unwrap(),
            lcdm4000_err: Regex::new(
                r#"ERR.*?LCDM4000 CCTalk:\s+Command:\s+Dispense\s+error="error code:\s+(\d+)\s+-\s+([^"]+)""#,
            )
            .unwrap(),
            lcdm4000_idx: Regex::new(r"idx=(\d+)").unwrap(),
            lcdm4000_note_result: Regex::new(
                r"LCDM4000 CCTalk:\s+Dispense:\s+note result.*?bankIndex=(\d+).*?chkSensorTotal=(\d+).*?deviceCode=(\d+)",
            )
            .unwrap(),
            lcdm4000_note_desc: Regex::new(r#"deviceDescription="([^"]+)""#).unwrap(),
            lcdm4000_note_counts: Regex::new(
                r"dispensedTotal=(\d+).*?noteDispensed=(\d+).*?noteN=(\d+).*?noteRejected=(\d+)",
            )
            .unwrap(),
            lcdm4000_stop: Regex::new(
                r"WRN.*?LCDM4000 CCTalk:\s+Dispense:\s+stop after device status.*?bankIndex=(\d+).*?deviceCode=(\d+)",
            )
            .unwrap(),
            lcdm4000_finished: Regex::new(
                r"LCDM4000 CCTalk:\s+Dispense:\s+finished.*?bankIndex=(\d+).*?chkSensorTotal=(\d+).*?dispensedTotal=(\d+).*?notesAmount=(\d+).*?rejectedTotal=(\d+)",
            )
            .unwrap(),
            lcdm4000_status_sensors: Regex::new(
                r"LCDM4000 CCTalk:\s+Command:\s+Status\s+SensorDVTL=(\w+)\s+SensorDVTR=(\w+)\s+SensorEJT=(\w+)\s+SensorEXIT=(\w+)\s+SensorRJT=(\w+)",
            )
            .unwrap(),
            lcdm4000_status_cassette: Regex::new(
                r"LCDM4000 CCTalk:\s+Command:\s+Status\s+Cassette(\d+)Exists=(\w+)\s+Cassette\d+Length=(\d+)\s+Cassette\d+Opacity=(\d+)\s+Cassette\d+Status=(\d+)",
            )
            .unwrap(),
            serial_path: Regex::new(r"serialPath=(\S+)").unwrap(),
        })
    }
}

fn map_from(pairs: Vec<(&str, Value)>) -> JsonMap {
    let mut m = Map::new();
    for (k, v) in pairs {
        m.insert(k.to_string(), v);
    }
    m
}

fn closed(r: &JsonMap) -> bool {
    r.get("closed").and_then(Value::as_bool).unwrap_or(false)
}

#[derive(Debug, Serialize)]
pub struct LowLevelResult {
    pub sessions: Vec<W2LowLevelSession>,
    pub lcdm_health: Vec<(String, Value)>,
    pub lcdm_health_changes: Vec<(String, Value)>,
    pub dispensers: BTreeMap<String, Value>,
    pub lines_processed: i64,
    pub lines_matched: i64,
}

pub struct W2LowLevelAnalyzer {
    pub sessions: Vec<W2LowLevelSession>,
    active_2000: BTreeMap<i64, usize>,
    active_4000: BTreeMap<i64, usize>,
    pub lcdm_health: Vec<(String, Value)>,
    pub lcdm_health_changes: Vec<(String, Value)>,
    last_sensor_state: Option<(String, String, String, String, String)>,
    last_cassette_state: BTreeMap<i64, (String, i64, i64, i64)>,
    pub dispensers: BTreeMap<String, Value>,
    pub lines_processed: i64,
    pub lines_matched: i64,
}

impl Default for W2LowLevelAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl W2LowLevelAnalyzer {
    pub fn new() -> Self {
        W2LowLevelAnalyzer {
            sessions: Vec::new(),
            active_2000: BTreeMap::new(),
            active_4000: BTreeMap::new(),
            lcdm_health: Vec::new(),
            lcdm_health_changes: Vec::new(),
            last_sensor_state: None,
            last_cassette_state: BTreeMap::new(),
            dispensers: BTreeMap::new(),
            lines_processed: 0,
            lines_matched: 0,
        }
    }

    pub fn parse_file(&mut self, path: &Path) -> std::io::Result<()> {
        // Байтовый префильтр: строку в текст превращаем только если в ней
        // есть маркер. На реальных логах это ~80 % строк, которые иначе
        // проходили проверку UTF-8 и strip_ansi впустую.
        for_each_line_bytes(path, |bytes| {
            self.lines_processed += 1;
            if memchr::memmem::find(bytes, b"Dispense").is_none()
                && memchr::memmem::find(bytes, b"Command: Status").is_none()
            {
                return;
            }
            let text = String::from_utf8_lossy(bytes);
            self.process_line_matched(&text);
        })
    }

    pub fn parse_str(&mut self, content: &str) {
        for line in content.split('\n') {
            let line = line.strip_suffix('\r').unwrap_or(line);
            self.process_line(line);
        }
    }

    fn extract_dt(&self, line: &str) -> Option<NaiveDateTime> {
        static YEAR: OnceLock<i32> = OnceLock::new();
        let year = *YEAR.get_or_init(|| {
            use chrono::Datelike;
            chrono::Local::now().year()
        });
        let c = LowLevelRegexes::get().dt.captures(line)?;
        let g = |i: usize| -> i64 { c.get(i).unwrap().as_str().parse().unwrap_or(0) };
        let (d1, d2, h, mi, s) = (g(1), g(2), g(3), g(4), g(5));
        NaiveDate::from_ymd_opt(year, d2 as u32, d1 as u32)
            .and_then(|d| d.and_hms_opt(h as u32, mi as u32, s as u32))
            .or_else(|| {
                NaiveDate::from_ymd_opt(year, d1 as u32, d2 as u32)
                    .and_then(|d| d.and_hms_opt(h as u32, mi as u32, s as u32))
            })
    }

    fn extract_serial_path(&self, line: &str) -> Option<String> {
        LowLevelRegexes::get()
            .serial_path
            .captures(line)
            .map(|c| c[1].to_string())
    }

    fn get_or_create_lcdm2000(&mut self, req_id: i64, line: &str) -> usize {
        if let Some(&idx) = self.active_2000.get(&req_id) {
            return idx;
        }
        let mut sess = W2LowLevelSession::default();
        sess.model = Some("LCDM2000".into());
        sess.req_id = Some(req_id);
        sess.serial_path = self.extract_serial_path(line);
        if let Some(sp) = &sess.serial_path {
            self.dispensers
                .entry(sp.clone())
                .or_insert_with(|| json!({"model": "LCDM2000"}));
        }
        let idx = self.sessions.len();
        self.sessions.push(sess);
        self.active_2000.insert(req_id, idx);
        idx
    }

    fn get_or_create_lcdm4000(&mut self, bank: i64, line: &str, idx_hint: Option<String>) -> usize {
        if let Some(&idx) = self.active_4000.get(&bank) {
            if !self.sessions[idx].closed {
                if let Some(hint) = idx_hint {
                    if self.sessions[idx].idx.is_none() {
                        self.sessions[idx].idx = Some(hint);
                    }
                }
                return idx;
            }
            self.active_4000.remove(&bank);
        }
        let mut sess = W2LowLevelSession::default();
        sess.model = Some("LCDM4000".into());
        sess.serial_path = self.extract_serial_path(line);
        sess.idx = idx_hint;
        if let Some(sp) = &sess.serial_path {
            self.dispensers
                .entry(sp.clone())
                .or_insert_with(|| json!({"model": "LCDM4000"}));
        }
        let idx = self.sessions.len();
        self.sessions.push(sess);
        self.active_4000.insert(bank, idx);
        idx
    }

    fn close_lcdm4000(&mut self, bank: i64) {
        if let Some(idx) = self.active_4000.remove(&bank) {
            self.sessions[idx].closed = true;
        }
    }

    fn find_last_active_lcdm2000(&self, serial_path: &Option<String>) -> Option<usize> {
        self.active_2000
            .values()
            .filter(|&&i| serial_path.is_none() || self.sessions[i].serial_path == *serial_path)
            .copied()
            .max_by_key(|&i| self.sessions[i].start_dt)
    }

    fn find_last_active_lcdm4000(&self, serial_path: &Option<String>) -> Option<usize> {
        self.active_4000
            .values()
            .filter(|&&i| serial_path.is_none() || self.sessions[i].serial_path == *serial_path)
            .copied()
            .max_by_key(|&i| self.sessions[i].start_dt)
    }

    pub fn process_line(&mut self, line: &str) {
        self.lines_processed += 1;
        if !line.contains("Dispense") && !line.contains("Command: Status") {
            return;
        }
        self.process_line_matched(line);
    }

    /// Разбор строки, уже прошедшей префильтр.
    fn process_line_matched(&mut self, line: &str) {
        let clean = strip_ansi(line);

        let Some(dt) = self.extract_dt(&clean) else {
            return;
        };
        // LCDM2000 и LCDM4000 — взаимоисключающие наборы шаблонов.
        // Раньше по каждой строке прогонялись все ~20 регексов подряд.
        let is_lcdm4000 = clean.contains("LCDM4000 CCTalk");
        let re = LowLevelRegexes::get();
        let raw200: String = clean.chars().take(200).collect();
        let serial_path = self.extract_serial_path(&clean);

        // ===== LCDM2000 =====
        if !is_lcdm4000 {
        if let Some(c) = re.handle_header.captures(&clean) {
            let req_id: i64 = c[1].parse().unwrap_or(0);
            let commands_count: i64 = c[2].parse().unwrap_or(0);
            let idx = self.get_or_create_lcdm2000(req_id, &clean);
            self.sessions[idx].add_event(
                "handleHeader",
                dt,
                raw200,
                map_from(vec![("commands_count", json!(commands_count))]),
            );
            self.lines_matched += 1;
            return;
        }

        if let Some(c) = re.handle_cmd.captures(&clean) {
            let req_id: i64 = c[1].parse().unwrap_or(0);
            let cmd_idx: i64 = c[2].parse().unwrap_or(0);
            let bank: i64 = c[3].parse().unwrap_or(0);
            let amount: i64 = c[4].parse().unwrap_or(0);
            let idx = self.get_or_create_lcdm2000(req_id, &clean);
            self.sessions[idx].commands.push(map_from(vec![
                ("cmdIndex", json!(cmd_idx)),
                ("bankIndex", json!(bank)),
                ("amount", json!(amount)),
            ]));
            self.sessions[idx].add_event(
                "handleCmd",
                dt,
                raw200,
                map_from(vec![
                    ("cmdIndex", json!(cmd_idx)),
                    ("bankIndex", json!(bank)),
                    ("amount", json!(amount)),
                ]),
            );
            self.lines_matched += 1;
            return;
        }

        if let Some(c) = re.worker_start.captures(&clean) {
            let req_id: i64 = c[1].parse().unwrap_or(0);
            let job_id: i64 = c[2].parse().unwrap_or(0);
            let idx = self.get_or_create_lcdm2000(req_id, &clean);
            self.sessions[idx].job_id = Some(job_id);
            self.sessions[idx].add_event(
                "workerStart",
                dt,
                raw200,
                map_from(vec![("jobID", json!(job_id))]),
            );
            self.lines_matched += 1;
            return;
        }

        if let Some(c) = re.start.captures(&clean) {
            let req_id: i64 = c[1].parse().unwrap_or(0);
            let job_id: i64 = c[2].parse().unwrap_or(0);
            let cmd_idx: i64 = c[3].parse().unwrap_or(0);
            let bank: i64 = c[4].parse().unwrap_or(0);
            let amount: i64 = c[5].parse().unwrap_or(0);
            let idx = self.get_or_create_lcdm2000(req_id, &clean);
            self.sessions[idx].job_id = Some(job_id);
            self.sessions[idx].add_event(
                "cmdStart",
                dt,
                raw200,
                map_from(vec![
                    ("cmdIndex", json!(cmd_idx)),
                    ("bankIndex", json!(bank)),
                    ("amount", json!(amount)),
                ]),
            );
            self.lines_matched += 1;
            return;
        }

        if let Some(c) = re.raw_hex.captures(&clean) {
            if let Some(idx) = self.find_last_active_lcdm2000(&serial_path) {
                self.sessions[idx].add_event(
                    "rawHex",
                    dt,
                    raw200,
                    map_from(vec![("hex", json!(c[1].to_string()))]),
                );
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.sensors.captures(&clean) {
            let up_chk: i64 = c[1].parse().unwrap_or(0);
            let up_exit: i64 = c[2].parse().unwrap_or(0);
            let lo_chk: i64 = c[3].parse().unwrap_or(0);
            let lo_exit: i64 = c[4].parse().unwrap_or(0);
            if let Some(idx) = self.find_last_active_lcdm2000(&serial_path) {
                self.sessions[idx].add_event(
                    "sensors",
                    dt,
                    raw200,
                    map_from(vec![
                        ("upper_chk", json!(up_chk)),
                        ("upper_exit", json!(up_exit)),
                        ("lower_chk", json!(lo_chk)),
                        ("lower_exit", json!(lo_exit)),
                    ]),
                );
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.status.captures(&clean) {
            let status_code = i64::from_str_radix(&c[1], 16).unwrap_or(0);
            let status_text = c[2].to_string();
            if let Some(idx) = self.find_last_active_lcdm2000(&serial_path) {
                let s = &mut self.sessions[idx];
                s.add_event(
                    "statusCode",
                    dt,
                    raw200,
                    map_from(vec![
                        ("status_code", json!(status_code)),
                        ("status_text", json!(status_text)),
                    ]),
                );
                if s.results.is_empty() || closed(s.results.last().unwrap()) {
                    s.results.push(map_from(vec![("closed", json!(false))]));
                }
                let r = s.results.last_mut().unwrap();
                r.insert("status_code".into(), json!(status_code));
                r.insert("status_text".into(), json!(status_text));
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.cmd_ok.captures(&clean) {
            let chk: i64 = c[1].parse().unwrap_or(0);
            let disp: i64 = c[2].parse().unwrap_or(0);
            let rej: i64 = c[3].parse().unwrap_or(0);
            if let Some(idx) = self.find_last_active_lcdm2000(&serial_path) {
                let s = &mut self.sessions[idx];
                if s.results.is_empty() || closed(s.results.last().unwrap()) {
                    s.results.push(map_from(vec![
                        ("closed", json!(false)),
                        ("chk_sensor", json!(0)),
                        ("dispensed", json!(0)),
                        ("rejected", json!(0)),
                    ]));
                }
                let r = s.results.last_mut().unwrap();
                let prev = |r: &JsonMap, k: &str| r.get(k).and_then(Value::as_i64).unwrap_or(0);
                let total_dispensed = prev(r, "dispensed") + disp;
                r.insert("chk_sensor".into(), json!(prev(r, "chk_sensor") + chk));
                r.insert("dispensed".into(), json!(total_dispensed));
                r.insert("rejected".into(), json!(prev(r, "rejected") + rej));
                s.add_event(
                    "cmdOk",
                    dt,
                    raw200,
                    map_from(vec![
                        ("chk", json!(chk)),
                        ("dispensed", json!(disp)),
                        ("rejected", json!(rej)),
                        ("total_dispensed", json!(total_dispensed)),
                    ]),
                );
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.cmd_err.captures(&clean) {
            let req_id: i64 = c[1].parse().unwrap_or(0);
            let cmd_idx: i64 = c[3].parse().unwrap_or(0);
            let bank: i64 = c[4].parse().unwrap_or(0);
            let amount: i64 = c[5].parse().unwrap_or(0);
            let chk: i64 = c[6].parse().unwrap_or(0);
            let disp: i64 = c[7].parse().unwrap_or(0);
            let rej: i64 = c[8].parse().unwrap_or(0);
            let idx = self.get_or_create_lcdm2000(req_id, &clean);
            let err_text = re.cmd_err_text.captures(&clean).map(|e| e[1].to_string());
            let s = &mut self.sessions[idx];
            if s.results.is_empty() || closed(s.results.last().unwrap()) {
                s.results.push(map_from(vec![("closed", json!(false))]));
            }
            {
                let r = s.results.last_mut().unwrap();
                r.insert("cmdIndex".into(), json!(cmd_idx));
                r.insert("bankIndex".into(), json!(bank));
                r.insert("requested".into(), json!(amount));
                r.insert("chk_sensor".into(), json!(chk));
                r.insert("dispensed".into(), json!(disp));
                r.insert("rejected".into(), json!(rej));
                r.insert(
                    "error".into(),
                    match &err_text {
                        Some(t) => json!(t),
                        None => Value::Null,
                    },
                );
                r.insert("closed".into(), json!(true));
            }
            s.add_event(
                "cmdErr",
                dt,
                raw200,
                map_from(vec![
                    ("cmdIndex", json!(cmd_idx)),
                    ("bankIndex", json!(bank)),
                    ("chk", json!(chk)),
                    ("dispensed", json!(disp)),
                    ("rejected", json!(rej)),
                    (
                        "error",
                        match &err_text {
                            Some(t) => json!(t),
                            None => Value::Null,
                        },
                    ),
                ]),
            );
            s.closed = true;
            self.active_2000.remove(&req_id);
            self.lines_matched += 1;
            return;
        }

        if let Some(c) = re.settled.captures(&clean) {
            let req_id: i64 = c[1].parse().unwrap_or(0);
            let cmd_idx: i64 = c[3].parse().unwrap_or(0);
            let bank: i64 = c[4].parse().unwrap_or(0);
            let amount: i64 = c[5].parse().unwrap_or(0);
            let attempts: i64 = c[6].parse().unwrap_or(0);
            if let Some(&idx) = self.active_2000.get(&req_id) {
                self.sessions[idx].add_event(
                    "settled",
                    dt,
                    raw200,
                    map_from(vec![
                        ("cmdIndex", json!(cmd_idx)),
                        ("bankIndex", json!(bank)),
                        ("amount", json!(amount)),
                        ("attempts", json!(attempts)),
                    ]),
                );
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.event_ok.captures(&clean) {
            let req_id: i64 = c[1].parse().unwrap_or(0);
            let cmd_idx: i64 = c[3].parse().unwrap_or(0);
            let bank: i64 = c[4].parse().unwrap_or(0);
            let requested: i64 = c[6].parse().unwrap_or(0);
            let chk: i64 = c[7].parse().unwrap_or(0);
            let disp: i64 = c[8].parse().unwrap_or(0);
            let rej: i64 = c[9].parse().unwrap_or(0);
            if let Some(&idx) = self.active_2000.get(&req_id) {
                let s = &mut self.sessions[idx];
                if s.results.is_empty() || closed(s.results.last().unwrap()) {
                    s.results.push(map_from(vec![("closed", json!(false))]));
                }
                {
                    let r = s.results.last_mut().unwrap();
                    r.insert("cmdIndex".into(), json!(cmd_idx));
                    r.insert("bankIndex".into(), json!(bank));
                    r.insert("requested".into(), json!(requested));
                    r.insert("chk_sensor".into(), json!(chk));
                    r.insert("dispensed".into(), json!(disp));
                    r.insert("rejected".into(), json!(rej));
                    r.insert("closed".into(), json!(true));
                }
                s.add_event(
                    "eventOk",
                    dt,
                    raw200,
                    map_from(vec![
                        ("cmdIndex", json!(cmd_idx)),
                        ("bankIndex", json!(bank)),
                        ("requested", json!(requested)),
                        ("chk", json!(chk)),
                        ("dispensed", json!(disp)),
                        ("rejected", json!(rej)),
                    ]),
                );
                let closed_cnt = s.results.iter().filter(|r| closed(r)).count();
                if closed_cnt >= s.commands.len() {
                    s.closed = true;
                    self.active_2000.remove(&req_id);
                }
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.job_done.captures(&clean) {
            let req_id: i64 = c[1].parse().unwrap_or(0);
            let job_id: i64 = c[2].parse().unwrap_or(0);
            if let Some(&idx) = self.active_2000.get(&req_id) {
                self.sessions[idx].add_event(
                    "jobDone",
                    dt,
                    raw200,
                    map_from(vec![("jobID", json!(job_id))]),
                );
                self.sessions[idx].closed = true;
                self.active_2000.remove(&req_id);
                self.lines_matched += 1;
            }
            return;
        }

        }  // конец веток LCDM2000

        // ===== LCDM4000 =====
        if is_lcdm4000 {
        if let Some(c) = re.lcdm4000_seq_start.captures(&clean) {
            let bank: i64 = c[1].parse().unwrap_or(0);
            let amount: i64 = c[2].parse().unwrap_or(0);
            let idx = self.get_or_create_lcdm4000(bank, &clean, None);
            let s = &mut self.sessions[idx];
            let cmd_index = s.commands.len() as i64;
            s.commands.push(map_from(vec![
                ("cmdIndex", json!(cmd_index)),
                ("bankIndex", json!(bank)),
                ("amount", json!(amount)),
            ]));
            s.add_event(
                "seqStart",
                dt,
                raw200,
                map_from(vec![("bankIndex", json!(bank)), ("amount", json!(amount))]),
            );
            self.lines_matched += 1;
            return;
        }

        if let Some(c) = re.lcdm4000_write_payload.captures(&clean) {
            if let Some(idx) = self.find_last_active_lcdm4000(&serial_path) {
                self.sessions[idx].add_event(
                    "writePayload",
                    dt,
                    raw200,
                    map_from(vec![("payload", json!(c[1].to_string()))]),
                );
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.lcdm4000_err.captures(&clean) {
            let device_code: i64 = c[1].parse().unwrap_or(0);
            let err_text = c[2].to_string();
            let idx_hint = re.lcdm4000_idx.captures(&clean).map(|m| m[1].to_string());
            if let Some(idx) = self.find_last_active_lcdm4000(&serial_path) {
                let s = &mut self.sessions[idx];
                if let Some(hint) = &idx_hint {
                    if s.idx.is_none() {
                        s.idx = Some(hint.clone());
                    }
                }
                if s.results.is_empty() || closed(s.results.last().unwrap()) {
                    s.results.push(map_from(vec![("closed", json!(false))]));
                }
                {
                    let r = s.results.last_mut().unwrap();
                    r.insert("device_code".into(), json!(device_code));
                    r.insert("error".into(), json!(err_text));
                }
                s.add_event(
                    "lcdm4000Err",
                    dt,
                    raw200,
                    map_from(vec![
                        ("device_code", json!(device_code)),
                        ("error", json!(err_text)),
                        (
                            "idx",
                            match &idx_hint {
                                Some(h) => json!(h),
                                None => Value::Null,
                            },
                        ),
                    ]),
                );
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.lcdm4000_note_result.captures(&clean) {
            let bank: i64 = c[1].parse().unwrap_or(0);
            let chk_total: i64 = c[2].parse().unwrap_or(0);
            let device_code: i64 = c[3].parse().unwrap_or(0);
            let desc = re
                .lcdm4000_note_desc
                .captures(&clean)
                .map(|m| m[1].to_string());
            let (mut disp_total, mut note_rej, mut note_n) = (0i64, 0i64, 0i64);
            if let Some(m) = re.lcdm4000_note_counts.captures(&clean) {
                disp_total = m[1].parse().unwrap_or(0);
                note_n = m[3].parse().unwrap_or(0);
                note_rej = m[4].parse().unwrap_or(0);
            }
            if let Some(&idx) = self.active_4000.get(&bank) {
                let s = &mut self.sessions[idx];
                if s.results.is_empty() || closed(s.results.last().unwrap()) {
                    s.results.push(map_from(vec![("closed", json!(false))]));
                }
                {
                    let r = s.results.last_mut().unwrap();
                    r.insert("bankIndex".into(), json!(bank));
                    r.insert("chk_sensor".into(), json!(chk_total));
                    r.insert("device_code".into(), json!(device_code));
                    r.insert(
                        "description".into(),
                        match &desc {
                            Some(d) => json!(d),
                            None => Value::Null,
                        },
                    );
                    r.insert("dispensed".into(), json!(disp_total));
                    r.insert("rejected".into(), json!(note_rej));
                    r.insert("requested".into(), json!(note_n));
                }
                s.add_event(
                    "noteResult",
                    dt,
                    raw200,
                    map_from(vec![
                        ("bankIndex", json!(bank)),
                        ("device_code", json!(device_code)),
                        (
                            "description",
                            match &desc {
                                Some(d) => json!(d),
                                None => Value::Null,
                            },
                        ),
                        ("dispensed", json!(disp_total)),
                        ("rejected", json!(note_rej)),
                    ]),
                );
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.lcdm4000_stop.captures(&clean) {
            let bank: i64 = c[1].parse().unwrap_or(0);
            let device_code: i64 = c[2].parse().unwrap_or(0);
            if let Some(&idx) = self.active_4000.get(&bank) {
                let s = &mut self.sessions[idx];
                if s.results.is_empty() || closed(s.results.last().unwrap()) {
                    s.results.push(map_from(vec![("closed", json!(false))]));
                }
                {
                    let r = s.results.last_mut().unwrap();
                    r.insert("device_code".into(), json!(device_code));
                    r.insert("stopped".into(), json!(true));
                    r.insert("closed".into(), json!(true));
                    if !r.get("error").map(|v| !v.is_null()).unwrap_or(false) {
                        let (desc, _) = classify_lcdm4000_code(device_code, None);
                        r.insert("error".into(), json!(desc));
                    }
                }
                s.add_event(
                    "lcdm4000Stop",
                    dt,
                    raw200,
                    map_from(vec![
                        ("bankIndex", json!(bank)),
                        ("device_code", json!(device_code)),
                    ]),
                );
                self.lines_matched += 1;
            }
            return;
        }

        if let Some(c) = re.lcdm4000_finished.captures(&clean) {
            let bank: i64 = c[1].parse().unwrap_or(0);
            let chk_total: i64 = c[2].parse().unwrap_or(0);
            let disp_total: i64 = c[3].parse().unwrap_or(0);
            let notes_amount: i64 = c[4].parse().unwrap_or(0);
            let rej_total: i64 = c[5].parse().unwrap_or(0);
            if let Some(&idx) = self.active_4000.get(&bank) {
                let s = &mut self.sessions[idx];
                if s.results.is_empty() || closed(s.results.last().unwrap()) {
                    s.results.push(map_from(vec![("closed", json!(false))]));
                }
                {
                    let r = s.results.last_mut().unwrap();
                    r.insert("bankIndex".into(), json!(bank));
                    r.insert("chk_sensor".into(), json!(chk_total));
                    r.insert("dispensed".into(), json!(disp_total));
                    r.insert("rejected".into(), json!(rej_total));
                    r.insert("requested".into(), json!(notes_amount));
                    r.insert("closed".into(), json!(true));
                }
                s.add_event(
                    "lcdm4000Finished",
                    dt,
                    raw200,
                    map_from(vec![
                        ("bankIndex", json!(bank)),
                        ("chk_sensor", json!(chk_total)),
                        ("dispensed", json!(disp_total)),
                        ("requested", json!(notes_amount)),
                        ("rejected", json!(rej_total)),
                    ]),
                );
                self.close_lcdm4000(bank);
                self.lines_matched += 1;
            }
            return;
        }

        // ===== Health LCDM4000 =====
        if let Some(c) = re.lcdm4000_status_sensors.captures(&clean) {
            let dvtl = c[1].to_string();
            let dvtr = c[2].to_string();
            let ejt = c[3].to_string();
            let exit_s = c[4].to_string();
            let rjt = c[5].to_string();
            let state = json!({
                "dt": dt.format("%Y-%m-%dT%H:%M:%S").to_string(),
                "DVTL": dvtl, "DVTR": dvtr, "EJT": ejt,
                "EXIT": exit_s, "RJT": rjt,
            });
            self.lcdm_health.push(("sensors".into(), state.clone()));
            let cur = (dvtl, dvtr, ejt, exit_s, rjt);
            if self.last_sensor_state.as_ref() != Some(&cur) {
                self.lcdm_health_changes.push(("sensors".into(), state));
                self.last_sensor_state = Some(cur);
            }
            self.lines_matched += 1;
            return;
        }

        if let Some(c) = re.lcdm4000_status_cassette.captures(&clean) {
            let num: i64 = c[1].parse().unwrap_or(0);
            let exists = c[2].to_string();
            let length: i64 = c[3].parse().unwrap_or(0);
            let opacity: i64 = c[4].parse().unwrap_or(0);
            let status: i64 = c[5].parse().unwrap_or(0);
            let state = json!({
                "dt": dt.format("%Y-%m-%dT%H:%M:%S").to_string(),
                "num": num, "exists": exists,
                "length": length, "opacity": opacity, "status": status,
            });
            self.lcdm_health.push(("cassette".into(), state.clone()));
            let cur = (exists, length, opacity, status);
            if self.last_cassette_state.get(&num) != Some(&cur) {
                self.lcdm_health_changes.push(("cassette".into(), state));
                self.last_cassette_state.insert(num, cur);
            }
            self.lines_matched += 1;
        }
        }  // конец веток LCDM4000
    }

    pub fn build_sessions(&mut self) {
        for s in self.sessions.iter_mut() {
            s.finalize_status();
        }
        self.sessions.sort_by_key(|s| s.start_dt);
    }

    fn compute_suspicion_flags(
        &self,
        fs: &W2WithdrawSession,
        ll: &W2LowLevelSession,
        prev_ll_end: Option<NaiveDateTime>,
    ) -> Vec<String> {
        let mut flags = Vec::new();
        if let (Some(fs_start), Some(ll_start)) = (fs.start_dt, ll.start_dt) {
            let gap = (ll_start - fs_start).num_milliseconds() as f64 / 1000.0;
            if gap.abs() > SUSPICION_START_GAP_SEC as f64 {
                let sign = if gap > 0.0 { '+' } else { '-' };
                flags.push(format!("gap {}{}s", sign, gap.abs() as i64));
            }
        }
        if let (Some(prev), Some(ll_start)) = (prev_ll_end, ll.start_dt) {
            let retry_gap = (ll_start - prev).num_milliseconds() as f64 / 1000.0;
            if retry_gap > SUSPICION_RETRY_GAP_SEC as f64 {
                flags.push(format!("retry pause {}s", retry_gap as i64));
            }
        }
        let bank_set = |cmds: &[Value]| -> Vec<i64> {
            let mut v: Vec<i64> = cmds
                .iter()
                .filter_map(|c| c.get("bankIndex").and_then(Value::as_i64))
                .collect();
            v.sort_unstable();
            v.dedup();
            v
        };
        let fe_banks = bank_set(&fs.commands);
        let ll_banks = {
            let cmds: Vec<Value> = ll
                .commands
                .iter()
                .map(|m| Value::Object(m.clone()))
                .collect();
            bank_set(&cmds)
        };
        if !fe_banks.is_empty() && !ll_banks.is_empty() {
            let diff: Vec<i64> = ll_banks
                .iter()
                .copied()
                .filter(|b| !fe_banks.contains(b))
                .collect();
            if !diff.is_empty() {
                let diff_str = diff
                    .iter()
                    .map(|b| b.to_string())
                    .collect::<Vec<_>>()
                    .join(",");
                let fe_str = format!("{:?}", fe_banks);
                flags.push(format!("другой банк (bank {} vs {})", diff_str, fe_str));
            }
        }
        let fe_total: i64 = fs
            .commands
            .iter()
            .filter_map(|c| c.get("notesAmount").and_then(Value::as_i64))
            .sum();
        let ll_disp = ll.total_dispensed();
        if fe_total > 0 && ll_disp > fe_total {
            flags.push(format!("выдано больше ({}>{})", ll_disp, fe_total));
        }
        flags
    }

    pub fn correlate_with_frontend(
        &mut self,
        frontend_sessions: &mut [W2WithdrawSession],
        start_window_before_sec: i64,
        end_window_after_sec: i64,
        next_session_buffer_sec: i64,
    ) {
        let mut order: Vec<usize> = frontend_sessions
            .iter()
            .enumerate()
            .filter(|(_, s)| s.start_dt.is_some())
            .map(|(i, _)| i)
            .collect();
        order.sort_by_key(|&i| frontend_sessions[i].start_dt);

        let effective_end = |fs: &W2WithdrawSession| -> Option<NaiveDateTime> {
            let relevant = ["dispensedEvent", "cannotContinue", "errorOccurs"];
            let mut candidates: Vec<NaiveDateTime> = fs
                .events
                .iter()
                .filter(|e| relevant.contains(&e.kind.as_str()))
                .map(|e| e.dt)
                .collect();
            candidates.sort();
            candidates.last().copied().or(fs.end_dt).or(fs.start_dt)
        };

        let mut used: Vec<usize> = Vec::new();
        for (pos, &i) in order.iter().enumerate() {
            let fs_start = frontend_sessions[i].start_dt.unwrap();
            let fe_banks: Vec<i64> = {
                let mut v: Vec<i64> = frontend_sessions[i]
                    .commands
                    .iter()
                    .filter_map(|c| c.get("bankIndex").and_then(Value::as_i64))
                    .collect();
                v.sort_unstable();
                v.dedup();
                v
            };
            let fe_device = frontend_sessions[i].dispenser_device_id.clone();

            let lower_bound = fs_start - chrono::Duration::seconds(start_window_before_sec);
            let eff_end = effective_end(&frontend_sessions[i]).unwrap_or(fs_start);
            let mut upper_bound = eff_end + chrono::Duration::seconds(end_window_after_sec);

            for &j in order.iter().skip(pos + 1) {
                let next_fs = &frontend_sessions[j];
                let same_device = fe_device.is_none()
                    || next_fs.dispenser_device_id.is_none()
                    || fe_device == next_fs.dispenser_device_id;
                if same_device {
                    if let Some(next_start) = next_fs.start_dt {
                        let hard_cap =
                            next_start - chrono::Duration::seconds(next_session_buffer_sec);
                        if hard_cap < upper_bound {
                            upper_bound = hard_cap;
                        }
                        break;
                    }
                }
            }
            if upper_bound < fs_start {
                continue;
            }

            let mut candidates: Vec<usize> = Vec::new();
            for (k, ll) in self.sessions.iter().enumerate() {
                if used.contains(&k) || ll.start_dt.is_none() {
                    continue;
                }
                let ll_start = ll.start_dt.unwrap();
                if ll_start < lower_bound || ll_start > upper_bound {
                    continue;
                }
                if !fe_banks.is_empty() {
                    let ll_banks: Vec<i64> = {
                        let mut v: Vec<i64> = ll
                            .commands
                            .iter()
                            .filter_map(|c| c.get("bankIndex").and_then(Value::as_i64))
                            .collect();
                        v.sort_unstable();
                        v.dedup();
                        v
                    };
                    if !ll_banks.is_empty() && !ll_banks.iter().any(|b| fe_banks.contains(b)) {
                        continue;
                    }
                }
                candidates.push(k);
            }
            candidates.sort_by_key(|&k| self.sessions[k].start_dt);

            let mut prev_ll_end: Option<NaiveDateTime> = None;
            for k in candidates {
                let flags = self.compute_suspicion_flags(
                    &frontend_sessions[i],
                    &self.sessions[k],
                    prev_ll_end,
                );
                frontend_sessions[i].lowlevel_sessions.push(k);
                frontend_sessions[i].suspicion_flags.push(flags);
                used.push(k);
                prev_ll_end = self.sessions[k].end_dt.or(self.sessions[k].start_dt);
            }
        }
    }

    /// Слияние результатов независимого разбора (для параллельного режима).
    pub fn merge(&mut self, mut other: W2LowLevelAnalyzer) {
        self.sessions.append(&mut other.sessions);
        self.lcdm_health.append(&mut other.lcdm_health);
        self.lcdm_health_changes.append(&mut other.lcdm_health_changes);
        for (k, v) in other.dispensers {
            self.dispensers.entry(k).or_insert(v);
        }
        self.lines_processed += other.lines_processed;
        self.lines_matched += other.lines_matched;
    }

    /// Параллельный разбор набора файлов: каждый файл — независимо,
    /// затем результаты сливаются. Порядок сессий восстанавливается
    /// сортировкой в build_sessions().
    pub fn parse_many(paths: &[std::path::PathBuf]) -> std::io::Result<Self> {
        use rayon::prelude::*;
        let parts: Vec<Self> = paths
            .par_iter()
            .map(|path| {
                let mut a = W2LowLevelAnalyzer::new();
                a.parse_file(path)?;
                Ok(a)
            })
            .collect::<std::io::Result<Vec<_>>>()?;

        let mut merged = W2LowLevelAnalyzer::new();
        for part in parts {
            merged.merge(part);
        }
        Ok(merged)
    }

    pub fn result(&self) -> LowLevelResult {
        LowLevelResult {
            sessions: self.sessions.clone(),
            lcdm_health: self.lcdm_health.clone(),
            lcdm_health_changes: self.lcdm_health_changes.clone(),
            dispensers: self.dispensers.clone(),
            lines_processed: self.lines_processed,
            lines_matched: self.lines_matched,
        }
    }
}
