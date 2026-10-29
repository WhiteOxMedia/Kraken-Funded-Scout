"""vrsc_relay.py — THE VERUS STRATUM RELAY (Oct 28).

WHY THIS EXISTS (the Oct 28 pivots): the region-branched download ships a
Verus miner because Monero routes are unreachable from the branch's
territories. The Verus miner cannot push hashrate at mine.xmrjackpot.com
— so the operator runs THIS relay on their own VPS, the generated .bat
points at XMRJ_VRSC_STRATUM (this relay's host:port), and Verus shares
earn entries through the SAME pipeline the Monero proxy's shares do:
allocate_free_tickets per accepted share. Payouts themselves flow Verus
coin-directly-to-wallet from the upstream pool; the raffle is the only
thing this relay owns.

WHAT IT IS BYTES-WISE: a dumb, faithful pipe. Every byte a miner sends
is forwarded to the upstream stratum exactly as received; every byte
the upstream sends, is forwarded back exactly. Parse errors NEVER
break the pipe — an unparseable frame still relays. The pool itself
pays regardless, so the only thing a parse miss can cost is ONE
share's entry in the raffle.

HOW AN ENTRY IS COUNTED: the miner's authorize carries the wallet
("address.worker" or plain address). Mining.submit replies with the same
JSON `id` the miner used; when the corresponding reply says result=true,
one accepted share exists, and ONE unit flows into:
    db.get_or_create_miner(wallet)      -> the entry ledger keys on this
    db.update_miner_stats(wallet, 1)    -> the pool pages see the rig
    db.shares_valid incremented         -> engine.allocate_free_tickets
So the "Every N valid shares = 1 free entry" banner in the .bat is the
same engine.entry_unit() promise the Monero side makes, honored by the
same code path.

RUN IT ON THE OPERATOR'S BOX (not the pool host):
    python vrsc_relay.py --listen-port 3957 --stratum na.luckpool.net:3956
            --db path/to/jackpot.db
It stays a plain TCP server: every miner who can TCP-connect is a
customer; there is no auth, no state, no lock-in, exactly like the pool.
"""
import argparse
import asyncio
import json
import sys
import time


# ── the log: best-effort, never load-bearing ─────────────────────────
def _log(*parts):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(parts)),
          flush=True)


# ── the entry pipeline, fail-soft at every layer ───────────────────────
def record_accepted_share(wallet, worker):
    """One accepted Verus share -> the same entry pipeline Monero uses.
    NEVER raises: a counter outage must not cost anything but the count.
    Returns True when the entry pipeline accepted the share."""
    try:
        import database as db          # pool/database.py sits beside us
        import jackpot_engine as engine
        miner = db.get_or_create_miner(wallet, worker or "")
        if miner:
            # WHICH COIN THIS BOT MINES (Oct 28): the drawing-side payout
            # branch reads miners.coin to decide the coin a win pays in —
            # this pipeline is the VRSC truth's one writer.
            db.set_miner_coin(miner["wallet_address"], "vrsc")
            db.update_miner_stats(miner["wallet_address"], 1)
            engine.allocate_free_tickets(miner["id"], 1)
        return True
    except Exception as exc:
        _log("[pipeline] dropped share entry for %s: %s" % (wallet, exc))
        return False


class RelaySession:
    """One miner connection. Two pipes, one submit id -> wallet hop."""

    def __init__(self, client_reader, client_writer,
                 stratum_host, stratum_port):
        self.client_reader = client_reader
        self.client_writer = client_writer
        self.stratum_host = stratum_host
        self.stratum_port = stratum_port
        self.upstream_reader = None
        self.upstream_writer = None
        # submit ids this miner sent; replies share the id
        self._submit_ids = {}   # id -> wallet/worker pair
        self.wallet = None
        self.worker = None

    def _on_client_line(self, raw: bytes):
        """From the miner: track authorize + submit ids. Relays regardless."""
        try:
            msg = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        method = msg.get("method")
        mid = msg.get("id")
        if method == "mining.authorize":
            user = str(msg.get("params", [""])[0]) if msg.get("params") else ""
            if "." in user:
                wallet, _, worker = user.partition(".")
            else:
                wallet, worker = user, ""
            if wallet:
                self.wallet, self.worker = wallet, worker
        elif method == "mining.submit":
            if isinstance(mid, (int, str)):
                self._submit_ids[mid] = (self.wallet, self.worker)

    def _on_upstream_line(self, raw: bytes):
        """From the stratum: a submit reply with the miner's id and
        result=true counts one accepted share. Relays regardless."""
        rid = None
        result = None
        try:
            msg = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return          # keepalive noise, a broken frame, anything
        if not isinstance(msg, dict):
            return
        rid = msg.get("id")
        result = msg.get("result")
        pair = self._submit_ids.pop(rid, None)
        if pair and result is True:
            wallet, worker = pair
            if wallet:
                record_accepted_share(wallet, worker)

    async def _pipe(self, src, dst, side):
        """One direction of the dumb pipe, with the counter riding
        alongside. `side` is 'down' (miner -> pool) or 'up'."""
        try:
            while True:
                line = await src.readline()
                if not line:
                    break
                (self._on_client_line if side == "down"
                 else self._on_upstream_line)(line)
                dst.write(line)
                await dst.drain()
        except Exception:
            pass            # anything the OS raised: the pipe just ends
        finally:
            try:
                dst.close()
            except Exception:
                pass

    async def open_upstream(self):
        self.upstream_reader, self.upstream_writer = await asyncio.open_connection(
            self.stratum_host, self.stratum_port)

    async def run(self):
        await self.open_upstream()
        down = self._pipe(self.client_reader, self.upstream_writer, "down")
        up = self._pipe(self.upstream_reader, self.client_writer, "up")
        try:
            await asyncio.gather(down, up)
        except Exception:
            pass
        finally:
            for w in (self.client_writer, self.upstream_writer):
                try:
                    w.close()
                except Exception:
                    pass


async def serve(listen_port, stratum_host, stratum_port):
    """Accept miners forever. A connection that dies dies; the server
    itself does not, whatever a miner does to it."""
    async def handle(reader, writer):
        session = RelaySession(reader, writer, stratum_host, stratum_port)
        try:
            await session.run()
        finally:
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, "0.0.0.0", listen_port)
    _log("vrsc_relay serving 0.0.0.0:%d -> %s:%s"
         % (listen_port, stratum_host, stratum_port))
    async with server:
        await server.serve_forever()


def main(argv=None):
    p = argparse.ArgumentParser(description="Verus stratum relay + entry counter")
    p.add_argument("--listen-port", type=int, default=3957)
    p.add_argument("--stratum", default="na.luckpool.net:3956")
    p.add_argument("--db", default=None,
                   help="path to jackpot.db (e.g. pool/jackpot.db on the "
                        "pool host copy mounted to the VPS box)")
    args = p.parse_args(argv)

    if args.db:
        sys.path.insert(0, "pool")
        try:
            import database as db
            db.DB_PATH = db.Path(args.db)
            db.init_db()
            _log("entry ledger: %s" % args.db)
        except Exception as exc:
            _log("[setup] db path IGNORED (relay runs WITHOUT entries): %s"
                 % exc)

    host, _, port = args.stratum.partition(":")
    try:
        asyncio.run(serve(args.listen_port, host, int(port or 3956)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
