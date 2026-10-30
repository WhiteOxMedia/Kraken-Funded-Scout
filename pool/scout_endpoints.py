"""
pool/scout_endpoints.py — XMR Jackpot Pool: scout identity + jackpot-cycle surfaces

Backing schema: schemas/pool_scout.sql
On-chain mirror: none — the xmrj_* programs are undeployed and uncalled here

Endpoints:
  POST   /api/scout/activate
  GET    /api/scout/status
  GET    /api/jackpot/status
"""

from __future__ import annotations

from pydantic import BaseModel, Field

import time

from . import database as db
from . import monero_wallet as xmr
try:                                   # the module runs flat (server.py) too
    import operator_config as oc
except ImportError:                    # pragma: no cover - package mode
    from . import operator_config as oc
from .security import require_auth
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class ScoutActivateBody(BaseModel):
    identity_token: str = Field(..., min_length=1)
    email: str | None = None
    device_fingerprint: str | None = None
    profile_id: str | None = None
    bind_to_pool_user_id: int | None = None


class ScoutActivateResult(BaseModel):
    ok: bool
    identity_id: int | None = None
    detail: str | None = None
    already_active: bool = False
    conflict: dict | None = None


def lookup_identity_by_token(token: str) -> dict | None:
    with db.get_db() as conn:
        r = conn.execute(
            "SELECT * FROM scout_identities WHERE identity_token = ?",
            (token,),
        ).fetchone()
        return dict(r) if r else None


def identity_owns_active_cycle(identity_id: int) -> dict | None:
    with db.get_db() as conn:
        r = conn.execute(
            "SELECT c.* FROM scout_jackpot_cycles c "
            "WHERE c.is_current = 1 "
            "AND EXISTS (SELECT 1 FROM scout_jackpot_entries e "
            "WHERE e.identity_id = ? AND e.onchain_entry_id = c.onchain_cycle_id)",
            (identity_id,),
        ).fetchone()
        return dict(r) if r else None


def identity_has_active_entry_in_cycle(
    identity_id: int, cycle_id: str
) -> dict | None:
    with db.get_db() as conn:
        r = conn.execute(
            "SELECT e.* FROM scout_jackpot_entries e "
            "JOIN scout_jackpot_cycles c "
            "  ON c.onchain_cycle_id = e.onchain_entry_id "
            "WHERE e.identity_id = ? "
            "  AND e.onchain_entry_id = ? "
            "  AND c.is_current = 1",
            (identity_id, cycle_id),
        ).fetchone()
        return dict(r) if r else None



def _social_proof():
    """The app's own social-proof coroutine.

    When server.py is started directly its module name is ``__main__``; the
    package copy (``pool.server``) would be a second, unstarted app, so prefer
    the already-running module before falling back to a normal package import.
    """
    import sys

    main = sys.modules.get("__main__")
    fn = getattr(main, "api_social_proof", None)
    if callable(fn):
        return fn
    try:
        from .server import api_social_proof
    except ImportError:  # direct ``python server.py`` layout
        from server import api_social_proof  # type: ignore[no-redef]
    return api_social_proof


def _pool_stats():
    from .jackpot_engine import get_pool_stats

    return get_pool_stats



SCOUT_PRO_PRICE_KEY = oc.SCOUT_PRO_PRICE_SETTING      # the ONE chain reader: oc
SCOUT_PRO_DAYS_KEY = oc.SCOUT_PRO_DAYS_SETTING
SCOUT_PRO_TTL_KEY = oc.SCOUT_PRO_CHECKOUT_MINUTES_SETTING
SCOUT_PRO_DEFAULT_PRICE = oc.SCOUT_PRO_DEFAULT_PRICE_USD
SCOUT_PRO_DEFAULT_DAYS = oc.SCOUT_PRO_DEFAULT_DAYS
SCOUT_PRO_DEFAULT_TTL_MIN = oc.SCOUT_PRO_DEFAULT_CHECKOUT_MIN
SCOUT_PRO_MIN_CONFIRMATIONS = 1


# The setting READERS (days, checkout TTL, price) live in
# pool/operator_config — the ONE reader per Pro chain (Oct 30).


def scout_pro_price():
    """Scout Pro's price as (usd, xmr) — the ONE chain reader is
    operator_config.pro_price(); this wrapper only keeps this module's
    historical name for its callers (checkout, order open). The product
    is sold at a DOLLAR price; the XMR amount is derived at the tracked
    rate each time a quote is rendered or an order is opened, so the
    homepage, the dashboard, the download page and the checkout cannot
    quote a different sum than the one the wallet is asked for.
    """
    return oc.pro_price()


def _scout_pro_payload(user_id):
    """Entitlement plus which path unlocked it.

    Two paths (Oct 24): the 100-day progression sets the lifetime flag,
    while a donation/checkout grant is time-boxed in scout_pro_until.
    """
    row = db.get_user(user_id) or {}
    until = float(row.get("scout_pro_until") or 0)
    lifetime = bool(int(row.get("scout_pro_lifetime") or 0))
    held = int(row.get("pending_entries") or 0)
    return {
        "active": lifetime or until > time.time(),
        "until": int(until) if until else 0,
        "lifetime": lifetime,
        "source": row.get("scout_pro_unlock_source") or "",
        #
        # "awarded" means a real ticket exists; a held entry is granted
        # but still waiting for a payout wallet to be minted onto.
        "entry_awarded": bool(int(row.get("scout_pro_entry_awarded") or 0))
        and not held,
        "entry_held": bool(held),
        "pending_entries": held,
    }


def _grant_scout_pro(user_id):
    days = oc.pro_days()
    # unlock_scout_pro also awards the one +1 jackpot entry per account;
    # with no payout wallet it HOLDS the entry on the account instead of
    # dropping it (claim_pending_entries mints it once a wallet lands).
    grant = db.unlock_scout_pro(user_id, "donation", grant_days=days)
    return grant.get("until", 0)


def _funding_module():
    """jackpot_funding under either entry layout (package or direct)."""
    try:
        from . import jackpot_funding as funding
    except ImportError:  # direct ``python server.py`` layout
        import jackpot_funding as funding  # type: ignore[no-redef]
    return funding


def _credit_order_donation(order):
    """Route a PAID donation into the jackpot (Oct 24).

    The memo promises the $95 donation goes into the daily jackpot and
    the checkout used it to stop at the entitlement: the buyer's XMR
    landed in the wallet and the pot never moved. credit_donation()
    raises the real base immediately and is idempotent per order id, so
    a replayed status poll can never fund the same checkout twice.

    A funding failure must not undo an entitlement the buyer already
    paid for, but it must not be silent either — it lands in the audit
    log where the admin ledger can see it and can be re-run.
    """
    try:
        return _funding_module().credit_donation(
            order["id"], order.get("amount_xmr"),
            sponsor_id=order.get("user_id"), source="scout_pro_checkout",
        )
    except Exception as e:
        print(f"[Funding] scout donation order #{order.get('id')} "
              f"not credited: {e}")
        try:
            db.add_audit_log(order.get("user_id"),
                             "scout_donation_credit_failed",
                             f"order #{order.get('id')}: {e}")
        except Exception:
            pass
        return {"credited": False, "reason": str(e)}


def _claim_held(user_id):
    """Mint any held unlock entry this account can now own.

    Called from the authenticated surfaces a returning member loads, so
    the entry moves the moment a wallet is attached, with no scheduler.
    """
    try:
        return db.claim_pending_entries(user_id)
    except Exception as e:
        print(f"[Progression] held-entry claim failed for {user_id}: {e}")
        return {"claimed": 0, "held": 0}


def _checkout_subaddress(user, request):
    """Create a fresh subaddress dedicated to this checkout.

    Deliberately NOT the account's payout subaddress: that one is polled by
    the hashrate deposit scanner, so reusing it would let an unrelated
    deposit of a similar amount be read as a Scout Pro payment (and vice
    versa). A per-order address keeps the two money flows disjoint.
    """
    if not xmr.rpc_available():
        return None, None
    addr, idx = xmr.create_subaddress(
        label="scout_pro_%s_%s" % (user["id"], int(time.time()))
    )
    if not addr or idx is None:
        return None, None
    try:
        db.add_audit_log(
            user["id"], "scout_checkout_address", "Subaddress #%s" % idx,
            request.client.host if request.client else "",
        )
    except Exception:
        pass
    return addr, idx


def _pay_uri(address, amount):
    amt = ("%.12f" % float(amount)).rstrip("0").rstrip(".")
    return "monero:%s?tx_amount=%s" % (address, amt)


def _order_payload(order):
    amount_xmr = float(order["amount_xmr"])
    try:
        amount_usd = round(amount_xmr * _funding_module().xmr_usd_rate(), 2)
    except Exception:
        amount_usd = None
    return {
        "order_id": int(order["id"]),
        "status": order["status"],
        "address": order["address"],
        "amount_xmr": amount_xmr,
        "amount_usd": amount_usd,
        "expires_at": int(order["expires_at"]),
        "txid": order.get("txid"),
        "confirmations": int(order.get("confirmations") or 0),
        "checkout_url": _pay_uri(order["address"], order["amount_xmr"]),
    }


def _matching_transfer(order):
    """Same subaddress, near-exact amount, at least one confirmation, and
    not already consumed by another checkout."""
    idx = order.get("subaddress_index")
    transfers = xmr.get_transfers(
        min_height=0, subaddr_indices=[idx] if idx is not None else None
    )
    if not transfers:
        return None
    price = float(order["amount_xmr"])
    best = None
    for t in transfers:
        if t.get("subaddr_index") != order.get("subaddress_index"):
            continue
        amount = float(t.get("amount") or 0)
        if amount + 1e-12 < price or amount > price * 1.05 + 1e-9:
            continue
        ts = float(t.get("timestamp") or 0)
        if ts and ts < float(order["created_at"]) - 120:
            continue
        if int(t.get("confirmations") or 0) < SCOUT_PRO_MIN_CONFIRMATIONS:
            continue
        if db.scout_purchase_txid_used(t["txid"]):
            continue
        if best is None or ts > float(best.get("timestamp") or 0):
            best = t
    return best


def _fulfil_order(order, txid, confirmations):
    """Mark paid and extend entitlement, at most once per order."""
    try:
        changed = db.mark_scout_purchase_paid(
            order["id"], txid, confirmations
        )
    except Exception:
        # A concurrent poll claimed the same txid first; that poll grants.
        return db.get_scout_purchase(order["id"])
    if changed:
        _grant_scout_pro(order["user_id"])
        # THE DONATION IS JACKPOT MONEY (Oct 24): the pot moves on the
        # same transition that grants Pro, never on a poll replay.
        _credit_order_donation(order)
    return db.get_scout_purchase(order["id"])

def register_scout_endpoints(app):
    @app.post("/api/scout/activate", response_model=ScoutActivateResult)
    async def api_scout_activate(body: ScoutActivateBody, request: Request):
        user = require_auth(request)
        token = (body.identity_token or "").strip()
        if not token:
            raise HTTPException(400, "identity_token is required")

        existing = lookup_identity_by_token(token)

        if existing:
            if existing.get("user_id") and int(existing["user_id"]) != int(user["id"]):
                return ScoutActivateResult(
                    ok=False,
                    detail="scout identity already owned by another account",
                    conflict={
                        "identity_id": int(existing["id"]),
                        "owned_by_user_id": int(existing["user_id"]),
                    },
                )
            with db.get_db() as conn:
                sets: list[str] = []
                vals: list[str | int | None] = []
                if body.email is not None:
                    sets.append("email = ?")
                    vals.append((body.email or "").strip() or None)
                if body.device_fingerprint is not None:
                    sets.append("device_fingerprint = ?")
                    vals.append((body.device_fingerprint or "").strip() or None)
                if body.profile_id is not None:
                    sets.append("profile_id = ?")
                    vals.append((body.profile_id or "").strip() or None)
                if body.bind_to_pool_user_id is not None:
                    sets.append("user_id = ?")
                    vals.append(int(body.bind_to_pool_user_id))
                if sets:
                    vals.append(int(existing["id"]))
                    conn.execute(
                        "UPDATE scout_identities SET "
                        + ", ".join(sets)
                        + " WHERE id = ?",
                        vals,
                    )
            return ScoutActivateResult(
                ok=True,
                identity_id=int(existing["id"]),
                detail="scout identity reconciled",
                already_active=bool(
                    identity_owns_active_cycle(int(existing["id"]))
                ),
            )

        target_user_id = body.bind_to_pool_user_id
        if target_user_id is None:
            target_user_id = int(user["id"])
        else:
            target_user = db.get_user(int(target_user_id))
            if not target_user:
                raise HTTPException(404, "bound pool user not found")
            if not target_user.get("wallet_address"):
                raise HTTPException(
                    403, "bound account has no wallet — a Scout needs a payout wallet"
                )

        if identity_owns_active_cycle(int(target_user_id)):
            return ScoutActivateResult(
                ok=False,
                detail="You already have an active Scout for this jackpot cycle.",
                already_active=True,
            )

        with db.get_db() as conn:
            cur = conn.execute(
                "INSERT INTO scout_identities "
                "(identity_token, email, device_fingerprint, profile_id, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    token,
                    (body.email or "").strip() or None,
                    (body.device_fingerprint or "").strip() or None,
                    (body.profile_id or "").strip() or None,
                    int(target_user_id) if target_user_id is not None else None,
                ),
            )
            identity_id = int(cur.lastrowid)

        return ScoutActivateResult(
            ok=True,
            identity_id=identity_id,
            detail="scout identity activated",
            already_active=False,
        )

    @app.post("/scout/activate", response_model=ScoutActivateResult)
    async def api_scout_activate_legacy(body: ScoutActivateBody, request: Request):
        """Legacy alias for POST /scout/activate (frontend fallback used by
        the current wiring checklist). Kept alongside /api/scout/activate so
        the scout_paywall_wire_patch fallback and older static builds keep
        working without a redirect.
        """
        return await api_scout_activate(body, request)

    @app.get("/api/scout/status")
    async def api_scout_status(request: Request):
        """Scout identity + jackpot-cycle surface (GET /scout/status).

        One read-only composition of the on-disk scout mirror tables
        (scout_identities / scout_jackpot_cycles / scout_jackpot_entries)
        plus the pool's jackpot stats and the landing social-proof next-
        drawing clock. Authenticated. Driven by the dashboard's two
        workflow panels: the customer-facing Scout purchase/activation
        explainer reads this; the Jackpot Cycle panel reads the
        cycle-level fields here.
        """
        user = require_auth(request)
        _claim_held(user["id"])
        stats = _pool_stats()()
        sp = await _social_proof()()

        wallet = user.get("wallet_address") or ""
        # READ-ONLY lookup (Oct 29, the honest auto-register): a status
        # poll never mints a miner row — activation and signed ingest
        # are the only creators.
        miner = db.get_miner_by_wallet(wallet) if wallet else None
        pending_tickets = int(db.get_miner_ticket_count(miner["id"])) if miner else 0

        with db.get_db() as conn:
            total_pending = int(
                conn.execute(
                    "SELECT COUNT(*) FROM tickets WHERE drawing_id IS NULL"
                ).fetchone()[0]
            )

        identity = lookup_identity_by_token(wallet) if wallet else None
        active_cycle = None
        active_entry = None
        if identity:
            active_cycle = identity_owns_active_cycle(int(identity["id"]))
            if active_cycle:
                active_entry = identity_has_active_entry_in_cycle(
                    int(identity["id"]), active_cycle["onchain_cycle_id"]
                )

        return {
            "jackpot_xmr": stats.get("jackpot_xmr", 0.0),
            "jackpot_usd": stats.get("jackpot_usd", 0.0),
            # the conversion behind jackpot_usd (Oct 26): the dashboard
            # sentence says what rate it was converted at, and whether
            # that rate is old or the fallback
            "xmr_usd_rate_info": stats.get("xmr_usd_rate_info", {}),
            "next_drawing_epoch": int(sp.get("next_drawing_ts", 0) or 0),
            "next_drawing_seconds_remaining": int(
                max(0, (sp.get("next_drawing_ts") or 0) - (sp.get("now") or 0))
            ),
            "total_pending_entries": total_pending,
            "your_pending_tickets": pending_tickets,
            "your_total_tickets": pending_tickets,
            "scout_pro": _scout_pro_payload(user["id"]),
            "scout": (
                {
                    "identity_id": int(identity["id"]),
                    "identity_token": identity["identity_token"],
                    "has_active_cycle": bool(active_cycle is not None),
                    "has_active_entry": bool(active_entry is not None),
                }
                if identity
                else None
            ),
        }

    @app.get("/api/scout/pro")
    async def api_scout_pro(request: Request):
        """THE BROWSER'S ENTITLEMENT READ (Oct 24).

        push_client.js gates every alert on a localStorage flag, and a
        flag nobody sets is a paywall that never opens. This is the
        cheap, authenticated source scout_pro_bridge.js caches: the
        verdict plus its expiry and unlock path — no pool stats, no
        social proof, no identity walk, so it is safe to poll.
        """
        user = require_auth(request)
        _claim_held(user["id"])
        return _scout_pro_payload(user["id"])

    @app.get("/scout/status")
    async def api_scout_status_legacy(request: Request):
        """Legacy alias for GET /scout/status (frontend fallback + current
        wiring checklist). Kept in addition to /api/scout/status so the
        scout_paywall_wire_patch fallback and any older static builds keep
        working without a redirect.
        """
        return await api_scout_status(request)

    @app.post("/scout/purchase")
    async def api_scout_purchase_legacy(request: Request):
        """Scout Pro donation checkout.

        donate_unlock (default): open/reuse a pending order and return the
        Monero address, exact amount, pay URI, and expiry.
        status: report the order; while pending, look for the matching
        on-chain transfer and fulfil it once it has one confirmation.
        confirm (or a legacy payment_proof): verify a specific txid.
        """
        user = require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        action = str(body.get("action") or "").strip().lower()
        order_id = body.get("order_id")
        _claim_held(user["id"])

        if action in ("status", "check") or (not action and order_id):
            try:
                order = db.get_scout_purchase(int(order_id))
            except (TypeError, ValueError):
                order = None
            if not order or int(order["user_id"]) != int(user["id"]):
                return JSONResponse(
                    {"status": "error", "message": "checkout not found"},
                    status_code=404,
                )
            if order["status"] == "pending":
                # Check the chain before expiring: a payment that lands late
                # (after expires_at but to this order's address) is still
                # the buyer's money and must fulfil the order.
                transfer = _matching_transfer(order)
                if transfer:
                    order = _fulfil_order(
                        order, transfer["txid"],
                        transfer.get("confirmations") or 0,
                    ) or order
                elif float(order["expires_at"]) <= time.time():
                    db.expire_scout_purchase(order["id"])
                    order = db.get_scout_purchase(order["id"]) or order
            return {
                **_order_payload(order),
                "scout_pro": _scout_pro_payload(user["id"]),
            }

        txid = str(body.get("payment_proof") or body.get("txid") or "").strip()
        if action in ("confirm", "verify") or (not action and txid):
            if not txid:
                return JSONResponse(
                    {"status": "error",
                     "message": "payment_proof (txid) is required"},
                    status_code=400,
                )
            transfer = xmr.get_transfer_by_txid(txid)
            if not transfer:
                return JSONResponse(
                    {"status": "error", "message": "transaction not found"},
                    status_code=404,
                )
            order = db.get_open_scout_purchase(user["id"])
            if not order:
                return JSONResponse(
                    {"status": "error",
                     "message": "no open checkout - start one first"},
                    status_code=409,
                )
            if db.scout_purchase_txid_used(txid):
                return JSONResponse(
                    {"status": "error", "message": "transaction already used"},
                    status_code=409,
                )
            ok_addr = transfer.get("subaddr_index") == order.get("subaddress_index")
            enough = (float(transfer.get("amount") or 0) + 1e-12
                      >= float(order["amount_xmr"]))
            confirmed = (int(transfer.get("confirmations") or 0)
                         >= SCOUT_PRO_MIN_CONFIRMATIONS)
            if not (ok_addr and enough and confirmed):
                return JSONResponse(
                    {"status": "error",
                     "message": "transaction does not match this checkout"},
                    status_code=400,
                )
            order = _fulfil_order(
                order, txid, transfer.get("confirmations") or 0
            ) or order
            return {
                **_order_payload(order),
                "scout_pro": _scout_pro_payload(user["id"]),
            }

        if not xmr.rpc_available():
            return JSONResponse(
                {"status": "error",
                 "message": "Monero wallet RPC is unavailable - checkout cannot open right now."},
                status_code=503,
            )
        _price_usd, price = scout_pro_price()
        if price <= 0:
            return JSONResponse(
                {"status": "error", "message": "Scout Pro price is not configured."},
                status_code=503,
            )
        order = db.get_open_scout_purchase(user["id"])
        if not order:
            address, idx = _checkout_subaddress(user, request)
            if not address:
                return JSONResponse(
                    {"status": "error",
                     "message": "Could not create a Monero address for this checkout."},
                    status_code=503,
                )
            ttl = oc.pro_checkout_minutes()
            order = db.create_scout_purchase(
                user["id"],
                body.get("identity_wallet") or user.get("wallet_address") or "",
                price,
                address,
                idx,
                time.time() + ttl * 60,
            )
        if not order:
            return JSONResponse(
                {"status": "error", "message": "Could not open checkout."},
                status_code=500,
            )
        return {
            **_order_payload(order),
            "scout_pro": _scout_pro_payload(user["id"]),
            "confirmations_required": SCOUT_PRO_MIN_CONFIRMATIONS,
        }

    @app.get("/api/jackpot/status")
    async def api_jackpot_status(request: Request):
        user = require_auth(request)
        _claim_held(user["id"])
        stats = _pool_stats()()
        sp = await _social_proof()()

        wallet = user.get("wallet_address") or ""
        # READ-ONLY lookup (Oct 29, the honest auto-register): a status
        # poll never mints a miner row — activation and signed ingest
        # are the only creators.
        miner = db.get_miner_by_wallet(wallet) if wallet else None
        pending_tickets = int(db.get_miner_ticket_count(miner["id"])) if miner else 0

        with db.get_db() as conn:
            total_pending = int(
                conn.execute(
                    "SELECT COUNT(*) FROM tickets WHERE drawing_id IS NULL"
                ).fetchone()[0]
            )

        identity = lookup_identity_by_token(wallet) if wallet else None
        active_cycle = None
        active_entry = None
        if identity:
            active_cycle = identity_owns_active_cycle(int(identity["id"]))
            if active_cycle:
                active_entry = identity_has_active_entry_in_cycle(
                    int(identity["id"]), active_cycle["onchain_cycle_id"]
                )

        return {
            "jackpot_xmr": stats.get("jackpot_xmr", 0.0),
            "jackpot_usd": stats.get("jackpot_usd", 0.0),
            # the conversion behind jackpot_usd (Oct 26): the dashboard
            # sentence says what rate it was converted at, and whether
            # that rate is old or the fallback
            "xmr_usd_rate_info": stats.get("xmr_usd_rate_info", {}),
            "next_drawing_epoch": int(sp.get("next_drawing_ts", 0) or 0),
            "next_drawing_seconds_remaining": int(
                max(0, (sp.get("next_drawing_ts") or 0) - (sp.get("now") or 0))
            ),
            "total_pending_entries": total_pending,
            "your_pending_tickets": pending_tickets,
            "your_total_tickets": pending_tickets,
            "scout_pro": _scout_pro_payload(user["id"]),
            "scout": (
                {
                    "identity_id": int(identity["id"]),
                    "identity_token": identity["identity_token"],
                    "has_active_cycle": bool(active_cycle is not None),
                    "has_active_entry": bool(active_entry is not None),
                }
                if identity
                else None
            ),
        }
