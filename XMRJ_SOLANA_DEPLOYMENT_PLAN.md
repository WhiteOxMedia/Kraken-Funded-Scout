# XMRJ Solana Deployment — Final Version

This plan is a concrete implementation path for the requested task: deploy XMRJ (SPL),
create the treasury vault, mint initial supply, lock/delegate mint authority, deploy Treasury
and Jackpot programs, and wire the off-chain XMR conversion pipeline. It does NOT perform
any Kraken listing actions.

## Current environment assessment

- Node.js is available: node v24.18.0
- Solana CLI is NOT installed: solana not found
- SPL Token CLI is NOT installed: spl-token not found
- Rust toolchain is NOT installed: cargo not found
- Keypair not present: no wallet.json in the workspace
- No Solana RPC config set: no devnet/mainnet config in this environment

Because the tooling is missing, the deployment cannot be executed directly from this workspace yet.
The steps below are the exact recommended sequence, with the first part being environment preparation
rather than token/program deployment.

## Recommended deployment model

- Build/test first on Solana devnet.
- Use one dedicated deployer keypair for program deployment and initial mint.
- Lock or delegate mint authority away from a single hot wallet before any mainnet use.
- Keep the off-chain XMR conversion service separate from the on-chain programs and from any Kraken account actions.

## 1. Solana environment setup

### 1.1 Install toolchain

Option A — WSL (recommended by current Solana docs):

wsl --install
# restart if prompted, then in Ubuntu/WSL:
sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"
solana --version

Option B — Native Windows installer (run from Administrator Command Prompt):

cmd /c "curl https://release.anza.xyz/stable/agave-install-init-x86_64-pc-windows-msvc.exe --output C:\agave-install-tmp\agave-install-init.exe --create-dirs"
C:\agave-install-tmp\agave-install-init.exe stable
solana --version

After install, update later with:

agave-install update

If spl-token is not included in the installed bundle, the SPL Token CLI can be built from Rust:

cargo install spl-token-cli
spl-token --version

### 1.2 Configure wallet and keypair

Create a keypair for deployment/testing. Keep this file out of shared repos and do not commit it to version control.
Example for PowerShell with a native Windows CLI:

New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\solana"
solana-keygen new --outfile "$env:USERPROFILE\.config\solana\id.json"
solana config set --keypair "$env:USERPROFILE\.config\solana\id.json"

For WSL/Ubuntu, use the Unix home path instead:

solana-keygen new
solana config set --keypair "$HOME/.config/solana/id.json"

Verify active configuration:

solana config get

### 1.3 Select devnet and fund wallet

solana config set --url devnet
solana airdrop 2
solana balance

If airdrop requests are rate-limited or fail, use the Web Faucet instead of repeated CLI airdrop calls.
Devnet airdrops are limited per request.

### 1.4 Confirm tooling before moving on

Do not proceed to token creation until these commands succeed in the chosen environment:

solana --version
spl-token --version
cargo --version

## 2. Create the XMRJ SPL token

Token parameters:

- Name: XMR Jackpot Treasury Token
- Symbol: XMRJ
- Decimals: 9
- Standard: SPL

### 2.1 Create token

spl-token --url devnet create-token --decimals 9

Store the printed mint address as XMRJ_MINT.

### 2.2 Create treasury token account

spl-token --url devnet create-account <XMRJ_MINT>

Store the returned associated token account address as TREASURY_VAULT.

### 2.3 Mint initial supply

spl-token --url devnet mint <XMRJ_MINT> <AMOUNT> <TREASURY_VAULT>

This example mints into the treasury vault. The implementation must support:

- fixed supply, and/or
- governed emission, where mint authority is held by governance program/multisig

## 3. Lock or delegate mint authority

### 3.1 Fixed supply

Disable mint authority:

spl-token --url devnet authorize <XMRJ_MINT> mint --disable

### 3.2 Governed emission

Set mint authority to a governance program or multisig. Mint authority must never remain on a single hot wallet.

spl-token --url devnet authorize <XMRJ_MINT> mint <NEW_AUTHORITY_PUBLICKEY>

In governed mode, the Treasury program should hold/enforce minting through that authority rather than a lone hot wallet key.

## 4. Deploy Treasury program

The Treasury program is responsible for:

- holding XMRJ in the treasury vault conceptually/on-chain as applicable
- recording XMRJ inflows
- applying split ratios (reserve vs jackpot)
- emitting conversion instructions for off-chain XMR conversion
- sending jackpot funding instructions to the Jackpot program

Required interface (as specified):

- record_inflow_xmrj(amount_xmrj)
- set_split_ratio(reserve_bps, jackpot_bps) — admin-only
- convert_xmrj_to_xmr(amount_xmrj)
- fund_jackpot_with_xmr(amount_xmr)

Interface status (Oct 16 — stated rather than implied, so nothing here reads as
"done" when it is only specified):

- `initialize(reserve_bps, jackpot_bps, exchange_rate_bps)` — implemented, and
  REQUIRED first: the state account does not exist until it runs (4.3)
- `record_inflow_xmrj(amount_xmrj)` — implemented; it has no authority gate yet
  (anyone holding the state account can record an inflow — open item)
- `set_split_ratio` / `set_exchange_rate` — implemented, admin-gated; the admin
  is the governance authority PDA, so these are reached by an approved
  proposal through the governance CPI
- `convert_xmrj_to_xmr(amount_xmrj)` — NOT implemented: the conversion is
  off-chain (section 6), which is where that work belongs
- `fund_jackpot_with_xmr(amount_xmr)` — NOT implemented. This is the treasury →
  jackpot CPI that would make section 5's "callable only by Treasury" true; the
  jackpot's current check is not that guarantee (see 5.3)

### 4.1 Build

Build the program artifact for the target cluster/version before deploy:

cargo build-sbf --manifest-path treasury-program/Cargo.toml

### 4.2 Deploy

solana program deploy --url devnet /path/to/treasury_program.so

Store the deployed program ID as TREASURY_PROGRAM_ID.

### 4.3 Initialize (required — the state account does not exist until this runs)

Call `initialize(reserve_bps, jackpot_bps, exchange_rate_bps)` once, immediately
after the deploy, with:

- `treasury_state` — the PDA `[b"treasury_state"]` under the treasury program
- `admin_authority` — the governance authority PDA, `[b"governance_authority"]`
  derived under the GOVERNANCE program. The treasury derives and checks this
  itself, and the governance program id is pinned inside the program rather
  than supplied — so the authority cannot be pointed anywhere else.
- `payer` — the deployer, paying for the state account

`reserve_bps + jackpot_bps` must equal 10 000, or the instruction refuses. Every
parameter is re-settable afterwards by an approved governance proposal
(`set_split_ratio` / `set_exchange_rate`), so the deployer's starting values are
not final.

Run it in the same sitting as the deploy: the state PDA is a fixed seed, so
whichever transaction lands first initializes it. Gating `initialize` on the
program's upgrade authority is the mainnet hardening step — an open item
recorded in the crate sources and in 5.3, not a property to assume today.

## 5. Deploy Jackpot program

The Jackpot program is responsible for:

- tracking logical jackpot balance (XMR amount)
- accepting funding only from the Treasury program
- managing payout events
- providing jackpot state to the UI

Required interface (as specified):

- fund_jackpot(amount_xmr) — callable only by Treasury

### 5.1 Build

cargo build-sbf --manifest-path jackpot-program/Cargo.toml

### 5.2 Deploy

solana program deploy --url devnet /path/to/jackpot_program.so

Store the deployed program ID as JACKPOT_PROGRAM_ID.

### 5.3 Initialize (required — the state account does not exist until this runs)

Call `initialize()` — it takes NO parameters — once, immediately after the
deploy, with:

- `jackpot_state` — the PDA `[b"jackpot_state"]` under the jackpot program
- `admin_authority` — the governance authority PDA, derived and checked by the
  jackpot program itself under the same pinned governance program id
- `payer` — the deployer

It pins the funder (`treasury_program` = the treasury program id, a constant in
the program) and the authority, and starts the payout cap UNCAPPED (10 000 bps).
Governance can lower that cap later through an approved proposal
(`set_payout_rules`). Because the instruction accepts nothing, a first-caller
race can only ever produce the correct configuration.

Note the honest limit of section 5's list: "accepting funding only from the
Treasury program" is NOT yet enforced. `fund_jackpot`'s check compares the
`caller` account's KEY to `treasury_program`, so a caller can pass the treasury
program's address without the treasury program being involved. Enforcing it needs
a treasury funder PDA plus the treasury → jackpot CPI from section 4's open item;
until then, treat the jackpot balance as a logical figure, not a guarded vault.

## 6. Off-chain XMR conversion pipeline

Define and integrate a separate off-chain microservice that:

- listens for Treasury conversion events
- converts XMRJ value to real XMR using the current exchange rate
- sends XMR to Kraken or the Monero jackpot wallet as applicable
- updates jackpot liquidity state

Exchange rate logic:

xmr_amount = xmrj_amount * exchange_rate

Admin-only controls:

- set_exchange_rate
- set_split_ratio

This service should be scoped, logged, and access-controlled. It is not a Kraken listing action and should
not perform any listing-related steps.

## 7. Application integration

Integrate into the application layer:

- XMRJ mint address
- Treasury program ID
- Jackpot program ID
- off-chain conversion service endpoint/config
- Scout Pro unlock logic (no token spending)

Scout unlock rules:

- Mining unlock (100 days) -> no XMRJ movement
- Paid unlock -> XMRJ inflow only
- No treasury outflow
- No token sinks
- No inflation

## 8. Mainnet deployment (when ready)

When ready to move off devnet:

solana config set --url https://api.mainnet-beta.solana.com

Then repeat, with mainnet-appropriate funding and authority controls:

- create XMRJ mint
- create treasury vault
- deploy Treasury program
- deploy Jackpot program
- mint initial supply
- lock or delegate mint authority

No Kraken listing actions should be performed at this time.

## Implementation notes / constraints

- This plan assumes the above tooling is installed in the deployment environment before any Solana commands are run.
- Do not commit keypairs or sensitive RPC/auth material to the repo.
- Test token creation, account creation, minting, and authority changes on devnet first.
- Confirm program security properties (admin gates, mint authority handling, Treasury->Jackpot authorization)
  before mainnet deployment.

## Status

Not started in this environment. Tooling installation is the first required step.
