/* scout_pro_bridge.js — THE SERVER'S ENTITLEMENT, IN THE BROWSER (Oct 24).
 *
 * push_client.js gates every alert on localStorage.scout_pro:
 *
 *     scout_pro !== "true"  ->  nothing fires
 *
 * ...and until now NOTHING in the site ever wrote that key. The intended
 * writer was a bridge that did not exist in this tree, so the paywall
 * could not open: a member who donated never had alerts switched on,
 * and the ones who did were only ever the hand-set ones.
 *
 * This is that bridge, and it does not decide anything: GET /api/scout/pro
 * is authenticated and server-authoritative (subscription window or the
 * 100-day lifetime grant). The browser only CACHES the verdict, because
 * routeAlert() has to answer synchronously when a signal lands.
 *
 * Rules kept deliberately:
 *   - no verdict (signed out, error response) -> locked
 *   - no network at all -> keep the cached verdict; never unlock on a
 *     failed read, and never wipe a paying member's flag because their
 *     connection dropped
 *   - every write announces itself with a `scoutprochange` event, so the
 *     paywall surfaces can re-render without polling
 */
(function () {
  var KEY = "scout_pro";
  var UNTIL = "scout_pro_until";

  function cached() {
    try {
      return localStorage.getItem(KEY) === "true";
    } catch (e) {
      return false;   // private mode with storage blocked: stay locked
    }
  }

  function write(active, until) {
    var was = cached();
    try {
      localStorage.setItem(KEY, active ? "true" : "false");
      if (until) {
        localStorage.setItem(UNTIL, String(until));
      } else {
        localStorage.removeItem(UNTIL);
      }
    } catch (e) { /* storage blocked: the return value still answers */ }
    if (was !== !!active) {
      try {
        document.dispatchEvent(new CustomEvent("scoutprochange", {
          detail: { active: !!active, until: Number(until) || 0 }
        }));
      } catch (e) { /* no CustomEvent (old harness): the flag is enough */ }
    }
    return !!active;
  }

  /* Ask the server, cache the answer. Resolves to the active flag. */
  function refresh() {
    return fetch("/api/scout/pro", {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    }).then(function (r) {
      if (!r.ok) {
        return write(false, 0);        // signed out / refused: locked
      }
      return r.json().then(function (d) {
        if (!d || typeof d !== "object") {
          return write(false, 0);
        }
        return write(!!d.active, d.until);
      });
    }).catch(function () {
      return cached();                 // offline: keep what we have
    });
  }

  window.XMRScoutPro = {
    refresh: refresh,
    isActive: cached,
    key: KEY
  };

  // THE IDENTITY CONTRACT (repo-wide): app.js announces every login and
  // logout as xmrjackpot:auth, and a per-user surface re-reads on it.
  // Without this, a same-page sign-in would leave the previous account's
  // verdict cached until a reload — which is exactly the stale-paywall
  // the bridge exists to remove.
  document.addEventListener("xmrjackpot:auth", refresh);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refresh);
  } else {
    refresh();
  }
})();
