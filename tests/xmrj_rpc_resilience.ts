/**
 * tests/xmrj_rpc_resilience.ts — THE RPC LAYER'S OWN TEST, WITHOUT A CHAIN
 *
 * WHY THIS IS IN tests/ AND NOT IN A NOTE: three devnet runs on Oct 27 were
 * killed by the public endpoint's 429 and by a confirmation that timed out at
 * 30 s, and nothing in this repo could tell "the endpoint is busy" from "the
 * client gave up too early". `tools/rpc_resilience.ts` is now the one place both
 * clients wait out a throttled endpoint, so it gets the same treatment as the
 * rest of this suite: a test that fails if the behaviour regresses.
 *
 * WHAT EACH STAGE PINS
 *   1. a 429 is retried and the eventual success is what the caller sees
 *   2. the backoff is exponential, and `Retry-After` overrides it (seconds AND
 *      an HTTP date), capped by the ceiling
 *   3. the retry is BOUNDED — an endpoint that never recovers gets the honest
 *      429 handed back, not an infinite loop and not a hang
 *   4. a PROGRAM error (HTTP 200 carrying a JSON-RPC `error`) is returned on the
 *      FIRST attempt: a defect must still fail loudly rather than be retried
 *      into looking like an outage
 *   5. a non-transport status (400) is returned immediately for the same reason
 *   6. network-level failures are retried, and when they persist the original
 *      error is thrown rather than replaced by something less useful
 *   7. the wall-clock budget stops the loop even when attempts remain
 *   8. `providerUrl` / `confirmTimeoutMs` read the environment and never guess
 *   9. the whole thing works THROUGH web3.js — a real `Connection` over the
 *      stub fetch returns a slot, which is the path the devnet runs use
 *
 * NO NETWORK, NO CHAIN: every stage drives a stub fetch with an injected clock,
 * so the suite stays deterministic and fast. The live proof is a separate thing
 * (see XMRJ_DEVNET_EXECUTION_REPORT.md).
 */

import { expect } from "chai";

import {
  confirmTimeoutMs,
  makeConnection,
  providerUrl,
  resilientFetch,
  resetRpcStats,
  retryAfterMs,
  rpcStats,
} from "../tools/rpc_resilience";

/** A stub `fetch` that walks a script of responses (the last one repeats). */
type Step =
  | { status: number; body?: string; headers?: Record<string, string>; throws?: undefined }
  | { throws: Error; status?: undefined };

function stubFetch(steps: Step[]): { fetch: (input: any, init?: any) => Promise<any>; calls: string[] } {
  const calls: string[] = [];
  const fetch = async (input: any): Promise<any> => {
    const index = calls.length;
    calls.push(typeof input === "string" ? input : String(input?.url ?? input));
    const step = steps[Math.min(index, steps.length - 1)];
    if (step.throws) throw step.throws;
    const body = step.body ?? "";
    return {
      status: step.status,
      ok: step.status >= 200 && step.status < 300,
      headers: step.headers ?? {},
      text: async () => body,
    };
  };
  return { fetch, calls };
}

/** An injected clock: no test in this file waits on real time. */
function fakeClock(): { now: () => number; sleep: (ms: number) => Promise<void>; slept: number[] } {
  let t = 0;
  const slept: number[] = [];
  return {
    now: () => t,
    slept,
    sleep: async (ms: number) => {
      slept.push(ms);
      t += ms;
    },
  };
}

/** The knobs every stage in this file uses: exact timing, captured log. */
function harness() {
  const clock = fakeClock();
  const logs: string[] = [];
  return {
    clock,
    logs,
    options: {
      sleep: clock.sleep,
      now: clock.now,
      jitterRatio: 0,
      log: (message: string) => logs.push(message),
    },
  };
}

// web3.js validates the envelope with superstruct and requires the id to be a
// STRING (it sends one), so the stub echoes one: `id: 1` fails validation with
// "Expected the value to satisfy a union of `type | type`", which would look
// like a bug in this layer rather than in the stub.
const OK_BODY = JSON.stringify({ jsonrpc: "2.0", id: "1", result: 515_833_000 });
const PROGRAM_ERROR_BODY = JSON.stringify({
  jsonrpc: "2.0",
  id: "1",
  error: { code: -32002, message: "Transaction simulation failed: Error Code: UnauthorizedAdmin" },
});
const URL = "https://api.devnet.solana.com";

describe("XMRJ RPC resilience (no chain, no network)", () => {
  beforeEach(() => resetRpcStats());

  it("1. waits out a 429 and hands the caller the eventual success", async () => {
    const h = harness();
    const stub = stubFetch([
      { status: 429, headers: {} },
      { status: 429, headers: {} },
      { status: 429, headers: {} },
      { status: 200, body: OK_BODY },
    ]);
    const wrapped = resilientFetch(stub.fetch, h.options);

    const response = await wrapped(URL, { method: "POST" });
    expect(response.status).to.equal(200);
    expect(await response.text()).to.equal(OK_BODY);
    expect(stub.calls.length, "the 429s must have been retried, not given up on").to.equal(4);
    expect(h.clock.slept, "exponential from 500ms, no jitter").to.deep.equal([500, 1000, 2000]);
    expect(rpcStats().absorbed429).to.equal(3);
  });

  it("2. honours Retry-After (seconds and an HTTP date), capped at the ceiling", async () => {
    expect(retryAfterMs({ "retry-after": "7" })).to.equal(7000);
    expect(retryAfterMs({}), "no header, no wait asked for").to.equal(null);
    const headers = { get: (name: string) => (name === "retry-after" ? "2" : null) };
    expect(retryAfterMs(headers)).to.equal(2000);
    const when = 1_700_000_000_000;
    const date = new Date(when + 4_000).toUTCString();
    expect(retryAfterMs({ "retry-after": date }, () => when)).to.equal(4000);

    // A Retry-After longer than the ceiling is capped at the ceiling: the
    // budget, not one header, is what bounds the wait.
    const h = harness();
    const stub = stubFetch([
      { status: 429, headers: { "retry-after": "600" } },
      { status: 200, body: OK_BODY },
    ]);
    const wrapped = resilientFetch(stub.fetch, { ...h.options, maxDelayMs: 30_000 });
    expect((await wrapped(URL, {})).status).to.equal(200);
    expect(h.clock.slept).to.deep.equal([30_000]);
  });

  it("3. BOUNDED: an endpoint that never recovers gets the honest 429 back, not a hang", async () => {
    const h = harness();
    const stub = stubFetch([{ status: 429, headers: {} }]);
    const wrapped = resilientFetch(stub.fetch, { ...h.options, maxAttempts: 4 });

    const response = await wrapped(URL, {});
    expect(response.status, "the caller must still be able to see the 429").to.equal(429);
    expect(stub.calls.length).to.equal(4);
    expect(h.clock.slept).to.deep.equal([500, 1000, 2000]);
    expect(rpcStats().gaveUp).to.equal(1);
    expect(h.logs.join("\n")).to.contain("handing it back to the caller");
  });

  it("4. NEVER retries a program error: a JSON-RPC error inside a 200 is returned at once", async () => {
    const h = harness();
    const stub = stubFetch([{ status: 200, body: PROGRAM_ERROR_BODY }]);
    const wrapped = resilientFetch(stub.fetch, h.options);

    const response = await wrapped(URL, {});
    expect(response.status).to.equal(200);
    expect(await response.text()).to.equal(PROGRAM_ERROR_BODY);
    expect(stub.calls.length, "a refusal by the program is not a busy endpoint").to.equal(1);
    expect(h.clock.slept).to.deep.equal([]);
    expect(rpcStats().retries).to.equal(0);
  });

  it("5. never retries a non-transport status either (400 is returned as it is)", async () => {
    const h = harness();
    const stub = stubFetch([{ status: 400, body: "bad request" }]);
    const wrapped = resilientFetch(stub.fetch, h.options);

    const response = await wrapped(URL, {});
    expect(response.status).to.equal(400);
    expect(stub.calls.length).to.equal(1);
    expect(h.clock.slept).to.deep.equal([]);
  });

  it("6. retries 5xx and network errors, and rethrows the original error when they persist", async () => {
    const h = harness();
    const stub = stubFetch([
      { status: 503, body: "" },
      { throws: new Error("ECONNRESET") },
      { status: 200, body: OK_BODY },
    ]);
    const wrapped = resilientFetch(stub.fetch, h.options);

    expect((await wrapped(URL, {})).status).to.equal(200);
    expect(stub.calls.length).to.equal(3);
    expect(h.clock.slept.length, "one pause per retry").to.equal(2);
    expect(rpcStats().absorbed5xx).to.equal(1);
    expect(rpcStats().absorbedNetwork).to.equal(1);

    const always = harness();
    const dead = stubFetch([{ throws: new Error("ENOTFOUND") }]);
    const doomed = resilientFetch(dead.fetch, { ...always.options, maxAttempts: 3 });
    let thrown: unknown = null;
    try {
      await doomed(URL, {});
    } catch (err) {
      thrown = err;
    }
    expect((thrown as Error)?.message, "the caller must see the transport error itself").to.equal(
      "ENOTFOUND",
    );
    expect(dead.calls.length).to.equal(3);
  });

  it("7. the wall-clock budget stops the loop even with attempts left to spend", async () => {
    const h = harness();
    const stub = stubFetch([{ status: 429, headers: {} }]);
    const wrapped = resilientFetch(stub.fetch, {
      ...h.options,
      maxAttempts: 50,
      budgetMs: 3000,
      baseDelayMs: 1000,
    });

    const response = await wrapped(URL, {});
    expect(response.status).to.equal(429);
    // 1000 + 2000 = 3000ms of waiting: the budget is reached at the third try.
    expect(stub.calls.length).to.be.at.most(4);
    expect(h.clock.now()).to.be.at.least(3000);
  });

  it("8. reads the endpoint and the confirmation window from the environment, and guesses neither", () => {
    expect(providerUrl({} as NodeJS.ProcessEnv)).to.equal(null);
    expect(providerUrl({ ANCHOR_PROVIDER_URL: "https://a" } as NodeJS.ProcessEnv)).to.equal("https://a");
    expect(
      providerUrl({ ANCHOR_PROVIDER_URL: "https://a", XMRJ_RPC_URL: "https://b" } as NodeJS.ProcessEnv),
      "XMRJ_RPC_URL wins, so the runbook can name the cluster it wants",
    ).to.equal("https://b");
    expect(providerUrl({ XMRJ_RPC_URL: "   " } as NodeJS.ProcessEnv)).to.equal(null);

    expect(confirmTimeoutMs({} as NodeJS.ProcessEnv)).to.equal(180_000);
    expect(confirmTimeoutMs({ XMRJ_CONFIRM_TIMEOUT_MS: "90000" } as NodeJS.ProcessEnv)).to.equal(90_000);
    expect(confirmTimeoutMs({ XMRJ_CONFIRM_TIMEOUT_MS: "nonsense" } as NodeJS.ProcessEnv)).to.equal(180_000);
  });

  it("9. works THROUGH web3.js: a real Connection over the stub fetch returns a slot", async () => {
    const h = harness();
    const stub = stubFetch([
      { status: 429, headers: {} },
      { status: 200, body: OK_BODY },
    ]);
    // web3.js's own five-attempt loop sees only the response this layer returns,
    // which is the point: the 429 never reaches the code that gives up on it.
    const connection = makeConnection(URL, { ...h.options, fetchImpl: stub.fetch });

    expect(await connection.getSlot()).to.equal(515_833_000);
    expect(stub.calls.length).to.equal(2);
    expect(h.logs.join("\n")).to.contain("retry 2/12");
  });
});
