"""cdn_purge.py — evict every edge copy a rebuild or page edit can leave stale.

WHY THIS EXISTS: Cloudflare serves the bundle zip with `max-age=14400`, so a
fresh build can sit behind a four-hour-old edge object while `/download`
already names the new version. The localized pages and the status/version
endpoints are no-cache today, but "no-cache today" is a header, not a
guarantee: one cache-rule edit turns them into HITs. One purge after a
rebuild is cheaper than reasoning about which header is in force.

THE LIST (keep in step with the routes in pool/server.py):

  bundle     the zip, its sidecar and the API the card reads
  localized  /download in all six languages
  status     the status page and the version endpoint
  chrome     / — only reachable content changes if the navbar, footer, or
             global JS changed
  miner      /api/miner-download — only if the miner .bat changed

Every run purges the WHOLE list, deliberately. A single API call carries all
of them, and forgetting a conditional entry is the exact failure this file
exists to prevent; purging a URL that did not change costs nothing.

TWO URLS THAT LOOK LIKE THEY BELONG AND DO NOT (checked Oct 24, 404 from
both the origin and the edge):

  /downloads/KrakenScoutBundle.zip.json  the manifest is a FILE the server
      reads to build /api/downloads/bundle-info (server.py:212, :375); it
      has no route of its own, so the API entry below is the manifest's
      only served form.
  /index  the front page is routed at / alone (server.py:487).

Adding either back would keep the dry-run looking complete while purging a
404. If a route for one is ever added, add its entry here in the same
commit - and delete this note.

  python cdn_purge.py                      # token from CLOUDFLARE_API_TOKEN
                                           # or CF_API_TOKEN
  python cdn_purge.py C:\\path\\cf.token     # or a file holding it
  python cdn_purge.py --dry-run            # print the URLs, no API call

Needs a token with Zone:Cache Purge (read access to the zone list). The token
is never printed.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "xmrjackpot.com"
ORIGIN = "https://" + ZONE_NAME

# ── the canonical list ────────────────────────────────────────────────
PURGE_PATHS = [
    # bundle: rebuild the zip and these three move together (no .zip.json:
    # the manifest is a dist file, and bundle-info is its only route)
    "/downloads/KrakenScoutBundle.zip",
    "/downloads/KrakenScoutBundle.zip.sha256",
    "/api/downloads/bundle-info",
    # localized pages: every language, English owns the unprefixed URL
    "/download",
    "/es/download",
    "/de/download",
    "/fr/download",
    "/pt/download",
    "/ru/download",
    # status surface
    "/status",
    "/api/version",
    # chrome: navbar, footer, global JS (the front page is routed at / only)
    "/",
    # miner download logic
    "/api/miner-download",
]
URLS = [ORIGIN + p for p in PURGE_PATHS]


def read_token(positional: str | None) -> str:
    if positional:
        try:
            with open(positional, encoding="utf-8") as fh:
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


def edge_status(url: str) -> str:
    """What the edge says about a URL right now, as evidence the purge took.
    DYNAMIC/MISS is what a purged object reports; HIT would mean a copy is
    still being served."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.headers.get("cf-cache-status", "-")
    except (urllib.error.URLError, OSError):
        return "unreachable"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="purge the edge copies a bundle rebuild or page edit "
                    "can leave stale")
    parser.add_argument("token_file", nargs="?",
                        help="file holding the Cloudflare API token "
                             "(default: $CLOUDFLARE_API_TOKEN / $CF_API_TOKEN)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the URLs and exit without calling the API")
    args = parser.parse_args()

    token = read_token(args.token_file)
    if args.dry_run:
        print(f"would purge {len(URLS)} files from {ZONE_NAME}:")
        for url in URLS:
            print("  " + url)
        return
    if not token:
        raise SystemExit(
            "no token: set CLOUDFLARE_API_TOKEN, or pass the path to a file "
            "holding it (never paste the value into a chat)")

    verify = call("GET", "/user/tokens/verify", token)
    print("token:", verify.get("result", {}).get("status", "?"))

    zones = call("GET", f"/zones?name={ZONE_NAME}", token).get("result") or []
    if not zones:
        raise SystemExit(f"zone {ZONE_NAME} not visible to this token")
    zone_id = zones[0]["id"]

    result = call("POST", f"/zones/{zone_id}/purge_cache", token,
                  {"files": URLS})
    if not result.get("success"):
        raise SystemExit("purge refused: " + json.dumps(result.get("errors")))

    print(f"purged {len(URLS)} files from {ZONE_NAME}")
    for url in URLS:
        print(f"  {edge_status(url):<10} {url}")


if __name__ == "__main__":
    main()
