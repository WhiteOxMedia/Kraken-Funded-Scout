/**
 * tools/rpc_resilience.ts — ONE RPC LAYER FOR BOTH CLIENTS
 *
 * WHY THIS EXISTS. Three devnet runs on Oct 27 were stopped by the free public
 * endpoint, not by the programs:
 *
 *   Error: 429 Too Many Requests: {"jsonrpc":"2.0","error":{"code": 429,
 *     "message":"Connection rate limits exceeded"}}
 *   TransactionExpiredTimeoutError: Transaction was not confirmed in 30.00 seconds
 *
 * Read out of the installed @solana/web3.js 1.99 rather than guessed, there are
 * two separate limits behind those two messages:
 *
 *   1. `Connection.callServer` retries an HTTP 429 FIVE times — 500, 1000, 2000,
 *      4000, 8000 ms — and then throws the 429 as a plain Error. Fifteen seconds
 *      is shorter than the window this endpoint enforces on a host that has just
 *      run a load harness, so the built-in retry runs out and the run dies with
 *      the wall of "Retrying after … delay" lines that fills these transcripts.
 *   2. `confirmTransaction` gives up after `_confirmTransactionInitialTimeout`
 *      (60 s by default, 30 s on the legacy path this repo hits). A confirmation
 *      request that is being throttled can cross that line and throw
 *      `TransactionExpiredTimeoutError` even though the transaction is landing
 *      perfectly well.
 *
 * WHAT THIS MODULE DOES, in one place so both clients behave the same way:
 *
 *   * `providerUrl()` — XMRJ_RPC_URL, then ANCHOR_PROVIDER_URL, then null. A run
 *     can be aimed at a second endpoint (devnet.rpcpool.com mirrors the same
 *     cluster) without editing a file, and nothing ever GUESSES a cluster.
 *   * `resilientFetch()` — a `fetch` installed as the Connection's own `fetch`
 *     that retries ONLY transport-level failures: HTTP 429, HTTP 5xx, and
 *     network errors. It backs off exponentially with jitter, honours
 *     `Retry-After`, and is bounded twice over — by an attempt count AND by a
 *     wall-clock budget — so it cannot loop for ever. Because it is the
 *     Connection's fetch, web3's own five-attempt loop never sees a 429 it would
 *     give up on; when this layer does eventually give up it hands the last 429
 *     back, so the error the operator sees is still the endpoint's.
 *
 *     PROGRAM ERRORS ARE NEVER RETRIED. A program refusal arrives as a JSON-RPC
 *     `error` inside an HTTP 200 response, which this layer returns untouched on
 *     the first attempt. A defect must still fail loudly, and a 400/403/404 is
 *     not a busy endpoint either — only the statuses above are retried.
 *   * `makeConnection()` / `makeProvider()` — consistent commitment, the
 *     resilient fetch, and a confirmation window (`XMRJ_CONFIRM_TIMEOUT_MS`,
 *     default 180 s) that outlasts a throttled confirmation instead of throwing
 *     at 30 s.
 *
 * EVERY RETRY IS LOGGED to stderr as it happens, and the counts are available
 * via `rpcStats()` for the run's own report: a transcript should show that the
 * throttling was SURVIVED, not that it was quietly hidden.
 */

import * as anchor from "@anchor-lang/core";

/** What a `fetch` looks like to this module. Deliberately loose: web3.js's own
 *  types are not exported for reuse, and the tests feed plain stubs in. */
export type FetchLike = (input: any, init?: any) => Promise<any>;

export interface ResilientFetchOptions {
  /** Total attempts per request, including the first. */
  maxAttempts?: number;
  /** First backoff step; each subsequent step doubles it. */
  baseDelayMs?: number;
  /** Ceiling for one backoff step (and for a `Retry-After` that asks for more). */
  maxDelayMs?: number;
  /** Wall-clock ceiling for one logical request, across all its attempts. */
  budgetMs?: number;
  /** Statuses worth a second attempt. 4xx other than 429 are NOT. */
  retryStatuses?: number[];
  /** Fraction of the delay to add/subtract as jitter. 0 makes a test exact. */
  jitterRatio?: number;
  /** Injectable for tests: no test should wait on real time. */
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  log?: (message: string) => void;
}

export const RETRYABLE_STATUS = [429, 500, 502, 503, 504];

export const RESILIENCE_DEFAULTS = {
  maxAttempts: 12,
  baseDelayMs: 500,
  maxDelayMs: 30_000,
  budgetMs: 180_000,
  jitterRatio: 0.2,
};

/** Counters for the run's own report. Reset with `resetRpcStats()`. */
const STATS = {
  requests: 0,
  retries: 0,
  absorbed429: 0,
  absorbed5xx: 0,
  absorbedNetwork: 0,
  gaveUp: 0,
};

export function rpcStats(): typeof STATS & { summary: string } {
  const summary =
    `${STATS.requests} RPC requests, ${STATS.retries} retried ` +
    `(${STATS.absorbed429} throttled, ${STATS.absorbed5xx} server-side, ` +
    `${STATS.absorbedNetwork} network), ${STATS.gaveUp} given up on`;
  return { ...STATS, summary };
}

export function resetRpcStats(): void {
  STATS.requests = 0;
  STATS.retries = 0;
  STATS.absorbed429 = 0;
  STATS.absorbed5xx = 0;
  STATS.absorbedNetwork = 0;
  STATS.gaveUp = 0;
}

/**
 * The endpoint this run should use, in the order the runbook documents.
 * `null` when neither is set — callers refuse to run rather than guess a
 * cluster (the same rule the stress harness already enforced).
 */
export function providerUrl(env: NodeJS.ProcessEnv = process.env): string | null {
  const url = env.XMRJ_RPC_URL ?? env.ANCHOR_PROVIDER_URL;
  return url && url.trim() !== "" ? url.trim() : null;
}

/** The confirmation window, in ms — long enough to outlast a throttled confirmation. */
export function confirmTimeoutMs(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env.XMRJ_CONFIRM_TIMEOUT_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : 180_000;
}

/**
 * `Retry-After` is either a number of seconds or an HTTP date. Returns the wait
 * it asks for in ms, or null when the header is absent or unreadable.
 */
export function retryAfterMs(headers: any, now: () => number = Date.now): number | null {
  if (!headers) return null;
  const raw =
    typeof headers.get === "function" ? headers.get("retry-after") : headers["retry-after"];
  if (raw === undefined || raw === null || String(raw).trim() === "") return null;
  const seconds = Number(String(raw).trim());
  if (Number.isFinite(seconds)) return Math.max(0, Math.round(seconds * 1000));
  const at = Date.parse(String(raw));
  return Number.isFinite(at) ? Math.max(0, at - now()) : null;
}

const realSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Wrap a `fetch` so that a busy endpoint is waited out instead of ending the run.
 *
 * The loop is deliberately small: attempt, inspect the STATUS, and either return
 * the response (success, a program error, or a non-retryable status) or pause and
 * try again. Two independent bounds stop it: `maxAttempts` and `budgetMs`.
 */
export function resilientFetch(baseFetch: FetchLike, options: ResilientFetchOptions = {}): FetchLike {
  const maxAttempts = options.maxAttempts ?? RESILIENCE_DEFAULTS.maxAttempts;
  const baseDelayMs = options.baseDelayMs ?? RESILIENCE_DEFAULTS.baseDelayMs;
  const maxDelayMs = options.maxDelayMs ?? RESILIENCE_DEFAULTS.maxDelayMs;
  const budgetMs = options.budgetMs ?? RESILIENCE_DEFAULTS.budgetMs;
  const retryStatuses = options.retryStatuses ?? RETRYABLE_STATUS;
  const jitterRatio = options.jitterRatio ?? RESILIENCE_DEFAULTS.jitterRatio;
  const sleep = options.sleep ?? realSleep;
  const now = options.now ?? Date.now;
  const log = options.log ?? ((message: string) => console.error(message));

  const host = (input: any): string => {
    try {
      return new URL(typeof input === "string" ? input : input?.url ?? String(input)).host;
    } catch {
      return String(input);
    }
  };

  return async (input: any, init?: any): Promise<any> => {
    STATS.requests += 1;
    const started = now();
    let attempt = 0;
    let lastResponse: any = null;

    for (;;) {
      attempt += 1;
      let failure: unknown = null;
      try {
        lastResponse = await baseFetch(input, init);
      } catch (err) {
        failure = err;
      }

      if (failure !== null) {
        // A network-level failure (reset socket, DNS, TLS) is transport, not
        // protocol: the same treatment as a 503.
        if (attempt >= maxAttempts || now() - started >= budgetMs) {
          STATS.gaveUp += 1;
          log(`[rpc] ${host(input)} unreachable after ${attempt} attempts — giving up: ${failure}`);
          throw failure;
        }
        STATS.retries += 1;
        STATS.absorbedNetwork += 1;
        await pause(attempt, null, `network error: ${(failure as Error)?.message ?? failure}`);
        continue;
      }

      if (!retryStatuses.includes(lastResponse.status)) {
        return lastResponse;
      }

      const retryAfter = retryAfterMs(lastResponse.headers, now);
      if (attempt >= maxAttempts || now() - started >= budgetMs) {
        STATS.gaveUp += 1;
        log(
          `[rpc] ${host(input)} still HTTP ${lastResponse.status} after ${attempt} attempts ` +
            `and ${now() - started}ms — handing it back to the caller`,
        );
        return lastResponse;
      }

      STATS.retries += 1;
      if (lastResponse.status === 429) STATS.absorbed429 += 1;
      else STATS.absorbed5xx += 1;
      await pause(attempt, retryAfter, `HTTP ${lastResponse.status}`);
    }

    /**
     * Wait before the next attempt: exponential from `baseDelayMs`, jittered,
     * never above `maxDelayMs`, and never shorter than what `Retry-After` asked
     * for (capped at the same ceiling — the budget is what really bounds us).
     */
    async function pause(attempt: number, retryAfter: number | null, why: string): Promise<void> {
      const exponential = Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1));
      const jittered = Math.round(exponential * (1 + jitterRatio * (Math.random() * 2 - 1)));
      const asked = retryAfter === null ? 0 : Math.min(maxDelayMs, retryAfter);
      const delay = Math.max(1, jittered, asked);
      log(
        `[rpc] ${host(input)} ${why} — retry ${attempt + 1}/${maxAttempts} in ${delay}ms` +
          (retryAfter !== null ? ` (Retry-After asked ${retryAfter}ms)` : ""),
      );
      await sleep(delay);
    }
  };
}

/**
 * A Connection that waits out a throttled endpoint. `fetchImpl` exists so a test
 * can drive the whole layer with a stub; production passes nothing.
 */
export function makeConnection(
  url: string,
  options: ResilientFetchOptions & { commitment?: anchor.web3.Commitment; fetchImpl?: FetchLike } = {},
): anchor.web3.Connection {
  const base = options.fetchImpl ?? ((globalThis as any).fetch as FetchLike);
  if (typeof base !== "function") {
    throw new Error("no global fetch available and no fetchImpl passed");
  }
  return new anchor.web3.Connection(url, {
    commitment: options.commitment ?? "confirmed",
    fetch: resilientFetch(base, options),
    confirmTransactionInitialTimeout: confirmTimeoutMs(),
  });
}

/**
 * A payer wallet from ANCHOR_WALLET, else `~/.config/solana/id.json` — the same
 * rule Anchor's own `NodeWallet.local()` uses, spelled out here so both clients
 * agree and a missing keypair says so before anything is signed.
 */
export function makeWallet(env: NodeJS.ProcessEnv = process.env): anchor.Wallet {
  const fs = require("fs") as typeof import("fs");
  const path = require("path") as typeof import("path");
  const walletPath =
    env.ANCHOR_WALLET ??
    path.join(env.HOME ?? env.USERPROFILE ?? "", ".config", "solana", "id.json");
  if (!fs.existsSync(walletPath)) {
    throw new Error(`no keypair at ${walletPath} — set ANCHOR_WALLET or create one`);
  }
  const secret = Uint8Array.from(
    JSON.parse(fs.readFileSync(walletPath, "utf-8")) as number[],
  );
  return new anchor.Wallet(anchor.web3.Keypair.fromSecretKey(secret));
}

/**
 * The provider both clients use: the resilient connection, the wallet, and the
 * longer confirmation window. Throws with the runbook's wording when no endpoint
 * is set, because a run must never inherit a stray cluster.
 */
export function makeProvider(
  options: ResilientFetchOptions & { url?: string; wallet?: anchor.Wallet; fetchImpl?: FetchLike } = {},
): { connection: anchor.web3.Connection; wallet: anchor.Wallet; provider: anchor.AnchorProvider } {
  const url = options.url ?? providerUrl();
  if (!url) {
    throw new Error(
      "no XMRJ_RPC_URL / ANCHOR_PROVIDER_URL set — this run will not guess a cluster. " +
        "Set one (devnet: https://api.devnet.solana.com) and run again.",
    );
  }
  const connection = makeConnection(url, options);
  const wallet = options.wallet ?? makeWallet();
  const provider = new anchor.AnchorProvider(connection, wallet, { commitment: "confirmed" });
  return { connection, wallet, provider };
}
