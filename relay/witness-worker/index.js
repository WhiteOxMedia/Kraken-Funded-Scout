// Relay witness - proves mine.xmrjackpot.com:3333 answers from Cloudflare's edge.
//
// This is the automated second vantage. The pool PC cannot prove its own ingress:
// every probe it runs shares one network (Cloudflare WARP, 104.28.x.x), and the
// datacenter ranges that hosted checkers use are the ones currently filtered.
// A Worker's outbound socket comes from Cloudflare's own network, which is the
// same family that already reaches the relay - and an unrelated one from ours.
//
//   GET /  ->  { verdict: "PASS" | "FAIL" | "INCONCLUSIVE", ... }
//
// The control (1.1.1.1:443) is measured here, on the Worker, so an
// INCONCLUSIVE result means "this Worker's networking is broken", never "the
// relay is down". The monitor reads this JSON with --witness-url.
import { connect } from "cloudflare:sockets";

const TARGET = { hostname: "mine.xmrjackpot.com", port: 3333 };
// NOT 1.1.1.1:443: Workers refuse raw TCP to Cloudflare's own HTTP endpoints
// ("looks like you might be trying to connect to a HTTP-based service"). A plain
// gopher host is what Cloudflare's own docs use as a TCP example.
const CONTROL = { hostname: "gopher.floodgap.com", port: 70 };
const TIMEOUT_MS = 7000;

const LOGIN =
  JSON.stringify({
    id: 1,
    jsonrpc: "2.0",
    method: "login",
    params: { login: "witness-worker", pass: "x", agent: "ingress-witness-worker/1" },
  }) + "\n";

function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function controlReachable() {
  let socket;
  try {
    socket = connect(CONTROL);
    await withTimeout(socket.opened, TIMEOUT_MS, "control");
    return { ok: true, error: null };
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    return { ok: false, error: message.slice(0, 200) };
  } finally {
    try {
      socket?.close();
    } catch {
      /* already closed */
    }
  }
}

async function probeStratum() {
  let socket;
  try {
    socket = connect(TARGET);
    await withTimeout(socket.opened, TIMEOUT_MS, "connect");

    const writer = socket.writable.getWriter();
    await withTimeout(writer.write(new TextEncoder().encode(LOGIN)), TIMEOUT_MS, "write");

    const reader = socket.readable.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const deadline = Date.now() + TIMEOUT_MS;
    while (Date.now() < deadline && !buffer.includes("\n")) {
      const { value, done } = await withTimeout(reader.read(), TIMEOUT_MS, "read");
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
    }

    if (!buffer) return { ok: false, detail: "connected, no reply" };
    const reply = JSON.parse(buffer.split("\n")[0]);
    const job = reply?.result?.job;
    return job
      ? { ok: true, detail: `job ${job.job_id ?? "?"}` }
      : { ok: false, detail: "reply carried no job" };
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    return { ok: false, detail: message.slice(0, 140) };
  } finally {
    try {
      socket?.close();
    } catch {
      /* already closed */
    }
  }
}

export default {
  async fetch(request) {
    // probe the relay first: the control only exists to interpret a failure
    const result = await probeStratum();
    const control = result.ok ? { ok: true, error: null } : await controlReachable();
    let verdict;
    if (result.ok) verdict = "PASS";
    else if (control.ok) verdict = "FAIL";
    else verdict = "INCONCLUSIVE";
    const detail = result.ok || control.ok
      ? result.detail
      : `${result.detail} (and the control failed too: ${control.error})`;

    const payload = {
      verdict,
      host: TARGET.hostname,
      port: TARGET.port,
      when: new Date().toISOString(),
      when_epoch: Math.floor(Date.now() / 1000),
      // colo is the edge that answered, e.g. "cloudflare-edge:LAX" - a witness
      // from Los Angeles is a different claim than one from Frankfurt.
      egress: "cloudflare-edge:" + (request.cf?.colo ?? "unknown"),
      control: control.ok,
      control_error: control.error,
      detail,
    };

    return new Response(JSON.stringify(payload, null, 2) + "\n", {
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  },
};
