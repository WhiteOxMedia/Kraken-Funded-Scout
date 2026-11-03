# Operator runbook — the xmrig-proxy API

Who talks to the proxy, how each of them resolves its two knobs, and
where an operator overrides. One reader for every chain:
`pool/operator_config.py` (enforced by `test_operator_config_contract.py`).

## 1. The call sites

| Caller | Endpoint | Cadence |
| --- | --- | --- |
| `pool/proxy_poller.py` | `/1/miners` + `/1/workers` | every 10 s, own process |
| `pool/jackpot_engine.py` (`_get_real_proxy_miners`) | `/2/summary` | on demand (real-miner count) |
| `pool/server.py` payment-scanner thread (`poll_proxy_workers`) | `/2/summary` | every 60 s |

The server also registers identities from the proxy's **log file**
(`monero/proxy.log`) with no API call at all — see §3.

## 2. The two knobs, resolved in ONE order

**Base URL** — `operator_config.proxy_api_base()`:
`XMRJ_PROXY_API` env > loopback default `http://127.0.0.1:9090`.
A trailing slash is stripped, so callers concatenate `/2/summary` safely.

**Bearer token** — `operator_config.proxy_token()`; first non-empty
tier wins:

1. `XMRJ_PROXY_API_TOKEN` env — the deployment secret (a fresh boot
   supplies it).
2. `monero/proxy_api_token.txt` — the operator's token file
   (skip-the-restart tier on the running box).
3. `monero/proxy_config.json` `http.access-token` — the value
   xmrig-proxy itself enforces; must always EQUAL the tier in use.
4. db setting `PROXY_API_TOKEN` — legacy, nothing writes it anymore.

Empty means unset: the caller sends no Authorization header, the proxy
refuses, and the poller prints it. Never guess a token.

**Override = set the env, or drop the rotated secret into the token
file; then make `proxy_config.json` match**, and old `xmrjackpot2026`
stays retired. Verify from the operator box:

```
curl -H "Authorization: Bearer <token>" http://127.0.0.1:9090/1/miners
```

HTTP 401/403 means the env/file tier and the proxy's own config have
drifted apart; re-run the resolution table above tier by tier.

**Propagation, precisely:**
- the poller and the engine resolve the token per call — a rotation
  lands on the next cycle with no restart;
- the poller resolves its base URL at process start — restart the
  poller after moving the proxy's port;
- the server's scanner thread copies both knobs when the scanning
  thread starts — restart the pool server for it to see a rotation.

## 3. How a rig becomes a pool user

A miner's identity is whatever its xmrig presents in the user field.
The miner sends `user` = `wallet_address.worker_name` at stratum login
(the shipped configs use the wallet as `user` and no `rig-id`; a
customer's own rig decides its own worker suffix).

- **API path (authoritative):** `proxy_poller.parse_workers()` reads
  `/1/miners` (self-describing `format` columns), takes that `user`
  string raw, and partitions it on the FIRST dot into
  `wallet_address` + `worker_name`. Rows with no user are skipped.
  Share counters and hashrate come from `/1/workers` keyed by
  `rig_id`, falling back to the worker name for rigs whose rig_id is
  the worker name.
- **Log path (identity + freshness only):** the server's scanner regex
  (`[48][0-9A-Za-z]{90,120}` + optional `.worker`) on
  `monero/proxy.log` creates the miner row and bumps `last_seen` —
  it never touches share counts.

Counters are LIFETIME values; the poller rebases per-poll deltas
against the DB, so a proxy restart (counters drop to 0) is a new zero
point, not lost shares. Duplicate rigs under the same wallet converge
on one DB row per (wallet, worker) pair.

**Where the operator overrides identity:**
- a rig's user label: that rig's xmrig config `pools[].user`
  (`wallet.worker`) and `pools[].rig-id` — nothing on the server side
  edits it;
- the deployment's OWN miner (miner bot): `XMRJ_MINER_WALLET` env,
  read only by `operator_config.miner_wallet()` — unset means
  `miner.py` refuses to mine and says so;
- installs ship a customer-neutral placeholder wallet in the payload
  config until activation; registration rewrites only
  `pools[0].user` and leaves every other key alone.

## 4. Quick diagnosis

- **"Proxy not responding"** in the poller: wrong `XMRJ_PROXY_API`,
  proxy down, or port changed without a poller restart (§2).
- **401/403 shape**: tier mismatch — proxy config vs env/token file.
- **A miner missing from the pool**: no `user` field at login, or
  wallet outside the 4-started 90–120-char shape the API/log readers
  accept. Fix the rig's `user`, not the pool.
- **Shares "reset"** after a proxy restart: rebase is expected — check
  the next accepted delta, not the stored totals.
