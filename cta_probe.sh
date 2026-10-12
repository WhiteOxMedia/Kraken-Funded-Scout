#!/usr/bin/env bash
# cta_probe.sh — the shared browser-shaped curl probes for the :3094
# pool server and its Cloudflare tunnel front.
#
# SOURCED (not executed): roll_3094.sh sources this after its own
# `cd`, so every path here is repo-relative. The Kraken CTA counter
# drive (test_kraken_cta_counter.py) pins THIS FILE's content as the
# one definition of the probe contract — expected codes, payload
# shape, and endpoint — so a probe edited here and only there is a
# real contract change the drive catches, never a script-local
# drift. Test-side edits drive from contract: probe expected codes
# or payload shape changed => the drive REDs.
#
# Exports (single definition, one owner):
#   PASS_N / FAIL_N         live check counters (caller prints the verdict)
#   check LABEL EXPECTED URL [UA] [ORIGIN] [BODY]
#                           one HTTP expectation, FAILing loud with the
#                           first bytes of the response body on mismatch
#                           (curl transport failure reads as 000, never
#                           kills the caller under set -e)
#   check_surface BASE      the full five-probe triangle against one
#                           front: /api/stats 200, the page carries
#                           data-cta-slot tags, the counter accepts a
#                           browser-shaped click (200) and refuses a
#                           bot UA (403) and a bad slot (422)
#   _CTA_PROBE_* constants  the counted endpoint, OUR referral url,
#                           the browser UA, the page/pool origin
set -u

PASS_N=${PASS_N:-0}; FAIL_N=${FAIL_N:-0}

_CTA_PROBE_HEALTH=/api/stats
_CTA_PROBE_COUNTER=/api/cta/kraken-click
_CTA_PROBE_URL=https://invite.kraken.com/JDNW/29bygijw
_CTA_PROBE_UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'
_CTA_PROBE_ORIGIN=https://xmrjackpot.com

# check LABEL EXPECTED_URL_PATTERN URL [UA] [ORIGIN] [BODY] — one probe,
# loud on failure (the first 160 bytes of the body ride the FAIL line)
check() {
  local label="$1" expect="$2" url="$3" ua="${4:-}" origin="${5:-}" body="${6:-}" got
  got=$(curl -s -o /tmp/rolllast.out -w '%{http_code}' "$url" \
        ${ua:+-H "User-Agent: $ua"} ${origin:+-H "Origin: $origin"} \
        ${body:+-H "Content-Type: application/json" -d "$body"}) || echo 000
  if [ "$got" = "$expect" ]; then
    printf '  PASS %s (http %s)\n' "$label" "$got"; PASS_N=$((PASS_N+1))
  else
    printf '  FAIL %s (http %s, expected %s — %s)\n' "$label" "$got" "$expect" \
           "$(head -c 160 /tmp/rolllast.out | tr -d '\r\n')"; FAIL_N=$((FAIL_N+1))
  fi
}

# check_surface BASE — the five-probe triangle against one front
check_surface() {
  local base="$1" n
  check "stats health"        200 "$base$_CTA_PROBE_HEALTH"
  n=$(curl -s "$base/" | grep -c 'data-cta-slot' || true)
  if [ "$n" -gt 0 ]; then printf '  PASS page carries %s data-cta-slot tags\n' "$n"; PASS_N=$((PASS_N+1))
  else printf '  FAIL page carries 0 data-cta-slot tags\n'; FAIL_N=$((FAIL_N+1)); fi
  check "counter valid click" 200 "$base$_CTA_PROBE_COUNTER" "$_CTA_PROBE_UA" "$_CTA_PROBE_ORIGIN" \
        "{\"slot\":\"hero-top\",\"url\":\"$_CTA_PROBE_URL\"}"
  check "counter bot UA"      403 "$base$_CTA_PROBE_COUNTER" "curl/8.0" "$_CTA_PROBE_ORIGIN" \
        "{\"slot\":\"hero-top\",\"url\":\"$_CTA_PROBE_URL\"}"
  check "counter bad slot"    422 "$base$_CTA_PROBE_COUNTER" "$_CTA_PROBE_UA" "$_CTA_PROBE_ORIGIN" \
        "{\"slot\":\"not-a-real-slot\",\"url\":\"$_CTA_PROBE_URL\"}"
}
