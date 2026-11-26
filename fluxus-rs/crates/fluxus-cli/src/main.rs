//! `fluxus-parse` — CLI front end for the Rust core.
//!
//! Parse:
//!   fluxus-parse <log> [--kind lima|frontend|lowlevel] [--pretty] [--bench]
//!
//! Save an analysis bundle (all three logs, correlated):
//!   fluxus-parse --save out.anl.gz --lima a.log --frontend b.log --lowlevel c.log
//!
//! Dump a saved bundle (re-serialized from the loaded state):
//!   fluxus-parse --load in.anl.gz [--pretty]

use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Instant;

use fluxus_core::serializer;
use fluxus_core::{LogAnalyzer, W2FrontendAnalyzer, W2LowLevelAnalyzer};

const USAGE: &str = "usage:
  fluxus-parse <log> [--kind lima|frontend|lowlevel] [--pretty] [--bench]
  fluxus-parse --save out.anl.gz --lima a.log --frontend b.log --lowlevel c.log
  fluxus-parse --load in.anl.gz [--pretty]";

fn print_json(v: &serde_json::Value, pretty: bool) -> ExitCode {
    let out = if pretty {
        serde_json::to_string_pretty(v)
    } else {
        serde_json::to_string(v)
    };
    match out {
        Ok(s) => {
            println!("{}", s);
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("error: {}", e);
            ExitCode::from(1)
        }
    }
}

fn main() -> ExitCode {
    let mut path: Option<PathBuf> = None;
    let mut pretty = false;
    let mut bench = false;
    let mut kind = String::from("lima");
    let mut save: Option<PathBuf> = None;
    let mut load: Option<PathBuf> = None;
    let mut lima_path: Option<PathBuf> = None;
    let mut front_path: Option<PathBuf> = None;
    let mut low_path: Option<PathBuf> = None;

    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let take = |a: &mut std::iter::Skip<std::env::Args>| a.next().map(PathBuf::from);
        match arg.as_str() {
            "--kind" => kind = args.next().unwrap_or_else(|| "lima".into()),
            "--pretty" => pretty = true,
            "--bench" => bench = true,
            "--save" => save = take(&mut args),
            "--load" => load = take(&mut args),
            "--lima" => lima_path = take(&mut args),
            "--frontend" => front_path = take(&mut args),
            "--lowlevel" => low_path = take(&mut args),
            "-h" | "--help" => {
                println!("{}", USAGE);
                return ExitCode::SUCCESS;
            }
            other => path = Some(PathBuf::from(other)),
        }
    }

    // ---- load + re-serialize ----
    if let Some(file) = load {
        return match serializer::load(&file) {
            Ok(l) => {
                let v =
                    serializer::bundle_value(&l.lima, &l.frontend, &l.lowlevel, &l.source_files);
                print_json(&v, pretty)
            }
            Err(e) => {
                eprintln!("error: cannot load {}: {}", file.display(), e);
                ExitCode::from(1)
            }
        };
    }

    // ---- save a bundle from three logs ----
    if let Some(out) = save {
        let (Some(lp), Some(fp), Some(llp)) = (lima_path, front_path, low_path) else {
            eprintln!("error: --save requires --lima, --frontend and --lowlevel");
            return ExitCode::from(2);
        };
        let mut lima = LogAnalyzer::new();
        let mut fe = W2FrontendAnalyzer::new();
        let mut ll = W2LowLevelAnalyzer::new();
        for (p, res) in [
            (lp.as_path(), lima.parse_file(&lp)),
            (fp.as_path(), fe.parse_file(&fp)),
            (llp.as_path(), ll.parse_file(&llp)),
        ] {
            if let Err(e) = res {
                eprintln!("error: cannot read {}: {}", p.display(), e);
                return ExitCode::from(1);
            }
        }
        lima.build_sessions();
        ll.build_sessions();
        fe.build_sessions();
        ll.correlate_with_frontend(&mut fe.sessions, 10, 60, 2);
        let sources = vec![
            lp.display().to_string(),
            fp.display().to_string(),
            llp.display().to_string(),
        ];
        return match serializer::save(&out, &lima, &fe, &ll, &sources) {
            Ok(()) => {
                println!("saved {}", out.display());
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("error: cannot write {}: {}", out.display(), e);
                ExitCode::from(1)
            }
        };
    }

    // ---- single-log parse ----
    let Some(path) = path else {
        eprintln!("{}", USAGE);
        return ExitCode::from(2);
    };

    let started = Instant::now();
    let parsed = match kind.as_str() {
        "lima" => {
            let mut a = LogAnalyzer::new();
            if let Err(e) = a.parse_file(&path) {
                return fail(&path, &e);
            }
            let parse_ms = started.elapsed().as_millis();
            let t = Instant::now();
            a.build_sessions();
            let sessions_ms = t.elapsed().as_millis();
            (
                serde_json::to_string(&a.result()),
                a.lines_found(),
                a.sessions.len(),
                parse_ms,
                sessions_ms,
            )
        }
        "frontend" => {
            let mut a = W2FrontendAnalyzer::new();
            if let Err(e) = a.parse_file(&path) {
                return fail(&path, &e);
            }
            let parse_ms = started.elapsed().as_millis();
            let t = Instant::now();
            a.build_sessions();
            let sessions_ms = t.elapsed().as_millis();
            (
                serde_json::to_string(&a.result()),
                a.lines_matched,
                a.sessions.len(),
                parse_ms,
                sessions_ms,
            )
        }
        "lowlevel" => {
            let mut a = W2LowLevelAnalyzer::new();
            if let Err(e) = a.parse_file(&path) {
                return fail(&path, &e);
            }
            let parse_ms = started.elapsed().as_millis();
            let t = Instant::now();
            a.build_sessions();
            let sessions_ms = t.elapsed().as_millis();
            (
                serde_json::to_string(&a.result()),
                a.lines_matched,
                a.sessions.len(),
                parse_ms,
                sessions_ms,
            )
        }
        other => {
            eprintln!("unknown --kind {:?}\n{}", other, USAGE);
            return ExitCode::from(2);
        }
    };

    let (json, matched, sessions, parse_ms, sessions_ms) = parsed;
    if bench {
        println!(
            "{} kind={} lines_matched={} sessions={} elapsed_ms={} parse_ms={} sessions_ms={}",
            path.display(),
            kind,
            matched,
            sessions,
            started.elapsed().as_millis(),
            parse_ms,
            sessions_ms
        );
        return ExitCode::SUCCESS;
    }

    match json {
        Ok(s) => {
            if pretty {
                match serde_json::from_str::<serde_json::Value>(&s) {
                    Ok(v) => print_json(&v, true),
                    Err(e) => {
                        eprintln!("error: {}", e);
                        ExitCode::from(1)
                    }
                }
            } else {
                println!("{}", s);
                ExitCode::SUCCESS
            }
        }
        Err(e) => {
            eprintln!("error: cannot serialize result: {}", e);
            ExitCode::from(1)
        }
    }
}

fn fail(path: &PathBuf, e: &std::io::Error) -> ExitCode {
    eprintln!("error: cannot read {}: {}", path.display(), e);
    ExitCode::from(1)
}
