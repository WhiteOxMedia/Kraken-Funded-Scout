"""cf_www_and_purge.py — route www to the tunnel, then purge robots.txt.

WHY THIS EXISTS: `cloudflared` runs here as a Windows service with
`tunnel run --token-file C:\\ProgramData\\cloudflared\\token`, which means a
remotely-managed tunnel. The hostname -> origin mapping lives in the
Cloudflare API (Zero Trust -> Tunnels -> Public Hostnames), not in a local
config.yml, so no file on this machine can add www. This script performs
the three changes the redirect needs, idempotently:

  1. append `www.xmrjackpot.com` to the tunnel ingress that already serves
     the apex, reusing that rule's service (the catch-all stays last);
  2. point the `www` DNS record at `<tunnel-id>.cfargotunnel.com`, proxied;
  3. purge `https://xmrjackpot.com/robots.txt`, which Cloudflare caches
     for four hours (the origin has served the right file since the route
     shipped).

Once the first two land, the origin's own 301 does the rest: any www host
is redirected to the apex with its path and query intact.

TOKEN: read from $CLOUDFLARE_API_TOKEN (or $CF_API_TOKEN), or from the
file path given as the only argument. It is never printed.

  python cf_www_and_purge.py                     # token from the shell
  python cf_www_and_purge.py C:\\Users\\me\\cf_token.txt

Needs a token with Zone:DNS:Edit, Zone:Cache Purge and
Account:Cloudflare Tunnel:Edit. Prints what changed; re-running is safe.
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "xmrjackpot.com"
WWW = "www." + ZONE_NAME
PURGE_FILES = ["https://" + ZONE_NAME + "/robots.txt"]


def read_token() -> str:
    if len(sys.argv) > 1:
        try:
            with open(sys.argv[1], encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:
            raise SystemExit(f"cannot read the token file: {exc}")
    return (os.environ.get("CLOUDFLARE_API_TOKEN")
            or os.environ.get("CF_API_TOKEN") or "").strip()


def call(method: str, path: str, token: str, body=None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("errors") or detail
        except ValueError:
            detail = detail[:300]
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {detail}")


def short(identifier: str) -> str:
    return (identifier or "")[:8] + "…"


def main() -> None:
    token = read_token()
    if not token:
        raise SystemExit(
            "no token: set CLOUDFLARE_API_TOKEN, or pass the path to a file "
            "holding it")

    verify = call("GET", "/user/tokens/verify", token)
    print("token:", verify.get("result", {}).get("status", "?"))

    zones = call("GET", f"/zones?name={ZONE_NAME}", token).get("result") or []
    if not zones:
        raise SystemExit(f"zone {ZONE_NAME} not visible to this token")
    zone_id = zones[0]["id"]
    account_id = zones[0]["account"]["id"]
    print("zone:", ZONE_NAME, short(zone_id), "| account:", short(account_id))

    tunnels = call("GET", f"/accounts/{account_id}/cfd_tunnel?is_deleted=false",
                   token).get("result") or []
    chosen = None
    for tunnel in tunnels:
        cfg = call("GET",
                   f"/accounts/{account_id}/cfd_tunnel/{tunnel['id']}/configurations",
                   token).get("result") or {}
        config = cfg.get("config") or {}
        ingress = list(config.get("ingress") or [])
        hosts = [r.get("hostname") for r in ingress if r.get("hostname")]
        if ZONE_NAME in hosts:
            chosen = (tunnel, config, ingress, hosts)
            break
    if not chosen:
        raise SystemExit(f"no tunnel ingress serves {ZONE_NAME}")

    tunnel, config, ingress, hosts = chosen
    print("tunnel:", tunnel.get("name"), short(tunnel["id"]),
          "| hosts:", ", ".join(hosts))

    if WWW in hosts:
        print("www: already routed by the tunnel")
    else:
        apex = next(r for r in ingress if r.get("hostname") == ZONE_NAME)
        rule = {"hostname": WWW, "service": apex.get("service")}
        # the catch-all (an entry with no hostname) must stay last
        cut = next((i for i, r in enumerate(ingress) if not r.get("hostname")),
                   len(ingress))
        ingress.insert(cut, rule)
        config["ingress"] = ingress
        call("PUT",
             f"/accounts/{account_id}/cfd_tunnel/{tunnel['id']}/configurations",
             token, {"config": config})
        print(f"www: routed {WWW} -> {rule['service']}")

    target = tunnel["id"] + ".cfargotunnel.com"
    records = call("GET", f"/zones/{zone_id}/dns_records?name={WWW}",
                   token).get("result") or []
    want = {"type": "CNAME", "name": WWW, "content": target, "proxied": True}
    if not records:
        call("POST", f"/zones/{zone_id}/dns_records", token, want)
        print(f"dns: created {WWW} CNAME {target} (proxied)")
    else:
        rec = records[0]
        if (rec.get("type") == "CNAME" and rec.get("content") == target
                and rec.get("proxied") is True):
            print(f"dns: already {WWW} CNAME {target} (proxied)")
        else:
            call("PATCH", f"/zones/{zone_id}/dns_records/{rec['id']}", token, want)
            print(f"dns: {rec.get('type')} {rec.get('content')} -> "
                  f"CNAME {target} (proxied)")

    call("POST", f"/zones/{zone_id}/purge_cache", token,
         {"files": PURGE_FILES})
    print("purged:", ", ".join(PURGE_FILES))
    print("done - allow a few seconds, then verify")


if __name__ == "__main__":
    main()
