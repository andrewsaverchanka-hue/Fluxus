//! PyO3 bindings: expose the Rust core to the existing PySide6 application.

use std::path::Path;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::PyRefMut;

use fluxus_core::{LogAnalyzer, W2FrontendAnalyzer, W2LowLevelAnalyzer};

/// Parse a Lima/devices_server log (plain or .gz) and return the same JSON
/// document `fluxus-parse` produces.
///
/// Returning JSON instead of a Python dict keeps the GIL held for one
/// allocation instead of building ~100k Python objects per session tree.
/// `json.loads` in CPython is fast (C scanner), so this is the cheaper bridge.
#[pyfunction]
#[pyo3(signature = (path, include_events=false))]
fn parse_lima_json(path: &str, include_events: bool) -> PyResult<String> {
    let mut analyzer = LogAnalyzer::new();
    analyzer
        .parse_file(Path::new(path))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    analyzer.build_sessions();
    if !include_events {
        // The session list is the bulk of the payload; the summary UI never
        // reads per-session events. Dropping them shrinks the JSON ~6x.
        for s in analyzer.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&analyzer.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Same as `parse_lima_json` but takes the log contents directly (for
/// streaming/remote logs that are already in memory).
#[pyfunction]
#[pyo3(signature = (content, include_events=false))]
fn parse_lima_json_str(content: &str, include_events: bool) -> PyResult<String> {
    let mut analyzer = LogAnalyzer::new();
    analyzer.parse_str(content);
    analyzer.build_sessions();
    if !include_events {
        for s in analyzer.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&analyzer.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Parse a skills-battle-frontend log (W2 frontend).
#[pyfunction]
#[pyo3(signature = (path, include_events=true))]
fn parse_frontend_json(path: &str, include_events: bool) -> PyResult<String> {
    let mut a = W2FrontendAnalyzer::new();
    a.parse_file(Path::new(path))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    a.build_sessions();
    if !include_events {
        for s in a.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&a.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Parse a devices_server log for LCDM dispenser activity (W2 low level).
#[pyfunction]
#[pyo3(signature = (path, include_events=true))]
fn parse_lowlevel_json(path: &str, include_events: bool) -> PyResult<String> {
    let mut a = W2LowLevelAnalyzer::new();
    a.parse_file(Path::new(path))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    a.build_sessions();
    if !include_events {
        for s in a.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&a.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Parse frontend + low-level logs and correlate them (the GUI's main path).
#[pyfunction]
#[pyo3(signature = (frontend_path, lowlevel_path, include_events=true))]
fn parse_w2_correlated(
    frontend_path: &str,
    lowlevel_path: &str,
    include_events: bool,
) -> PyResult<String> {
    let mut fe = W2FrontendAnalyzer::new();
    fe.parse_file(Path::new(frontend_path))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", frontend_path, e)))?;
    let mut ll = W2LowLevelAnalyzer::new();
    ll.parse_file(Path::new(lowlevel_path))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", lowlevel_path, e)))?;
    ll.build_sessions();
    fe.build_sessions();
    ll.correlate_with_frontend(&mut fe.sessions, 10, 60, 2);
    if !include_events {
        for s in fe.sessions.iter_mut() {
            s.events.clear();
        }
        for s in ll.sessions.iter_mut() {
            s.events.clear();
        }
    }
    let out = serde_json::json!({
        "frontend": fe.result(),
        "lowlevel": ll.result(),
    });
    serde_json::to_string(&out)
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Parse three logs, correlate, and write an `.anl.gz` bundle.
#[pyfunction]
#[pyo3(signature = (out, lima, frontend, lowlevel, source_files=None))]
fn save_bundle(
    out: &str,
    lima: &str,
    frontend: &str,
    lowlevel: &str,
    source_files: Option<Vec<String>>,
) -> PyResult<()> {
    let mut a = LogAnalyzer::new();
    a.parse_file(Path::new(lima))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", lima, e)))?;
    let mut fe = W2FrontendAnalyzer::new();
    fe.parse_file(Path::new(frontend))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", frontend, e)))?;
    let mut ll = W2LowLevelAnalyzer::new();
    ll.parse_file(Path::new(lowlevel))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", lowlevel, e)))?;
    a.build_sessions();
    ll.build_sessions();
    fe.build_sessions();
    ll.correlate_with_frontend(&mut fe.sessions, 10, 60, 2);
    let sources = source_files
        .unwrap_or_else(|| vec![lima.to_string(), frontend.to_string(), lowlevel.to_string()]);
    fluxus_core::serializer::save(Path::new(out), &a, &fe, &ll, &sources)
        .map_err(|e| PyRuntimeError::new_err(format!("cannot write {}: {}", out, e)))
}

/// Load an `.anl.gz` bundle and return parse-result-shaped JSON:
/// `{"lima": ..., "frontend": ..., "lowlevel": ..., "metadata": {...}}`.
/// Shaped this way so the Python adapter can reuse its normal constructors.
#[pyfunction]
fn load_bundle_json(path: &str) -> PyResult<String> {
    let l = fluxus_core::serializer::load(Path::new(path))
        .map_err(|e| PyRuntimeError::new_err(format!("cannot load {}: {}", path, e)))?;
    let out = serde_json::json!({
        "lima": l.lima.result(),
        "frontend": l.frontend.result(),
        "lowlevel": l.lowlevel.result(),
        "metadata": {
            "version": l.version,
            "saved_at": l.saved_at,
            "source_files": l.source_files,
        },
    });
    serde_json::to_string(&out)
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Parse many Lima logs into one analyzer (the GUI calls parse per file and
/// expects accumulation, like the Python analyzer does).
#[pyfunction]
#[pyo3(signature = (paths, include_events=false))]
fn parse_lima_files_json(paths: Vec<String>, include_events: bool) -> PyResult<String> {
    let mut a = LogAnalyzer::new();
    for path in &paths {
        a.parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    }
    a.build_sessions();
    if !include_events {
        for s in a.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&a.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

#[pyfunction]
#[pyo3(signature = (paths, include_events=true))]
fn parse_frontend_files_json(paths: Vec<String>, include_events: bool) -> PyResult<String> {
    let mut a = W2FrontendAnalyzer::new();
    for path in &paths {
        a.parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    }
    a.build_sessions();
    if !include_events {
        for s in a.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&a.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

#[pyfunction]
#[pyo3(signature = (paths, include_events=true))]
fn parse_lowlevel_files_json(paths: Vec<String>, include_events: bool) -> PyResult<String> {
    let mut a = W2LowLevelAnalyzer::new();
    for path in &paths {
        a.parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    }
    a.build_sessions();
    if !include_events {
        for s in a.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&a.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

#[pyfunction]
#[pyo3(signature = (frontend_paths, lowlevel_paths, include_events=true))]
fn parse_w2_correlated_files(
    frontend_paths: Vec<String>,
    lowlevel_paths: Vec<String>,
    include_events: bool,
) -> PyResult<String> {
    let mut fe = W2FrontendAnalyzer::new();
    for path in &frontend_paths {
        fe.parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    }
    let mut ll = W2LowLevelAnalyzer::new();
    for path in &lowlevel_paths {
        ll.parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    }
    ll.build_sessions();
    fe.build_sessions();
    ll.correlate_with_frontend(&mut fe.sessions, 10, 60, 2);
    if !include_events {
        for s in fe.sessions.iter_mut() {
            s.events.clear();
        }
        for s in ll.sessions.iter_mut() {
            s.events.clear();
        }
    }
    let out = serde_json::json!({
        "frontend": fe.result(),
        "lowlevel": ll.result(),
    });
    serde_json::to_string(&out)
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Save a bundle built from *all* files of each kind.
#[pyfunction]
#[pyo3(signature = (out, lima_paths, frontend_paths, lowlevel_paths, source_files=None))]
fn save_bundle_files(
    out: &str,
    lima_paths: Vec<String>,
    frontend_paths: Vec<String>,
    lowlevel_paths: Vec<String>,
    source_files: Option<Vec<String>>,
) -> PyResult<()> {
    let mut a = LogAnalyzer::new();
    for path in &lima_paths {
        a.parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    }
    let mut fe = W2FrontendAnalyzer::new();
    for path in &frontend_paths {
        fe.parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    }
    let mut ll = W2LowLevelAnalyzer::new();
    for path in &lowlevel_paths {
        ll.parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))?;
    }
    a.build_sessions();
    ll.build_sessions();
    fe.build_sessions();
    ll.correlate_with_frontend(&mut fe.sessions, 10, 60, 2);
    let mut sources = source_files.unwrap_or_default();
    if sources.is_empty() {
        sources.extend(lima_paths.iter().cloned());
        sources.extend(frontend_paths.iter().cloned());
        sources.extend(lowlevel_paths.iter().cloned());
    }
    fluxus_core::serializer::save(Path::new(out), &a, &fe, &ll, &sources)
        .map_err(|e| PyRuntimeError::new_err(format!("cannot write {}: {}", out, e)))
}

// ---------------------------------------------------------------------------
// Постоянные парсеры: файлы добавляются по одному, разбор идёт сразу.
// Это даёт честный прогресс в GUI (а не «всё в конце»).
// ---------------------------------------------------------------------------

#[pyclass]
pub struct LimaParser {
    inner: LogAnalyzer,
}

#[pymethods]
impl LimaParser {
    #[new]
    fn new() -> Self {
        LimaParser { inner: LogAnalyzer::new() }
    }

    fn add_file(&mut self, path: &str) -> PyResult<()> {
        self.inner
            .parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))
    }

    /// Параллельный разбор набора файлов в этот же парсер.
    fn add_files_parallel(&mut self, paths: Vec<String>) -> PyResult<()> {
        let p: Vec<std::path::PathBuf> = paths.iter().map(std::path::PathBuf::from).collect();
        let parsed = LogAnalyzer::parse_many(&p)
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read logs: {}", e)))?;
        self.inner.merge(parsed);
        Ok(())
    }

    fn result_json(&mut self, include_events: bool) -> PyResult<String> {
        self.inner.build_sessions();
        if !include_events {
            for s in self.inner.sessions.iter_mut() {
                s.events.clear();
            }
        }
        serde_json::to_string(&self.inner.result())
            .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
    }
}

#[pyclass]
pub struct FrontendParser {
    inner: W2FrontendAnalyzer,
}

#[pymethods]
impl FrontendParser {
    #[new]
    fn new() -> Self {
        FrontendParser { inner: W2FrontendAnalyzer::new() }
    }

    fn add_file(&mut self, path: &str) -> PyResult<()> {
        self.inner
            .parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))
    }

    fn result_json(&mut self, include_events: bool) -> PyResult<String> {
        self.inner.build_sessions();
        if !include_events {
            for s in self.inner.sessions.iter_mut() {
                s.events.clear();
            }
        }
        serde_json::to_string(&self.inner.result())
            .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
    }
}

#[pyclass]
pub struct LowLevelParser {
    inner: W2LowLevelAnalyzer,
}

#[pymethods]
impl LowLevelParser {
    #[new]
    fn new() -> Self {
        LowLevelParser { inner: W2LowLevelAnalyzer::new() }
    }

    fn add_file(&mut self, path: &str) -> PyResult<()> {
        self.inner
            .parse_file(Path::new(path))
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read {}: {}", path, e)))
    }

    /// Параллельный разбор набора файлов в этот же парсер.
    fn add_files_parallel(&mut self, paths: Vec<String>) -> PyResult<()> {
        let p: Vec<std::path::PathBuf> = paths.iter().map(std::path::PathBuf::from).collect();
        let parsed = W2LowLevelAnalyzer::parse_many(&p)
            .map_err(|e| PyRuntimeError::new_err(format!("cannot read logs: {}", e)))?;
        self.inner.merge(parsed);
        Ok(())
    }

    fn result_json(&mut self, include_events: bool) -> PyResult<String> {
        self.inner.build_sessions();
        if !include_events {
            for s in self.inner.sessions.iter_mut() {
                s.events.clear();
            }
        }
        serde_json::to_string(&self.inner.result())
            .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
    }
}

/// Корреляция уже разобранных данных — без повторного чтения файлов.
#[pyfunction]
#[pyo3(signature = (frontend, lowlevel, include_events=true))]
fn correlate_parsers(
    mut frontend: PyRefMut<'_, FrontendParser>,
    mut lowlevel: PyRefMut<'_, LowLevelParser>,
    include_events: bool,
) -> PyResult<String> {
    lowlevel.inner.build_sessions();
    frontend.inner.build_sessions();
    lowlevel
        .inner
        .correlate_with_frontend(&mut frontend.inner.sessions, 10, 60, 2);
    if !include_events {
        for s in frontend.inner.sessions.iter_mut() {
            s.events.clear();
        }
        for s in lowlevel.inner.sessions.iter_mut() {
            s.events.clear();
        }
    }
    let out = serde_json::json!({
        "frontend": frontend.inner.result(),
        "lowlevel": lowlevel.inner.result(),
    });
    serde_json::to_string(&out)
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Параллельный разбор набора Lima-логов (rayon).
#[pyfunction]
#[pyo3(signature = (paths, include_events=true))]
fn parse_lima_many(paths: Vec<String>, include_events: bool) -> PyResult<String> {
    let p: Vec<std::path::PathBuf> = paths.iter().map(std::path::PathBuf::from).collect();
    let mut a = LogAnalyzer::parse_many(&p)
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read logs: {}", e)))?;
    a.build_sessions();
    if !include_events {
        for s in a.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&a.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

/// Параллельный разбор набора low-level логов (rayon).
#[pyfunction]
#[pyo3(signature = (paths, include_events=true))]
fn parse_lowlevel_many(paths: Vec<String>, include_events: bool) -> PyResult<String> {
    let p: Vec<std::path::PathBuf> = paths.iter().map(std::path::PathBuf::from).collect();
    let mut a = W2LowLevelAnalyzer::parse_many(&p)
        .map_err(|e| PyRuntimeError::new_err(format!("cannot read logs: {}", e)))?;
    a.build_sessions();
    if !include_events {
        for s in a.sessions.iter_mut() {
            s.events.clear();
        }
    }
    serde_json::to_string(&a.result())
        .map_err(|e| PyRuntimeError::new_err(format!("serialize failed: {}", e)))
}

#[pymodule]
fn fluxus_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_lima_json, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lima_json_str, m)?)?;
    m.add_function(wrap_pyfunction!(parse_frontend_json, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lowlevel_json, m)?)?;
    m.add_function(wrap_pyfunction!(parse_w2_correlated, m)?)?;
    m.add_function(wrap_pyfunction!(save_bundle, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lima_files_json, m)?)?;
    m.add_function(wrap_pyfunction!(parse_frontend_files_json, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lowlevel_files_json, m)?)?;
    m.add_function(wrap_pyfunction!(parse_w2_correlated_files, m)?)?;
    m.add_function(wrap_pyfunction!(save_bundle_files, m)?)?;
    m.add_function(wrap_pyfunction!(correlate_parsers, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lima_many, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lowlevel_many, m)?)?;
    m.add_class::<LimaParser>()?;
    m.add_class::<FrontendParser>()?;
    m.add_class::<LowLevelParser>()?;
    m.add_function(wrap_pyfunction!(load_bundle_json, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
