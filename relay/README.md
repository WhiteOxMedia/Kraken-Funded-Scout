# Relay VPS for `mine.xmrjackpot.com:3333`

The pool's stratum port cannot sit behind Cloudflare. Free/Pro plans proxy HTTP(S)
only, Spectrum for arbitrary TCP is an Enterprise add-on, and Cloudflare Tunnel's
TCP mode needs `cloudflared` on every miner's machine. So `mine` must be a
grey-cloud (DNS-only) record pointing at a real address — today the house
connection, `74.214.242.54`.

This kit moves that public address to a cheap VPS and drives the pool PC into the
relay over WireGuard, so the house IP leaves DNS and the router's port forward can
be shut:

```
miner --TCP 3333--> VPS (haproxy) --wg0--> 10.66.66.2:3333 (pool PC) --> p2pool
```

On this pool PC port 3333 belongs to `xmrig-proxy`, which forwards to p2pool, so
the relay's backend is the proxy rather than p2pool directly. Nothing in this kit
changes: haproxy dials whatever listens on `10.66.66.2:3333`, and it must keep
listening on `0.0.0.0`, not `127.0.0.1`, or the tunnelled connection is refused.

Nothing client-side changes: `mine.xmrjackpot.com:3333` stays the address in the
bundle installer, the xmrig config generator, the site templates and the README.

**The kit is provider-agnostic.** Any Ubuntu 22.04/24.04 VPS with a public IPv4
and ~512 MB RAM is enough; stratum is a handful of kilobytes per share, so even a
few dozen miners stay far under 1 GB/month. One researched option is
[Kamatera](https://index.trygravity.com/go/eaf83d93-27f3-4da5-892c-cf0022f64d5a)
(month-by-month, smallest instance, 30-day trial).

## Deployment record (2026-10-24)

**Live since 2026-10-24 18:52 UTC: `mine.xmrjackpot.com` -> `155.94.157.66`, grey cloud, verified from Cloudflare's own nameservers.**

RackNerd LA DC03, `155.94.157.66`, Ubuntu 26.04.1 LTS, 1 vCPU / 453 MB.
`bootstrap-relay.sh` ran clean: `wg-quick@wg0` and `haproxy` active, haproxy bound
to `0.0.0.0:3333`, `wg0` on `udp/51820`, peer `hjBpSbWX…` (`10.66.66.2`) registered.
The 26.04 minimal image ships **no `ufw`**, so the script's firewall step reported
"ufw not installed" and the box briefly had no host firewall at all; `ufw` was
installed afterwards with exactly `22/tcp`, `3333/tcp`, `51820/udp` open.

First proofs, run from the pool PC over the interim SSH tunnel before WireGuard
was installed (the WireGuard-only proofs are further down):

| check | result |
|---|---|
| `verify-stratum.py 127.0.0.1 3333` | PASS — pool and proxy healthy locally |
| `verify-stratum.py 155.94.157.66 3333` | **PASS** — `status='OK'`, real job in 260–400 ms |
| `verify-stratum.py 155.94.157.66 3333 --external` | **PASS** — 4/4 nodes connected (bg1, hk1, nl1, sg1) |
| `dig +short mine.xmrjackpot.com` | `155.94.157.66` after the cutover (was `74.214.242.54`) |

### Cutover (2026-10-24 18:52 UTC)

The `mine` A record was changed through the Cloudflare API from `74.214.242.54` to
`155.94.157.66`, `proxied=false`, TTL auto — the record id was
`3eaa6a6c607d4d9e32894c83e2f78593`, zone `0f3371bee638441b6d63ccedec05cfaa`. The
edit script lives at `.relay/bin/cf-set-mine.py` (token from `CF_API_TOKEN` or
`.relay/cf.token`; it forces the grey cloud because Cloudflare cannot proxy raw
TCP).

One wrinkle worth remembering: for about a minute after the API call, a direct
query to `dimitris`/`poppy.ns.cloudflare.com` still returned the old address while
`1.1.1.1`, `8.8.8.8` and `9.9.9.9` already served the new one. Cloudflare's edge
propagation is fast but not literally instantaneous — do not "fix" a record that
still reads stale from one authority seconds after an edit.

Post-cutover checks, all from the pool PC:

| check | result |
|---|---|
| `dimitris` / `poppy.ns.cloudflare.com` (authoritative) | `155.94.157.66` on both |
| `1.1.1.1`, `8.8.8.8`, `9.9.9.9` | `155.94.157.66` on all three |
| `verify-stratum.py mine.xmrjackpot.com 3333` | **PASS** — `status='OK'`, job in 275 ms |
| `verify-stratum.py mine.xmrjackpot.com 3333 --external` | **PASS** — 4/4 (es2, nl1, ro1, vn1) |
| apex `xmrjackpot.com` | unchanged Cloudflare anycast (`104.21.14.46`, `172.67.157.189`) — house IP gone |

The `ro1` node that timed out twice before the cutover connected in 0.24 s here, so
those earlier failures were that node's transient state, not the relay.

### The link is WireGuard; the SSH tunnel is retired

WireGuard for Windows 1.1.1 was installed on the pool PC and the peer config the
bootstrap printed is registered as the service `WireGuardTunnel$wg-home` with
`StartType Automatic`, so the tunnel comes up at boot before anyone logs in.
Inbound `3333/tcp` is opened only for the tunnel address:

```powershell
New-NetFirewallRule -DisplayName "p2pool stratum via relay" `
  -Direction Inbound -Protocol TCP -LocalPort 3333 -RemoteAddress 10.66.66.1 -Action Allow
```

| check | result |
|---|---|
| haproxy backend | `server pool-pc 10.66.66.2:3333 check inter 10s fall 3 rise 2` |
| `wg show wg0` on the VPS | peer `hjBpSbWX…`, `latest handshake`, KiB in both directions |
| `verify-stratum.py 155.94.157.66 3333` | **PASS** — 250–261 ms |
| `verify-stratum.py 155.94.157.66 3333 --external` | **PASS** — 4/4 nodes (in, jp, pl, us) |

The interim SSH reverse tunnel (`relay-tunnel.bat`) is stopped: the supervisor loop
and its ssh child are killed, the Startup entry is disabled (kept as
`.relay/bin/relay-tunnel.cmd.disabled`), and the VPS has **0** listeners on 3334.
Re-enable it only if WireGuard cannot start — restore the Startup entry, set the
haproxy backend back to `127.0.0.1:3334`, reload haproxy.

Expect a short outage whenever the link restarts: haproxy marks the backend `DOWN`
(`Connection refused`) after `fall 3` failed checks and `UP` a few seconds after
the tunnel returns. That window is what a `FAIL connected but the server closed
without replying` looks like through the relay — it is not a pool fault. One
`--external` run also reported a single node timing out (ro1) while the pool
answered normally; the retry with different nodes was 4/4, so treat a lone node
failure as that node, and only a 0/4 as a real problem.

### Pool PC topology (confirmed while wiring this up)

`p2pool.exe` runs with `--stratum 0.0.0.0:3334`, and `xmrig-proxy` listens on
`0.0.0.0:3333` in front of it. The relay therefore targets **3333, the proxy**,
and the proxy must keep binding `0.0.0.0` — bound to `127.0.0.1` it would refuse
the tunnel address `10.66.66.2`. An earlier note in this file claiming p2pool
itself was on 3333 was wrong.

### Why the relay was needed at all (kept for the record)

Before the VPS, `mine.xmrjackpot.com` pointed at the house WAN address
`74.214.242.54` and nothing inbound worked: `verify-stratum.py 74.214.242.54 3333
--external` got 0/4 nodes, and ICMP plus TCP/80 timed out from the same nodes,
while `127.0.0.1:3333` answered normally. The address was current (the router
reported it as its own), so the block was upstream — ISP filter or router inbound
firewall. That is also why a static or "dedicated" address from the ISP would not
have fixed it by itself: the address was already public and still unroutable
inbound.

## 1. Bring the relay up (VPS, root)

```bash
scp relay/bootstrap-relay.sh root@<VPS_IP>:/root/
ssh root@<VPS_IP> 'bash /root/bootstrap-relay.sh'
```

**First login.** RackNerd hands you `root` and a password by email, and there is
**no cloud firewall** in front of the instance — the OS firewall the script
configures with `ufw` is the only one, which is why it opens exactly `22/tcp`,
`3333/tcp` and `51820/udp` and nothing else. If the panel accepted a public key
at the SSH-keys tab, log in with it (`ssh -i <private-key> root@<VPS_IP>`). If it
did not, run this once in the SolusVM browser console, replacing the key with the
contents of `.relay/ssh/relay_admin.pub`:

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh
echo 'ssh-ed25519 AAAA... relay-admin' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

Then from this workstation `ssh -i .relay/ssh/relay_admin root@<VPS_IP> true` should
return silently. Only after that run the bootstrap.

It installs WireGuard + haproxy, generates the server key, generates a home peer,
writes `/etc/wireguard/wg0.conf` and `/etc/haproxy/haproxy.cfg`, validates the
haproxy config, opens `22/tcp`, `3333/tcp` and `51820/udp` in ufw, and prints the
ready-made Windows peer config. Re-running it is safe and keeps the existing peer.

The haproxy timeouts are deliberately 4 hours: stratum connections sit idle for
minutes between shares, and the stock 30–50 s defaults would cut miners off
mid-session (which shows up as random `connection reset` in xmrig).

## 2. Join the pool PC to the relay (Windows)

Import the printed config as a WireGuard tunnel, activate it, then check:

```powershell
ping 10.66.66.1
New-NetFirewallRule -DisplayName "p2pool stratum via relay" `
  -Direction Inbound -Protocol TCP -LocalPort 3333 `
  -RemoteAddress 10.66.66.1 -Action Allow
```

`AllowedIPs = 10.66.66.1/32` is intentional: only relay traffic is tunnelled, so
`xmrjackpot.com`, the existing Cloudflare tunnel and normal browsing still leave
through the house connection.

## 3. Prove it before touching DNS

From the pool PC, run the probe twice — once for stratum through the tunnel, once
for reachability from other countries:

```bash
python relay/verify-stratum.py <VPS_IP> 3333
python relay/verify-stratum.py <VPS_IP> 3333 --external
```

The first is not an external test when run here: the packet leaves over `wg0` and
comes back through haproxy, so it proves the tunnel and the backend but says
nothing about the internet reaching the VPS. `--external` has check-host.net
nodes in several countries open the socket, which is exactly what a provider
firewall blocks while `ufw` inside the box looks fine. Both lines must PASS. A
bare TCP connect is not enough for the first: it also succeeds if haproxy is
merely buffering.

If you would rather not trust this machine at all, run the same command from a
phone hotspot instead — it must not be on the LAN.

`PASS` means a real p2pool `login` came back through haproxy and the WireGuard
tunnel, with a job in it. Do not continue on anything less.

## 4. Move the record

Cloudflare → DNS → `mine`:

| field | before | after |
|---|---|---|
| Type | A | A |
| Content | `74.214.242.54` | `155.94.157.66` |
| Proxy status | DNS only | **DNS only** (must stay grey — TCP cannot be proxied) |
| TTL | Auto (300 s) | Auto (300 s) |

Then re-run the probe against the name, and again from the hotspot:

```bash
python relay/verify-stratum.py mine.xmrjackpot.com 3333
```

**Leave the router's 3333 forward open for ~15 minutes** after the flip (one full
300 s TTL plus slack). Miners that resolved the old address keep connecting to the
house until their cached answer expires; closing it immediately would disconnect
exactly those miners. After the wait, close the forward — the pool then accepts
stratum only from `10.66.66.1`.

## 5. Confirm the exposure is gone

```bash
dig +short mine.xmrjackpot.com        # -> the VPS IP, not 74.214.242.54
dig +short xmrjackpot.com             # -> Cloudflare anycast (unchanged)
```

Neither record should return the house IP. Note that hiding it in DNS does not
rewrite history: any leak that already happened (search-engine caches, blocklists,
a previous DNS export) still holds the old address, and a determined scanner can
still sweep the ISP range for an open 3333 — which is why closing the forward
matters as much as moving the record.

## Monitoring (`relay/monitor-relay.py`)

Runs on the pool PC with the Python already there: no admin, no installs, no
dependency on the VPS console. Five probes, each answering a different question:

| probe | target | what it proves |
|---|---|---|
| `pool` | `127.0.0.1:3333` | xmrig-proxy is answering locally |
| `public` | `mine.xmrjackpot.com:3333` | a miner's exact path returns a real job |
| `dns` | `mine` -> `155.94.157.66` | nobody re-pointed or hijacked the record |
| `tunnel` | VPS over ssh | haproxy active, **backend check state**, `wg0` handshake younger than 180 s |
| `external` | check-host.net nodes | ingress works from outside our egress (default every 15 min) |

The tunnel probe reads haproxy's own answer instead of guessing. `haproxy.cfg` now
carries a runtime socket, and `relay/relay-backend-status.py` is deployed to
`/usr/local/bin/relay-backend-status.py` on the VPS to read it:

```
global
    stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners
    stats timeout 30s
```

`bootstrap-relay.sh` now writes that block itself, so a rebuild keeps the socket.
The reader still has to be copied once:

```bash
scp relay/relay-backend-status.py root@155.94.157.66:/usr/local/bin/relay-backend-status.py
ssh root@155.94.157.66 'chmod 755 /usr/local/bin/relay-backend-status.py'
```

That is what lets an alert name the layer. Three distinct outcomes, three distinct
messages:

- `haproxy down on the VPS` — haproxy itself is not running
- `haproxy up but backend is DOWN/MAINT (check L4KO) - tunnel or pool, not haproxy`
- `WireGuard handshake 240s old (peer down)` — tunnel peer gone, haproxy healthy

Without the socket, a dead tunnel, a dead pool and a firewall block all produce the
same symptom and the operator learns nothing.

### Automated witness (deployed 2026-10-24 20:08 UTC)

A Worker now measures the relay from Cloudflare's edge and answers JSON:

```
https://site-1a0ea1f6ed6a49ebb2c0de8e532498c3.freebuff.page
{
  "verdict": "PASS", "detail": "job 262", "control": true,
  "egress": "cloudflare-edge:unknown", "when_epoch": 1792872510
}
```

That is a genuine second network: a stratum login from Cloudflare's edge to
`mine.xmrjackpot.com:3333`, returning a real p2pool job. Source lives in
`relay/witness-worker/` (`index.js`, `deploy.json`, `public/`), and the monitor
reads it with `--witness-url`, which the supervisor already passes. Requests are
cheap: one per monitor cycle is ~1 440/day against a 100 000/day free allowance.

Two things that had to be got right, both learned by failing:

- **Sites only accepts files inside the workspace root**, which here is
  `C:\Users\birds\OneDrive\Documents\WindowsPowerShell`, not this repo. The Worker
  is therefore copied to `relay-witness/` in the workspace before deploying; the
  repo copy is the source of truth.
- **The Worker's control cannot be `1.1.1.1:443`.** Workers refuse raw TCP to
  Cloudflare's own HTTP endpoints:
  `proxy request failed ... looks like you might be trying to connect to a
  HTTP-based service`. The control is `gopher.floodgap.com:70` (the example
  Cloudflare's own docs use), and the relay is probed *before* the control so a
  broken control can never mask a working relay.

Republish after editing `relay/witness-worker/index.js`:

```bash
cp relay/witness-worker/index.js "$WORKSPACE/relay-witness/index.js"
# then deploy sites: manifest relay-witness/deploy.json, environment preview
```

The endpoint is **deliberately public** (decision 2026-10-24): it exposes only the
hostname and port, carries no credentials, and a witness that requires a secret to
read is one more thing that can break during an incident. The preview environment
is published, quota 100 000 requests/month; the monitor's one call per cycle is
about 1 440/day, well inside it.

If the Worker itself is ever unreachable, the witness goes back to `SKIP` and the
local probes keep running — the monitor never treats a missing second opinion as
a failure of the relay.

### Manual witness (any network, no account)

Every probe above runs from one network. That network is itself unusual: this PC's
egress is Cloudflare WARP (`104.28.x.x`), which is precisely why it can reach the
VPS while check-host's scanner ranges mostly cannot. One vantage is not proof.

`relay/ingress-check.py` is the second opinion. No dependencies, no key, no admin —
run it anywhere Python 3 exists, from any network:

```bash
python3 relay/ingress-check.py --witness .relay/witness.json
# network egress : 203.0.113.7
# control        : PASS  1.1.1.1:443
# dns            : 155.94.157.66
# tcp 3333       : PASS  78 ms
# stratum        : PASS  job 1aa
# VERDICT        : PASS
```

The monitor reads `.relay/witness.json` every cycle:

| witness state | monitor does |
|---|---|
| fresh PASS (< 24 h) | `witness=PASS`, named with the egress IP and age |
| fresh FAIL | **ALERT** — a network that is not ours could not reach the relay |
| `INCONCLUSIVE` (control unreachable) | SKIP: that network is broken, its opinion is worthless |
| older than `--witness-max-age` | SKIP, reported as stale — an old PASS is not evidence |
| missing | SKIP, with the command to fix it |

All five states are covered by fixtures (`fresh PASS`, `fresh FAIL`, `stale`,
`INCONCLUSIVE`, `missing`) and each produced the verdict in that table. The witness
is suppressed when the local public probe already failed, so a dead relay produces
one alert, not two.

To take a real witness: put a laptop on a phone hotspot, run the command inside this
repo (it writes straight into `.relay/witness.json`), then rejoin the normal
network. A phone with Termux works too — copy the resulting file back. Refresh it
whenever the monitor reports it stale; an automated vantage would need an account
or host outside this network, which does not exist yet.

**Hysteresis: one bad sample is not an alert.** A probe has to fail twice in a row
(`--alert-after 2`, or one cycle with `--once`) before it speaks; the first failure
prints `pending: <probe> failed once ...` and is logged only. That matters here
because third-party nodes and single missed keepalives produce isolated bad
samples: on 2026-10-24 the external probe saw `0/6` on 3333 while one node still
reached 22, then recovered without anything having been wrong with the relay.

**One root cause, one alert.** Probes run in specificity order — `pool`, `tunnel`,
`public`, `dns` — and dependents are suppressed: if the pool is down, the tunnel and
public probes log their failure but stay silent, because the pool alert already
explains them. Verified by putting the backend into maintenance with haproxy's
runtime API (`set server p2pool/pool-pc state maint`), which produced exactly one
line — `ALERT: Backend tunnel is down [tunnel] haproxy up but backend is MAINT` —
and restored with `state ready`.

```bash
python relay/monitor-relay.py                     # 60 s cycles, no external probe
python relay/monitor-relay.py --external          # + check-host every 15 min
python relay/monitor-relay.py --once              # one cycle, exit 1 on any ALERT
python relay/monitor-relay.py --once --no-popup   # same, silently (tests)
```

Alerts fire on **state change**, not every cycle: one popup and one line when a
probe breaks, one `RECOVERED:` line when it comes back. Verdicts are the only
thing on stdout; per-probe detail goes to `.relay/logs/monitor.jsonl`, and the
current snapshot to `.relay/relay-status.json` for anything else that wants to
read it.

Flags: `--witness <path>` (default `.relay/witness.json`), `--witness-max-age`
(seconds, default 86400), `--no-witness`, `--alert-after`, `--no-popup`.

`relay/monitor-supervisor.bat` restarts the monitor if it ever exits, and
`relay-monitor.cmd` in the user's Startup folder launches it minimised at logon —
no admin rights, no service, no scheduled task. Stop it by killing the
`monitor-relay.py` python process; nothing restarts it except the next logon.

### The same snapshot is the public status page

`.relay/relay-status.json` is not only for the operator. The pool app reads it and
serves it, anonymously, at two URLs:

| URL | what it is |
|---|---|
| `https://xmrjackpot.com/status` | human page: one line per probe, the endpoint, pool miners/hashrate |
| `https://xmrjackpot.com/api/relay-status` | the same facts as JSON, `Cache-Control: no-store` |

Both are `noindex`; there is no API key and no admin session behind either. The
verdict has three values, deliberately not two:

* **operational** — the monitor ran within the last 5 minutes and every required
  probe passed.
* **degraded** — the monitor is fresh and something failed; the page names the
  failing layer (e.g. "haproxy up but backend is DOWN"), so a miner can tell the
  relay from their own firewall.
* **unknown** — the file is missing or older than 5 minutes. This is *not* "down":
  the relay can be perfectly healthy while the monitor is not running, and a page
  that cries wolf sends miners to debug the wrong end of the wire.

So the page is a view of the monitor, not a second monitor. If it says `unknown`
while the relay is fine, start the monitor — `python relay/monitor-relay.py`
writes the file on its first cycle. Only what the monitor measures appears here;
the page never probes anything itself.

**The external probe has a control built in.** On 2026-10-24 check-host's nodes
stopped reaching this VPS at all: `tcp/22` was as dead as `3333`, a fresh listener
on 8080 was equally unreachable, and a `tcpdump` on the VPS saw **zero** SYNs while
the public stratum probe from the pool PC kept answering in ~230 ms. The VPS could
still reach `il1`/`us3` check-host nodes, but `de2` timed out in both directions —
the signature of an upstream network filtering the scanner ranges, not a dead
relay. The probe therefore classifies instead of merely reporting, using **three** inputs,
all measured on the checker's own nodes:

| relay 3333 | local public probe | nodes -> `1.1.1.1:443` | nodes -> VPS `22` | verdict |
|---|---|---|---|---|
| connects | - | - | - | **PASS** |
| 0 | failed | - | - | SKIP, real outage (the public probe alerts) |
| 0 | ok | 0 | - | SKIP, checker infrastructure |
| 0 | ok | ok | 0 | SKIP, scanner-range filtering of our IP |
| 0 | ok | ok | ok | **ALERT**, genuine ingress fault on 3333 |

All five branches are covered by a stubbed test (swap `check_host` for a fake and
call `probe_external`), so the classification is verifiable without breaking the
relay. Live, this currently reports:

```
skipped: 6/6 nodes reach 1.1.1.1 but 0/6 reach this VPS on any port
         - scanner-range filtering upstream, not a relay fault
```

One design note, because it is easy to get wrong: both controls must be measured
**from the checker's nodes**. A control that opens sockets from the pool PC to
check-host's own hosts proves only that *our* egress works — which is already
proven by the fact that the API call itself succeeds — and would classify every
outage as "external filtering". The `1.1.1.1:443` control exists to catch a broken
checker; the `tcp/22` control exists to catch our IP being filtered while the
checker is healthy. Neither can prove the VPS is alive on its own: if the VPS is
down, both fail and the table would say "filtering", which is why the local public
probe has the final say and the external probe stays silent when it fails too.

## Caveats specific to p2pool

- **One source IP for everyone.** p2pool sees every miner as `10.66.66.1`. Its
  per-IP ban logic therefore hits all miners at once if one of them misbehaves, and
  per-IP stats on the pool UI collapse into a single entry. Stratum has no PROXY
  protocol support, so the real client IP cannot be forwarded without patching the
  pool server. If that trade-off is unacceptable, the alternative is Spectrum on
  Enterprise, not a different relay.
- **WireGuard must stay up.** If the home peer drops, haproxy's health check fails
  and miners get connection refused until it returns. Run WireGuard as the Windows
  service (the installer does this by default) so it starts before login and
  survives reboots; `PersistentKeepalive = 25` keeps the NAT mapping alive.

## Rollback (about 60 seconds)

Point `mine` back at `74.214.242.54`, reopen the router's 3333 forward, and stop
the tunnel on the pool PC. Miners recover as their cached TTL expires. The VPS can
stay up (idle) or be destroyed.
