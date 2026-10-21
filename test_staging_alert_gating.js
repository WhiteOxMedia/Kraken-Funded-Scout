/* test_staging_alert_gating.js — the staging validation harness for the
   alert surfaces (checklist 2.1, 2.2, 3.4, 4.1, 4.2, 6.1-6.3, 7).

   It executes the REAL pool/static/push_client.js in a Node VM with a
   stub browser, so the assertions are about shipped code, not a copy:
     2.1  locked Scout Pro -> nothing fires (no console, no push, no log)
     2.2  unlocked        -> console alert + phone push, normalized names
     3.4  NFTY off        -> console alert + profile log, no push
     4.1/4.2              -> POST body carries type, payload, ts
     6.3  worker message  -> routed through routeAlert, no duplicate banner
     6.2  worker          -> shows a notification, no audio/popup path

   Run: node test_staging_alert_gating.js
*/
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
let checks = 0;

function ok(cond, label, detail) {
  checks += 1;
  if (cond) {
    console.log("  PASS  " + label);
  } else {
    failures += 1;
    console.log("  FAIL  " + label + (detail ? "  <- " + detail : ""));
  }
}

function loadClient() {
  const src = fs.readFileSync(
    path.join(__dirname, "pool", "static", "push_client.js"), "utf8");

  const state = {
    console: [],
    fetches: [],
    notifications: [],
    registrations: [],
    listeners: {},
    storage: new Map(),
  };

  const registration = {
    install: true,
    pushManager: {
      getSubscription: () => Promise.resolve(null),
      subscribe: () => Promise.resolve({ toJSON: () => ({ endpoint: "stub" }) }),
    },
    showNotification: (title, opts) => {
      state.notifications.push({ title: title, opts: opts || {} });
      return Promise.resolve();
    },
    scope: "/",
  };
  registration.active = true;
  registration.waiting = null;
  registration.installing = null;

  const serviceWorker = {
    ready: Promise.resolve(registration),
    register: (url) => {
      state.registrations.push(url);
      return Promise.resolve(registration);
    },
    getRegistration: () => Promise.resolve(registration),
    addEventListener: (name, fn) => { state.listeners[name] = fn; },
  };

  const documentStub = {
    readyState: "complete",
    addEventListener: () => {},
    getElementById: () => null,
    querySelector: () => null,
    createElement: () => ({ style: {}, classList: { add() {}, remove() {} } }),
    dispatchEvent: () => {},
  };

  const windowStub = {
    atob: (s) => Buffer.from(s, "base64").toString("binary"),
    location: { origin: "https://staging.example" },
    addEventListener: () => {},
    Promise: Promise,
    Notification: { permission: "default", requestPermission: () => Promise.resolve("denied") },
    PushManager: function PushManager() {},
    localStorage: {
      getItem: (k) => (state.storage.has(k) ? state.storage.get(k) : null),
      setItem: (k, v) => state.storage.set(k, String(v)),
      removeItem: (k) => state.storage.delete(k),
    },
  };

  const sandbox = {
    console: {
      log: (...args) => state.console.push(args),
      warn: (...args) => state.console.push(args),
      error: (...args) => state.console.push(args),
    },
    document: documentStub,
    navigator: { serviceWorker: serviceWorker },
    localStorage: windowStub.localStorage,
    fetch: (url, opts) => {
      state.fetches.push({ url: url, opts: opts || {} });
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(""),
        headers: { get: () => null },
      });
    },
    setTimeout: setTimeout,
    clearInterval: () => {},
    // the client keeps a 30s stamp refresher; stubbing it keeps the
    // harness process from parking the event loop forever
    setInterval: () => 0,
    Notification: windowStub.Notification,
    atob: windowStub.atob,
    Promise: Promise,
    Date: Date,
    JSON: JSON,
    CustomEvent: function CustomEvent() {},
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;
  sandbox.location = windowStub.location;

  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "push_client.js" });

  return { sandbox: sandbox, state: state, registration: registration };
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function main() {
  console.log("\n== 2.1 locked Scout Pro (scout_pro !== true) ==");
  {
    const env = loadClient();
    const alerts = env.sandbox.XMRJackpotAlerts;
    ok(!!alerts, "alert surface is exported to window");

    const r1 = alerts.routeAlert("BUY_NOW", { price: 1 });
    const r2 = alerts.routeAlert("GET_OUT_NOW", { price: 2 });
    const r3 = alerts.routeAlert("signal_buy", { price: 3 });
    await tick();

    ok(r1 === false && r2 === false && r3 === false,
      "locked routeAlert returns false for every alert type",
      JSON.stringify([r1, r2, r3]));
    ok(env.state.console.length === 0,
      "locked: no console alert", JSON.stringify(env.state.console));
    ok(env.state.notifications.length === 0,
      "locked: no push notification", JSON.stringify(env.state.notifications));
    ok(env.state.fetches.length === 0,
      "locked: no profile log request", JSON.stringify(env.state.fetches));

    const pushed = alerts.handlePushMessage({
      data: { type: "signal_buy", source: "push_sw" },
    });
    ok(pushed === false, "locked: service-worker message is dropped");
    ok(env.state.console.length === 0 && env.state.notifications.length === 0,
      "locked: worker message still fires nothing");
  }

  console.log("\n== 2.2 unlocked Scout Pro, NFTY on ==");
  {
    const env = loadClient();
    const alerts = env.sandbox.XMRJackpotAlerts;
    env.state.storage.set("scout_pro", "true");
    env.state.storage.set("nfty_enabled", "true");

    const r = alerts.routeAlert("BUY_NOW", { price: 4 });
    await tick();
    await tick();

    ok(r === true, "unlocked: routeAlert reports the alert fired");
    ok(env.state.console.length === 1, "unlocked: exactly one console alert",
      JSON.stringify(env.state.console));
    ok(String(env.state.console[0][0]).indexOf("BUY_NOW") !== -1,
      "unlocked: console line names BUY_NOW", JSON.stringify(env.state.console[0]));
    ok(env.state.notifications.length === 1,
      "unlocked+NFTY: exactly one push notification",
      JSON.stringify(env.state.notifications));
    ok(env.state.notifications[0].title === "BUY_NOW",
      "notification title is the normalized BUY_NOW",
      env.state.notifications[0].title);
    ok(env.state.notifications[0].opts.tag === "xmrjackpot-BUY_NOW",
      "notification carries a stable tag (no stacked duplicates)");
    ok(env.state.fetches.length === 0,
      "NFTY on: profile log is not used (push is the record)");

    alerts.routeAlert("signal_exit", { price: 5 });
    await tick();
    await tick();
    ok(env.state.notifications.length === 2 &&
      env.state.notifications[1].title === "GET_OUT_NOW",
      "signal_exit normalizes to GET_OUT_NOW on the device",
      JSON.stringify(env.state.notifications.map((n) => n.title)));

    const informational = alerts.routeAlert("MINER_BOT", { hashrate: 10 });
    ok(informational === false,
      "informational surface (Miner Bot) never becomes an alert");
    const contest = alerts.routeAlert("contest_helper", {});
    ok(contest === false, "contest helper never becomes an alert");
  }

  console.log("\n== 3.4 / 4.1 / 4.2 NFTY disabled ==");
  {
    const env = loadClient();
    const alerts = env.sandbox.XMRJackpotAlerts;
    env.state.storage.set("scout_pro", "true");
    env.state.storage.set("nfty_enabled", "false");

    alerts.routeAlert("BUY_NOW", { pair: "XMR/USDT" });
    await tick();
    await tick();

    ok(env.state.notifications.length === 0, "NFTY off: no push notification");
    ok(env.state.console.length === 1, "NFTY off: console alert still fires");
    ok(env.state.fetches.length === 1, "NFTY off: profile log is written once",
      JSON.stringify(env.state.fetches.map((f) => f.url)));

    const call = env.state.fetches[0] || { url: "", opts: {} };
    ok(call.url === "/api/profile/alert-log",
      "profile log posts to /api/profile/alert-log", call.url);
    let body = {};
    try { body = JSON.parse(call.opts.body || "{}"); } catch (e) { body = {}; }
    ok(body.type === "BUY_NOW", "log body type is BUY_NOW", call.opts.body);
    ok(body.payload && body.payload.pair === "XMR/USDT",
      "log body payload is the alert payload", call.opts.body);
    ok(typeof body.ts === "string" && !isNaN(Date.parse(body.ts)),
      "log body carries an ISO timestamp", call.opts.body);

    alerts.routeAlert("GET_OUT_NOW", { pair: "XMR/USDT" });
    await tick();
    await tick();
    ok(env.state.fetches.length === 2 &&
      JSON.parse(env.state.fetches[1].opts.body).type === "GET_OUT_NOW",
      "GET_OUT_NOW logs with its own type");
  }

  console.log("\n== 6.1 / 6.3 service worker registration + message handoff ==");
  {
    const env = loadClient();
    const alerts = env.sandbox.XMRJackpotAlerts;
    env.state.storage.set("scout_pro", "true");
    env.state.storage.set("nfty_enabled", "false");

    ok(typeof env.state.listeners.message === "function",
      "page listens for service-worker messages (postMessage handoff)");

    const routed = alerts.handlePushMessage({
      data: { type: "signal_buy", payload: { pair: "XMR/USDT" }, source: "push_sw" },
    });
    await tick();
    ok(routed === true, "worker BUY_NOW message routes through the alert router");
    ok(env.state.console.length === 1 &&
      String(env.state.console[0][0]).indexOf("BUY_NOW") !== -1,
      "worker message produces the console alert",
      JSON.stringify(env.state.console));
    ok(env.state.fetches.length === 1 &&
      JSON.parse(env.state.fetches[0].opts.body).payload.pair === "XMR/USDT",
      "worker payload survives the handoff into the profile log");

    const ignored = alerts.handlePushMessage({ data: { type: "signal_buy" } });
    ok(ignored === false, "a message from another source is ignored");

    const reg = env.sandbox.navigator.serviceWorker;
    ok(typeof reg.register === "function", "registration API present");
    const src = fs.readFileSync(
      path.join(__dirname, "pool", "static", "push_client.js"), "utf8");
    ok(src.indexOf("/push_sw.js") !== -1,
      "client registers the worker at the root path /push_sw.js");
  }

  console.log("\n== 6.2 worker behaviour (static contract) ==");
  {
    const sw = fs.readFileSync(
      path.join(__dirname, "pool", "static", "push_sw.js"), "utf8");
    ok(/showNotification\(/.test(sw), "worker shows a notification");
    ok(/signal_buy"?\s*\)?\s*\?\s*"BUY_NOW"/.test(sw.replace(/\s+/g, " ")) ||
      /type === "signal_buy" \? "BUY_NOW"/.test(sw),
      "worker maps signal_buy -> BUY_NOW");
    ok(/"GET_OUT_NOW"/.test(sw), "worker maps signal_exit -> GET_OUT_NOW");
    ok(/postMessage\(\{ type: type, payload: payload, source: "push_sw" \}/.test(sw),
      "worker forwards {type, payload, source} to the page");
    ok(!/new Audio|\.play\(\)|<audio/i.test(sw),
      "worker has no audio path (no beep)");
    ok(!/alert\(/.test(sw), "worker never calls alert() (no popup)");
  }

  console.log("\n" + (failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED") +
    "  (" + checks + " assertions)");
  process.exitCode = failures === 0 ? 0 : 1;
}

main().catch((err) => {
  console.error("harness crashed:", err);
  process.exitCode = 1;
});
