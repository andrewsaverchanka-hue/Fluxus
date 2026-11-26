//! Helpers (port of `core/utils.py`).

use std::borrow::Cow;
use std::collections::HashSet;
use std::fs::File;
use std::io::{BufRead, BufReader, Read};
use std::path::Path;
use std::sync::OnceLock;

use flate2::read::MultiGzDecoder;
use regex::Regex;

use crate::constants::lcdm_status_codes;

fn ansi_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\x1b\[[0-9;]*m").unwrap())
}

/// Remove ANSI colour escapes. Returns a borrowed slice when there is nothing
/// to strip, which is the common case and avoids an allocation per line.
pub fn strip_ansi(text: &str) -> Cow<'_, str> {
    if !text.contains('\x1b') {
        return Cow::Borrowed(text);
    }
    ansi_re().replace_all(text, "")
}

/// Classify a dispenser error string into a `FAILED_*` category.
pub fn classify_dispense_error(status_detail: Option<&str>) -> &'static str {
    let Some(sd) = status_detail else {
        return "FAILED_HARDWARE";
    };
    let sd = sd.to_lowercase();
    if sd.contains("jam") {
        "FAILED_JAM"
    } else if sd.contains("bill-end") || sd.contains("bill end") || sd.contains("empty") {
        "FAILED_EMPTY"
    } else if sd.contains("timeout") {
        "FAILED_TIMEOUT"
    } else {
        "FAILED_HARDWARE"
    }
}

/// Look up an LCDM2000 status code -> (description, category).
pub fn classify_lcdm_status(code: u8) -> (String, &'static str) {
    match lcdm_status_codes().get(&code) {
        Some((desc, cat)) => ((*desc).to_string(), *cat),
        None => (format!("Unknown 0x{:02X}", code), "FAILED_HARDWARE"),
    }
}

/// LCDM4000 device codes -> (description, category). Port of
/// `classify_lcdm4000_code`.
pub fn classify_lcdm4000_code(
    device_code: i64,
    description: Option<&str>,
) -> (String, &'static str) {
    match device_code {
        0 => ("No error".to_string(), "OK"),
        1 => ("Bill Pick Up Error".to_string(), "FAILED_HARDWARE"),
        _ => match description {
            Some(d) => (d.to_string(), classify_dispense_error(Some(d))),
            None => (format!("Unknown code {}", device_code), "FAILED_HARDWARE"),
        },
    }
}

/// Open a plain or gzip-compressed log file.
pub fn open_log_reader(path: &Path) -> std::io::Result<Box<dyn Read>> {
    let file = File::open(path)?;
    let name = path.to_string_lossy().to_lowercase();
    if name.ends_with(".gz") {
        Ok(Box::new(MultiGzDecoder::new(file)))
    } else {
        Ok(Box::new(file))
    }
}

/// Iterate over lines the way Python's text mode does: `\n` separated,
/// `\r\n` normalised, invalid UTF-8 replaced (`errors='ignore'` is close
/// enough; lossy replacement only differs on already-broken bytes).
pub fn for_each_line<F>(path: &Path, mut f: F) -> std::io::Result<()>
where
    F: FnMut(&str),
{
    let reader = open_log_reader(path)?;
    let mut buf = BufReader::with_capacity(1024 * 1024, reader);
    let mut line: Vec<u8> = Vec::with_capacity(512);
    let mut last_ended_with_newline = false;
    let mut saw_any = false;
    loop {
        line.clear();
        let n = buf.read_until(b'\n', &mut line)?;
        if n == 0 {
            break;
        }
        saw_any = true;
        last_ended_with_newline = line.last() == Some(&b'\n');
        while matches!(line.last(), Some(b'\n') | Some(b'\r')) {
            line.pop();
        }
        let text = String::from_utf8_lossy(&line);
        f(&text);
    }
    // The Python readers always flush their remainder buffer through
    // `_process_chunk`, so a file ending in a newline (or an empty file)
    // yields one extra empty line. Reproduce it so `lines_processed` matches.
    if !saw_any || last_ended_with_newline {
        f("");
    }
    Ok(())
}

/// Как `for_each_line`, но отдаёт строки как байты.
///
/// Нужен «тяжёлым» парсерам: 80 % строк в реальных логах — шум, и проверять
/// в них UTF-8 незачем. Строка превращается в текст только если прошла
/// байтовый префильтр.
pub fn for_each_line_bytes<F>(path: &Path, mut f: F) -> std::io::Result<()>
where
    F: FnMut(&[u8]),
{
    let reader = open_log_reader(path)?;
    let mut buf = BufReader::with_capacity(1024 * 1024, reader);
    let mut line: Vec<u8> = Vec::with_capacity(512);
    let mut last_ended_with_newline = false;
    let mut saw_any = false;
    loop {
        line.clear();
        let n = buf.read_until(b'\n', &mut line)?;
        if n == 0 {
            break;
        }
        saw_any = true;
        last_ended_with_newline = line.last() == Some(&b'\n');
        while matches!(line.last(), Some(b'\n') | Some(b'\r')) {
            line.pop();
        }
        f(&line);
    }
    if !saw_any || last_ended_with_newline {
        f(b"");
    }
    Ok(())
}

/// Detect the log flavour from the first 8 KB (port of `detect_log_type`).
pub fn detect_log_type(path: &Path) -> &'static str {
    static FE: OnceLock<Regex> = OnceLock::new();
    static DEV: OnceLock<Regex> = OnceLock::new();
    let fe = FE.get_or_init(|| {
        Regex::new(r"\[\d{1,2}/\d{1,2}/\d{4},\s*\d{1,2}:\d{2}:\d{2}\s*(AM|PM)\]").unwrap()
    });
    let dev = DEV.get_or_init(|| Regex::new(r"\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+DBG").unwrap());

    let Ok(mut reader) = open_log_reader(path) else {
        return "unknown";
    };
    let mut raw = Vec::new();
    let mut chunk = [0u8; 8192];
    if let Ok(n) = reader.read(&mut chunk) {
        raw.extend_from_slice(&chunk[..n]);
    }
    let head = String::from_utf8_lossy(&raw);
    let head = strip_ansi(&head);
    if fe.is_match(&head) {
        return "frontend";
    }
    if dev.is_match(&head) {
        return "devices";
    }
    if head.contains("DevicesServer") || head.contains("LCDM") || head.contains("serialPath=") {
        return "devices";
    }
    "unknown"
}

/// Make an arbitrary string safe for use as a file name (port of
/// `safe_filename`). `\w` in Python is Unicode-aware, so we keep alphanumerics
/// plus `_` and `-`.
pub fn safe_filename(value: &str, max_length: usize) -> String {
    if value.is_empty() {
        return "unknown".to_string();
    }
    let mut out = String::with_capacity(value.len());
    for ch in value.chars() {
        if ch.is_alphanumeric() || ch == '_' || ch == '-' {
            out.push(ch);
        } else {
            out.push('_');
        }
    }
    let trimmed = out.trim_matches('_').to_string();
    // collapse runs of underscores
    let mut collapsed = String::with_capacity(trimmed.len());
    let mut prev_underscore = false;
    for ch in trimmed.chars() {
        if ch == '_' {
            if prev_underscore {
                continue;
            }
            prev_underscore = true;
        } else {
            prev_underscore = false;
        }
        collapsed.push(ch);
    }
    let result: String = collapsed.chars().take(max_length).collect();
    if result.is_empty() {
        "unknown".to_string()
    } else {
        result
    }
}

/// Truncate by characters, matching Python's `s[:n]` slicing.
pub fn truncate_chars(s: &str, n: usize) -> String {
    if s.len() <= n {
        return s.to_string();
    }
    if s.is_ascii() {
        return s[..n].to_string();
    }
    s.chars().take(n).collect()
}

/// Dedupe helper used by collection detection.
pub fn note_map_eq(a: &[(i64, i64)], b: &[(i64, i64)]) -> bool {
    let sa: HashSet<_> = a.iter().collect();
    let sb: HashSet<_> = b.iter().collect();
    sa == sb
}
