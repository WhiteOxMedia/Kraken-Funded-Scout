//! XMRJ -> XMR Off-Chain Conversion Microservice
//!
//! Responsibilities:
//! - Listen for Treasury conversion events
//! - Convert XMRJ value -> real XMR
//! - Send XMR to Kraken or jackpot wallet
//! - Update jackpot liquidity state
//! - Log all conversions
//!
//! Components:
//! - Solana event listener
//! - Monero RPC client
//! - Kraken deposit handler
//! - Exchange rate module
//! - Conversion queue
//! - Audit log system
//!
//! Security:
//! - Verify Treasury Program signature on conversion instructions
//! - Reject user-triggered conversions
//! - Maintain full audit logs
//! - Support multisig admin

// This crate is OFF-CHAIN: it needs exactly two things from anchor-lang, the
// `msg!` macro and the ProgramError type its security helpers return, so it
// names them instead of glob-importing the prelude. That glob is what broke
// this crate's first `cargo check`: `anchor_lang::prelude` re-exports
// `Result<T> = std::result::Result<T, anchor_lang::Error>`, so every
// std-style `Result<(), String>` / `Result<(), ProgramError>` below became
// "type alias takes 1 generic argument but 2 generic arguments were supplied"
// — 18 errors from one import. The pins are untouched; only the import is.
use anchor_lang::solana_program::msg;
use anchor_lang::solana_program::program_error::ProgramError;
use std::collections::HashMap;
use std::sync::Arc;

// ---------------------------------------------------------------------------
// Log-only config placeholders
// ---------------------------------------------------------------------------

/// Exchange rate representation: XMR per XMRJ unit, stored as basis points
/// to avoid floating point in shared state.
///
/// ```text
/// xmr_amount = xmrj_amount * exchange_rate
/// ```
// `text`, not a bare fence, on purpose: a bare fence is a DOCTEST, and these
// are pseudocode — `cargo test` tried to compile them as Rust and failed
// with "cannot find value `xmr_amount` in this scope" until it was told.
pub const EXCHANGE_RATE_BPS_DEFAULT: u64 = 0;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExchangeRate {
    pub bps: u64,
}

impl ExchangeRate {
    pub fn new(bps: u64) -> Self {
        Self { bps }
    }

    /// Takes `&self`, not `self`: callers hold the rate behind a shared
    /// reference (`self.rate.as_f64()` in ExchangeRateModule), and a by-value
    /// receiver made that "cannot move out of `self.rate`".
    pub fn as_f64(&self) -> f64 {
        self.bps as f64 / 10_000.0
    }
}

// ---------------------------------------------------------------------------
// Conversion record (audit trail)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct ConversionAuditEntry {
    pub id: u64,
    pub requested_by_treasury_ix_index: u64,
    pub xmrj_amount: u64,
    pub exchange_rate_bps: u64,
    pub xmr_amount: f64,
    pub destination: Destination,
    pub status: ConversionStatus,
    pub timestamp_utc: i64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Destination {
    Kraken,
    JackpotWallet,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ConversionStatus {
    Queued,
    InProgress,
    SentToMoneroRpc,
    SentToKraken,
    UpdatedJackpot,
    Failed(String),
}

// ---------------------------------------------------------------------------
// Conversion queue
// ---------------------------------------------------------------------------

pub struct ConversionQueue {
    next_id: u64,
    pending: HashMap<u64, ConversionAuditEntry>,
}

impl ConversionQueue {
    pub fn new() -> Self {
        Self {
            next_id: 1,
            pending: HashMap::new(),
        }
    }

    pub fn enqueue(
        &mut self,
        xmrj_amount: u64,
        exchange_rate_bps: u64,
    ) -> u64 {
        let id = self.next_id;
        self.next_id += 1;

        let xmr_amount = xmrj_amount as f64 * exchange_rate_bps as f64 / 10_000.0;

        let entry = ConversionAuditEntry {
            id,
            requested_by_treasury_ix_index: 0,
            xmrj_amount,
            exchange_rate_bps,
            xmr_amount,
            destination: Destination::JackpotWallet,
            status: ConversionStatus::Queued,
            timestamp_utc: 0,
        };

        self.pending.insert(id, entry);
        id
    }

    pub fn get(&self, id: u64) -> Option<&ConversionAuditEntry> {
        self.pending.get(&id)
    }

    pub fn mark_status(&mut self, id: u64, status: ConversionStatus) {
        if let Some(entry) = self.pending.get_mut(&id) {
            entry.status = status;
        }
    }
}

// ---------------------------------------------------------------------------
// Exchange-rate module
// ---------------------------------------------------------------------------

pub struct ExchangeRateModule {
    rate: ExchangeRate,
}

impl ExchangeRateModule {
    pub fn new(rate_bps: u64) -> Self {
        Self {
            rate: ExchangeRate::new(rate_bps),
        }
    }

    pub fn current_rate(&self) -> ExchangeRate {
        self.rate.clone()
    }

    pub fn set_rate(&mut self, bps: u64) {
        self.rate = ExchangeRate::new(bps);
    }

    pub fn compute_xmr(&self, xmrj_amount: u64) -> f64 {
        xmrj_amount as f64 * self.rate.as_f64()
    }
}

// ---------------------------------------------------------------------------
// HTTP endpoint shapes (spec)
// ---------------------------------------------------------------------------

/// POST /convert
/// Body:
/// - xmrj_amount
/// - destination
///
/// Requires Treasury Program signature verification.
/// Rejects user-triggered conversions.
pub struct ConvertRequest {
    pub xmrj_amount: u64,
    pub destination: Destination,
}

pub struct ConvertResponse {
    pub conversion_id: u64,
    pub xmr_amount: f64,
    pub status: String,
}

/// POST /send-to-kraken
/// Body:
/// - conversion_id
/// - kraken deposit account / withdraw target ref
///
/// Requires admin authorization.
pub struct SendToKrakenRequest {
    pub conversion_id: u64,
    pub kraken_target_ref: String,
}

pub struct SendToKrakenResponse {
    pub conversion_id: u64,
    pub status: String,
}

/// POST /update-jackpot
/// Body:
/// - conversion_id
/// - jackpot_program address
/// - amount_xmr
///
/// Updates jackpot liquidity state after conversion.
pub struct UpdateJackpotRequest {
    pub conversion_id: u64,
    pub jackpot_program_id: String,
    pub amount_xmr: f64,
}

pub struct UpdateJackpotResponse {
    pub conversion_id: u64,
    pub jackpot_updated: bool,
}

/// GET /exchange-rate
pub struct ExchangeRateResponse {
    pub exchange_rate_bps: u64,
    pub exchange_rate_decimals: f64,
}

// ---------------------------------------------------------------------------
// Event listener (conceptual)
// ---------------------------------------------------------------------------

/// Treasury emits conversion instructions; this listener ingests them.
pub struct TreasuryEventPoller {
    // In a real implementation this would subscribe to Solana logs/accounts
    // emitted by the Treasury program's convert_xmrj_to_xmr flow.
    pub running: bool,
}

impl TreasuryEventPoller {
    pub fn new() -> Self {
        Self { running: false }
    }

    pub fn start(&mut self) {
        self.running = true;
        msg!("TreasuryEventPoller started");
    }

    pub fn stop(&mut self) {
        self.running = false;
        msg!("TreasuryEventPoller stopped");
    }
}

// ---------------------------------------------------------------------------
// Monero RPC client (interface)
// ---------------------------------------------------------------------------

pub struct MoneroRpcClient {
    // Placeholder for RPC endpoint + auth config.
    // Actual implementation must never log private keys or seed material.
    endpoint: String,
}

impl MoneroRpcClient {
    pub fn new(endpoint: String) -> Self {
        Self { endpoint }
    }

    pub fn send_xmr(&self, _dest: &str, _amount: f64) -> Result<(), String> {
        msg!("MoneroRpcClient::send_xmr called");
        Err("Monero RPC client is a spec placeholder".into())
    }
}

// ---------------------------------------------------------------------------|
// Kraken deposit handler (interface)
// ---------------------------------------------------------------------------

pub struct KrakenDepositHandler {
    // Placeholder. Production use requires secured admin credentials stored
    // out-of-band, never in source control.
    configured: bool,
}

impl KrakenDepositHandler {
    pub fn new() -> Self {
        Self { configured: false }
    }

    pub fn configure(&mut self) {
        self.configured = true;
    }

    pub fn send_to_kraken(&self, _amount_xmr: f64) -> Result<(), String> {
        if !self.configured {
            return Err("Kraken handler not configured".into());
        }
        msg!("KrakenDepositHandler::send_to_kraken called");
        Err("Kraken deposit handler is a spec placeholder".into())
    }
}

// ---------------------------------------------------------------------------
// Audit log system
// ---------------------------------------------------------------------------

pub struct AuditLog {
    entries: Vec<ConversionAuditEntry>,
}

impl AuditLog {
    pub fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }

    pub fn append(&mut self, entry: ConversionAuditEntry) {
        self.entries.push(entry);
    }

    pub fn all(&self) -> &[ConversionAuditEntry] {
        &self.entries
    }
}

// ---------------------------------------------------------------------------
// Conversion pipeline orchestrator
// ---------------------------------------------------------------------------

pub struct ConversionPipeline {
    pub queue: ConversionQueue,
    pub exchange_rate: ExchangeRateModule,
    pub audit: AuditLog,
    pub poller: TreasuryEventPoller,
    pub monero_client: MoneroRpcClient,
    pub kraken_handler: KrakenDepositHandler,
}

impl ConversionPipeline {
    pub fn new(rate_bps: u64, monero_endpoint: String) -> Self {
        Self {
            queue: ConversionQueue::new(),
            exchange_rate: ExchangeRateModule::new(rate_bps),
            audit: AuditLog::new(),
            poller: TreasuryEventPoller::new(),
            monero_client: MoneroRpcClient::new(monero_endpoint),
            kraken_handler: KrakenDepositHandler::new(),
        }
    }

    /// Enqueue a conversion requested by Treasury.
    /// Admin-only controls should gate rate/split changes elsewhere.
    pub fn convert(&mut self, xmrj_amount: u64, destination: Destination) -> u64 {
        let rate = self.exchange_rate.current_rate();
        let id = self.queue.enqueue(xmrj_amount, rate.bps);
        self.queue.mark_status(id, ConversionStatus::Queued);
        id
    }

    pub fn send_to_kraken(&mut self, conversion_id: u64) -> Result<(), String> {
        // Read the amount OUT of the queue before mutating its status: holding
        // the `get` borrow across `mark_status` is a second borrow of the same
        // field (E0502), and cloning the one number is cheaper than a RefCell.
        let Some(entry) = self.queue.get(conversion_id) else {
            return Err("unknown conversion id".into());
        };
        let xmr_amount = entry.xmr_amount;
        self.queue.mark_status(conversion_id, ConversionStatus::SentToKraken);
        self.kraken_handler.send_to_kraken(xmr_amount)
    }

    pub fn update_jackpot(&mut self, conversion_id: u64) -> Result<(), String> {
        if self.queue.get(conversion_id).is_none() {
            return Err("unknown conversion id".into());
        }
        self.queue
            .mark_status(conversion_id, ConversionStatus::UpdatedJackpot);
        msg!(
            "Update jackpot liquidity state for conversion {}",
            conversion_id
        );
        Ok(())
    }

    pub fn current_exchange_rate_response(&self) -> ExchangeRateResponse {
        let rate = self.exchange_rate.current_rate();
        ExchangeRateResponse {
            exchange_rate_bps: rate.bps,
            exchange_rate_decimals: rate.as_f64(),
        }
    }
}

// ---------------------------------------------------------------------------
// Admin-only control hooks (capability spec)
// ---------------------------------------------------------------------------

pub struct AdminControls {
    // Admin-gated operations: set_exchange_rate, set_split_ratio, etc.
    // These must be protected by multisig or governance verification.
    configured: bool,
}

impl AdminControls {
    pub fn new() -> Self {
        Self { configured: false }
    }

    pub fn verify_admin(&self) -> Result<(), &'static str> {
        if self.configured {
            Ok(())
        } else {
            Err("admin control path not configured")
        }
    }

    pub fn set_exchange_rate(&mut self, _rate_bps: u64) -> Result<(), &'static str> {
        self.verify_admin()?;
        Ok(())
    }

    pub fn set_split_ratio(&mut self, _reserve_bps: u64, _jackpot_bps: u64) -> Result<(), &'static str> {
        self.verify_admin()?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Security helpers (spec)
// ---------------------------------------------------------------------------

/// Verify that a conversion instruction was submitted by the Treasury Program.
/// Reject any user-triggered conversion.
pub fn verify_treasury_program_signature(_signature: &[u8]) -> Result<(), ProgramError> {
    msg!("verify_treasury_program_signature called");
    Err(ProgramError::InvalidArgument)
}

/// Reject user-triggered conversions at the entrypoint layer.
pub fn reject_user_conversion() -> Result<(), ProgramError> {
    msg!("conversion rejected: not from Treasury Program");
    Err(ProgramError::Custom(0))
}

// ---------------------------------------------------------------------------
// Example conversion logic (matching spec)
// ---------------------------------------------------------------------------

/// ```text
/// xmr_amount = xmrj_amount * exchange_rate
/// ```
// (The fence is `text` for the same reason as at the top of this file: this is
// the formula, not a Rust example doctests should try to compile.)
pub fn compute_xmr(xmrj_amount: u64, exchange_rate: f64) -> f64 {
    xmrj_amount as f64 * exchange_rate
}

// ---------------------------------------------------------------------------
// Module tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exchange_rate_bps_to_decimal() {
        let rate = ExchangeRate::new(1250);
        assert!((rate.as_f64() - 0.125).abs() < 1e-9);
    }

    #[test]
    fn compute_xmr_basic() {
        let xmr = compute_xmr(1_000_000, 0.5);
        assert!((xmr - 500_000.0).abs() < 1e-6);
    }

    #[test]
    fn conversion_queue_enqueue_and_lookup() {
        let mut queue = ConversionQueue::new();
        let id = queue.enqueue(1_000_000, 10_000);
        let entry = queue.get(id).expect("enqueued entry should exist");
        assert_eq!(entry.xmrj_amount, 1_000_000);
        assert_eq!(entry.exchange_rate_bps, 10_000);
        assert!((entry.xmr_amount - 1_000_000.0).abs() < 1e-6);
    }

    #[test]
    fn conversion_pipeline_exchange_rate_read() {
        let pipeline = ConversionPipeline::new(1250, "http://127.0.0.1:18081".into());
        let resp = pipeline.current_exchange_rate_response();
        assert_eq!(resp.exchange_rate_bps, 1250);
        assert!((resp.exchange_rate_decimals - 0.125).abs() < 1e-9);
    }

    #[test]
    fn admin_controls_reject_before_config() {
        let mut admin = AdminControls::new();
        assert!(admin.set_exchange_rate(100).is_err());
        assert!(admin.set_split_ratio(5000, 5000).is_err());
    }

    #[test]
    fn audit_log_appends_and_replays() {
        let mut audit = AuditLog::new();
        let entry = ConversionAuditEntry {
            id: 1,
            requested_by_treasury_ix_index: 0,
            xmrj_amount: 1_000,
            exchange_rate_bps: 5_000,
            xmr_amount: 0.5,
            destination: Destination::Kraken,
            status: ConversionStatus::Queued,
            timestamp_utc: 0,
        };
        audit.append(entry.clone());
        assert_eq!(audit.all().len(), 1);
        assert_eq!(audit.all()[0].id, 1);
    }
}
