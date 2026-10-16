# XMRJ → XMR Off-Chain Conversion Microservice

This module is a spec/placeholder implementation for the requested off-chain conversion service.

## Status in this workspace

Code files present:
- `src/lib.rs`
- `README.md`

This crate is written as a Rust/Anchor-style spec module in this repo. **It is not a compiled or running service here.**  
The required HTTP endpoints (`POST /convert`, `POST /send-to-kraken`, `POST /update-jackpot`, `GET /exchange-rate`) are defined only as request/response shapes in `src/lib.rs`. There is no live server in this workspace.

## Responsibilities

- Listen for Treasury conversion events
- Convert XMRJ value → real XMR
- Send XMR to Kraken or jackpot wallet
- Update jackpot liquidity state
- Log all conversions

## Required components

- Solana event listener
- Monero RPC client
- Kraken deposit handler
- Exchange rate module
- Conversion queue
- Audit log system

## Conversion logic

```
xmr_amount = xmrj_amount * exchange_rate
```

## Required endpoints

- `POST /convert`
- `POST /send-to-kraken`
- `POST /update-jackpot`
- `GET  /exchange-rate`

## Security requirements

- Verify Treasury Program signature on conversion instructions
- Reject user-triggered conversions
- Maintain full audit logs
- Support multisig admin

## Build/runtime gaps

To make this a real service, you would still need:
- a Rust workspace/Cargo setup that can compile these modules
- an HTTP runtime for the listed endpoints
- Solana event ingestion integration
- real Monero RPC and Kraken integration, secured and access-controlled
- persistent/immutable audit storage

## Production notes

Production implementation must:
- never store private keys, seed material, or platform credentials in source control
- protect admin controls behind multisig or governance verification
- log conversions in an immutable, searchable audit system
