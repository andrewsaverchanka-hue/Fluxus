//! Fluxus core: parsing of terminal logs (Lima / W2), ported from Python.
//!
//! Parity with the Python implementation is the primary goal; speed is the
//! reason for the port. Every module here is a mechanical translation of the
//! corresponding `parsers/` + `core/` module, and the differential tests in
//! `tools/compare.py` compare its JSON output against the Python reference.

pub mod constants;
pub mod lima;
pub mod models;
pub mod serializer;
pub mod utils;
pub mod w2_frontend;
pub mod w2_lowlevel;
pub mod w2_models;

pub use lima::{LimaResult, LogAnalyzer};
pub use w2_frontend::{W2FrontendAnalyzer, W2FrontendResult};
pub use w2_lowlevel::{LowLevelResult, W2LowLevelAnalyzer};
pub use w2_models::{W2CashEvent, W2Event, W2LowLevelSession, W2WithdrawSession};
