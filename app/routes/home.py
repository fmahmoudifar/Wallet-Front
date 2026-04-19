from collections import defaultdict
from decimal import Decimal
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Blueprint, jsonify, render_template, session

from app.services.user_scope import filter_records_by_user
from config import API_URL, aws_auth

# Reuse the same currency/FX helpers used by the Crypto page
from .crypto import (
    _get_fx_rate,
    _get_user_base_currency,
    _normalize_currency,
)

# Reuse stock scaling helpers for transaction totals
from .stock import (
    _scale_minor_currency,
    _to_decimal as _to_decimal_stock,
)

home_bp = Blueprint("home", __name__, url_prefix="/")

_SETTINGS_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "settings_defaults.json")
with open(_SETTINGS_DEFAULTS_PATH, "r") as _f:
    _SETTINGS_DEFAULTS = json.load(_f)

# In-process cache for the Overview page to avoid recomputing heavy totals on every refresh.
# NOTE: This is per-process (per gunicorn worker) and resets on restart.
_OVERVIEW_CTX_CACHE: dict[tuple[str, str], dict] = {}


def _dashboard_cache_ttl_seconds() -> int:
    # In Codespaces/dev, keep a short cache by default to improve dashboard responsiveness
    # while still refreshing frequently. Can be overridden with OVERVIEW_CACHE_TTL_SECONDS.
    try:
        in_codespaces = str(os.getenv("CODESPACES") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
        if in_codespaces and os.getenv("OVERVIEW_CACHE_TTL_SECONDS") is None:
            return 10
    except Exception:
        pass

    try:
        return max(0, int((os.getenv("OVERVIEW_CACHE_TTL_SECONDS") or "20").strip()))
    except Exception:
        return 20


def _truthy_env(val: str | None) -> bool:
    s = str(val or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _dashboard_cache_get(user_id: str, base_currency: str):
    ttl = _dashboard_cache_ttl_seconds()
    if ttl <= 0:
        return None
    key = (str(user_id or "").strip(), str(base_currency or "").strip().upper())
    now = time.time()
    cached = _OVERVIEW_CTX_CACHE.get(key)
    if cached and (now - float(cached.get("ts") or 0.0) < ttl):
        return cached.get("ctx")
    return None


def _dashboard_cache_set(user_id: str, base_currency: str, ctx: dict):
    ttl = _dashboard_cache_ttl_seconds()
    if ttl <= 0:
        return
    key = (str(user_id or "").strip(), str(base_currency or "").strip().upper())
    _OVERVIEW_CTX_CACHE[key] = {"ts": time.time(), "ctx": ctx}


def _dashboard_cache_invalidate_user(user_id: str) -> None:
    uid = str(user_id or "").strip()
    if not uid:
        return
    keys = [k for k in list(_OVERVIEW_CTX_CACHE.keys()) if isinstance(k, tuple) and len(k) >= 1 and str(k[0]) == uid]
    for k in keys:
        _OVERVIEW_CTX_CACHE.pop(k, None)


def _api_list(path: str, *, user_id: str, list_key: str, timeout: int = 12) -> list:
    try:
        resp = requests.get(
            f"{API_URL}/{path.lstrip('/')}",
            params={"userId": user_id},
            auth=aws_auth,
            timeout=timeout,
        )
        items = resp.json().get(list_key, []) if resp.status_code == 200 else []
        return filter_records_by_user(items, user_id)
    except Exception as e:
        try:
            print(f"Error fetching {path}: {e}")
        except Exception:
            pass
        return []


_SETTINGS_SYNC_TTL_SECONDS = 300  # 5 min — balances freshness vs per-request AWS round-trip


def _ensure_user_settings_row(user_id: str, *, force: bool = False) -> None:
    """Sync the user's settings from the DB into the session, throttled.

    Called from page route handlers; a fresh AWS round-trip on every page load
    made navigation slow. We now cache for _SETTINGS_SYNC_TTL_SECONDS via a
    per-session timestamp; pass force=True after mutations (e.g., settings save).
    """
    try:
        last = float(session.get("_settingsSyncTs") or 0.0)
    except Exception:
        last = 0.0
    if not force and last and (time.time() - last) < _SETTINGS_SYNC_TTL_SECONDS:
        return
    try:
        resp = requests.get(f"{API_URL}/settings", params={"userId": user_id}, auth=aws_auth, timeout=10)
        settings = resp.json().get("settings", []) if resp.status_code == 200 else []
        settings = filter_records_by_user(settings, user_id)
    except Exception as e:
        print(f"Error fetching settings (home init): {e}")
        settings = []

    if settings and isinstance(settings, list):
        try:
            first = settings[0] or {}
            if first.get("theme"):
                session["theme"] = first.get("theme")
            if first.get("currency"):
                session["currency"] = first.get("currency")
            if first.get("dashboardColors"):
                session["dashboardColors"] = first.get("dashboardColors")
            if first.get("incomeCategories"):
                session["incomeCategories"] = first.get("incomeCategories")
            if first.get("expenseCategories"):
                session["expenseCategories"] = first.get("expenseCategories")
        except Exception:
            pass
        session["_settingsSyncTs"] = time.time()
        return

    default_data = {"userId": user_id, **_SETTINGS_DEFAULTS}

    try:
        upsert = requests.patch(f"{API_URL}/settings", json=default_data, auth=aws_auth, timeout=10)
        if upsert.status_code in (200, 201):
            session["currency"] = default_data["currency"]
            session["theme"] = default_data["theme"]
            session["dashboardColors"] = default_data["dashboardColors"]
            session["incomeCategories"] = default_data["incomeCategories"]
            session["expenseCategories"] = default_data["expenseCategories"]
            session["_settingsSyncTs"] = time.time()
        else:
            try:
                print(f"Settings default upsert failed: {upsert.status_code} {upsert.text}")
            except Exception:
                print(f"Settings default upsert failed: {upsert.status_code}")
    except Exception as e:
        print(f"Error creating default settings (home init): {e}")


@home_bp.route("/api/dashboard-cache/invalidate", methods=["POST"])
def invalidate_dashboard_cache():
    user = session.get("user")
    if not user:
        return jsonify({"error": "Not authenticated"}), 401
    user_id = user.get("username")
    _dashboard_cache_invalidate_user(user_id)
    return jsonify({"ok": True})


@home_bp.route("/dashboard", methods=["GET"])
def dashboard():
    user = session.get("user")
    if user:
        userId = user.get("username")
        _ensure_user_settings_row(userId)
        base_currency = _get_user_base_currency(userId)
        return render_template(
            "dashboard.html",
            baseCurrency=base_currency,
            userId=userId,
            dashboardColors=session.get("dashboardColors", {}),
        )
    else:
        return render_template("dashboard.html")


@home_bp.route("/api/dashboard-data", methods=["GET"])
def dashboard_data():
    user = session.get("user")
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    userId = user.get("username")
    base_currency = _get_user_base_currency(userId)

    cached_ctx = _dashboard_cache_get(userId, base_currency)
    if isinstance(cached_ctx, dict):
        return jsonify(cached_ctx)

    # Fetch all API resources in parallel to reduce total wall time.
    cryptos: list = []
    wallets: list = []
    transactions: list = []
    loans: list = []
    stocks: list = []

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {
            ex.submit(_api_list, "cryptos", user_id=userId, list_key="cryptos", timeout=12): "cryptos",
            ex.submit(_api_list, "wallets", user_id=userId, list_key="wallets", timeout=12): "wallets",
            ex.submit(_api_list, "transactions", user_id=userId, list_key="transactions", timeout=12): "transactions",
            ex.submit(_api_list, "loans", user_id=userId, list_key="loans", timeout=12): "loans",
            ex.submit(_api_list, "stocks", user_id=userId, list_key="stocks", timeout=12): "stocks",
        }
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                data = fut.result() or []
            except Exception:
                data = []
            if name == "cryptos":
                cryptos = data
            elif name == "wallets":
                wallets = data
            elif name == "transactions":
                transactions = data
            elif name == "loans":
                loans = data
            elif name == "stocks":
                stocks = data

    if _truthy_env(os.getenv("OVERVIEW_DEBUG")):
        def _distinct_user_ids(rows: list) -> list[str]:
            out = set()
            for r in rows or []:
                try:
                    if isinstance(r, dict) and r.get("userId") is not None:
                        out.add(str(r.get("userId")).strip())
                except Exception:
                    continue
            return sorted([x for x in out if x])

        try:
            in_codespaces = str(os.getenv("CODESPACES") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
            print(
                "[dashboard debug] userId=", userId,
                "baseCurrency=", base_currency,
                "codespaces=", in_codespaces,
                "cacheTTL=", _dashboard_cache_ttl_seconds(),
                "wallets=", len(wallets or []),
                "cryptos=", len(cryptos or []),
                "transactions=", len(transactions or []),
                "loans=", len(loans or []),
                "stocks=", len(stocks or []),
            )
            print("[dashboard debug] wallets.userId distinct:", _distinct_user_ids(wallets))
            print("[dashboard debug] transactions.userId distinct:", _distinct_user_ids(transactions))
            print("[dashboard debug] loans.userId distinct:", _distinct_user_ids(loans))
            print("[dashboard debug] stocks.userId distinct:", _distinct_user_ids(stocks))
            print("[dashboard debug] cryptos.userId distinct:", _distinct_user_ids(cryptos))
        except Exception:
            pass

    wallet_currency_by_id = {}
    for w in wallets or []:
        try:
            wid = (w or {}).get("walletId")
            if not wid:
                continue
            raw_ccy = (
                (w or {}).get("currency")
                or (w or {}).get("Currency")
                or (w or {}).get("walletCurrency")
                or (w or {}).get("wallet_currency")
            )
            wallet_currency_by_id[str(wid)] = _normalize_currency(raw_ccy, base_currency)
        except Exception:
            continue

    def _wallet_ccy(wallet_id: str) -> str:
        try:
            return wallet_currency_by_id.get(str(wallet_id), _normalize_currency(base_currency, "EUR"))
        except Exception:
            return _normalize_currency(base_currency, "EUR")

    def _fx_amount_to_wallet(amount: Decimal, from_currency: str, wallet_id: str) -> Decimal:
        try:
            from_ccy = _normalize_currency(from_currency, _normalize_currency(base_currency, "EUR"))
            to_ccy = _wallet_ccy(wallet_id)
            if from_ccy == to_ccy:
                return amount
            return amount * _get_fx_rate(from_ccy, to_ccy)
        except Exception:
            # On FX failure, fall back to no conversion.
            return amount

    # Wallet cash balances are kept in each wallet's own currency.
    # This ledger is used later to compute wallet totals (cash + crypto live value + stock live value).
    wallet_fiat_balances = defaultdict(lambda: Decimal("0"))

    # Legacy/unused: Real-time wallet balance calculation (base-currency) kept for reference.
    wallet_balances = defaultdict(lambda: Decimal("0"))
    # Track per-wallet crypto quantities for live valuation
    wallet_crypto_qty = defaultdict(lambda: defaultdict(lambda: Decimal(0)))

    # Calculate crypto totals using exact same method as crypto.py
    crypto_totals_map = {}
    try:
        # Safe Decimal conversion matching crypto.py behavior
        def to_decimal(val):
            try:
                if val is None:
                    return Decimal(0)
                s = str(val).strip().replace(",", ".")
                if s == "":
                    return Decimal(0)
                return Decimal(s)
            except Exception:
                return Decimal(0)

        # Group transactions by crypto and sort by date to process chronologically
        crypto_transactions = {}
        for c in cryptos:
            name = c.get("cryptoName") or "Unknown"
            if name not in crypto_transactions:
                crypto_transactions[name] = []
            crypto_transactions[name].append(c)

        # Sort each crypto's transactions by date
        for name in crypto_transactions:
            crypto_transactions[name].sort(key=lambda x: x.get("tdate", ""))

        # Process each crypto's transactions chronologically (exact same logic as crypto.py)
        for name, crypto_txs in crypto_transactions.items():
            entry = {
                "cryptoName": name,
                "total_qty": Decimal(0),  # Current holding quantity
                "total_cost": Decimal(0),  # Current total cost basis
                "total_fee": Decimal(0),  # Total fees paid
                "total_value_buy": Decimal(0),  # Total spent on purchases
                "total_value_sell": Decimal(0),  # Total received from sales
                "currency": base_currency,
            }

            for tx in crypto_txs:
                try:
                    qty = to_decimal(tx.get("quantity", 0))
                    price = to_decimal(tx.get("price", 0))
                    fee = to_decimal(tx.get("fee", 0))
                    operation = str(tx.get("operation") or tx.get("side") or "buy").lower()
                    fee_unit = str(tx.get("feeUnit") or "").strip().lower()
                    fee_currency = _normalize_currency(tx.get("feeCurrency"), "")

                    to_wallet = tx.get("toWallet")
                    from_wallet = tx.get("fromWallet")

                    # TRANSFER: fee is crypto quantity.
                    # Net received quantity = max(0, qty - fee).
                    # - From wallet loses qty (gross)
                    # - To wallet receives (qty - fee) (net)
                    # Portfolio delta = (+net if toWallet else 0) - (qty if fromWallet else 0)
                    if operation == "transfer":
                        fee_qty = fee if (fee_unit == "crypto" or fee_currency == "CRYPTO" or fee_unit == "") else Decimal(0)
                        qty_total = qty
                        qty_net = qty_total - fee_qty
                        if qty_net < 0:
                            qty_net = Decimal(0)

                        qty_in = qty_net if to_wallet else Decimal(0)
                        qty_out = qty_total if from_wallet else Decimal(0)

                        if from_wallet:
                            wallet_crypto_qty[from_wallet][name] -= qty_out
                        if to_wallet:
                            wallet_crypto_qty[to_wallet][name] += qty_in

                        delta_qty = qty_in - qty_out
                        if delta_qty > 0:
                            entry["total_qty"] += delta_qty
                        elif delta_qty < 0:
                            qty_to_remove = -delta_qty
                            if entry["total_qty"] and entry["total_qty"] > 0:
                                avg_cost_per_unit = entry["total_cost"] / entry["total_qty"]
                                can_remove = min(qty_to_remove, entry["total_qty"])
                                entry["total_qty"] -= can_remove
                                entry["total_cost"] -= can_remove * avg_cost_per_unit
                                remaining = qty_to_remove - can_remove
                                if remaining > 0:
                                    entry["total_qty"] -= remaining
                            else:
                                entry["total_qty"] -= qty_to_remove
                        continue

                    tx_currency = _normalize_currency(tx.get("currency"), base_currency)
                    fx_rate = _get_fx_rate(tx_currency, base_currency)

                    # Fee conversion: fiat feeCurrency or same-asset crypto fee.
                    if fee_unit == "crypto" or fee_currency == "CRYPTO":
                        fee_base = (fee * price) * fx_rate
                    else:
                        fee_ccy = _normalize_currency(fee_currency, tx_currency)
                        fee_fx = _get_fx_rate(fee_ccy, base_currency)
                        fee_base = fee * fee_fx

                    tx_value = (qty * price) + fee
                    tx_value_base = ((qty * price) * fx_rate) + fee_base
                    revenue_base = (qty * price) * fx_rate

                    if operation == "buy":
                        # BUY: Add to holdings and cost basis
                        entry["total_qty"] += qty
                        entry["total_cost"] += tx_value_base
                        entry["total_value_buy"] += tx_value_base
                        entry["total_fee"] += fee_base

                        # Wallet balance: money goes out of from_wallet, crypto goes into to_wallet
                        if from_wallet:
                            wallet_balances[from_wallet] -= tx_value_base  # Cash out (base currency)
                            # Cash out in the from_wallet's own currency
                            try:
                                wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(tx_value_base, base_currency, from_wallet)
                            except Exception:
                                pass
                        if to_wallet:
                            wallet_balances[to_wallet] += tx_value_base  # Crypto asset in (base currency)
                            # Track live crypto quantity by destination wallet
                            wallet_crypto_qty[to_wallet][name] += qty

                    elif operation == "sell":
                        # SELL: Use weighted average to calculate cost of sold portion
                        if entry["total_qty"] > 0:
                            # Calculate current weighted average cost per unit
                            avg_cost_per_unit = entry["total_cost"] / entry["total_qty"]

                            # Determine how much we can actually sell from current holdings
                            qty_to_sell = min(qty, entry["total_qty"])

                            # Cost basis of the sold quantity
                            sold_cost = qty_to_sell * avg_cost_per_unit

                            # Update holdings
                            entry["total_qty"] -= qty_to_sell
                            entry["total_cost"] -= sold_cost
                            entry["total_value_sell"] += (revenue_base - fee_base)  # Net revenue from sale (after fee)
                            entry["total_fee"] += fee_base

                            # If selling more than we have, handle the excess as short position
                            excess_qty = qty - qty_to_sell
                            if excess_qty > 0:
                                # Track the excess as negative position
                                entry["total_qty"] -= excess_qty
                                # No additional cost basis change for short position

                        else:
                            # Selling without holdings (short sell) - track as negative
                            entry["total_qty"] -= qty
                            entry["total_value_sell"] += (revenue_base - fee_base)
                            entry["total_fee"] += fee_base

                        # Wallet balance: crypto goes out of from_wallet, money goes into to_wallet
                        if from_wallet:
                            wallet_balances[
                                from_wallet
                            ] -= tx_value_base  # Crypto asset out (base currency)
                        if to_wallet:
                            wallet_balances[to_wallet] += revenue_base  # Cash in (base currency)
                            # Cash in (net of fee) in the to_wallet's own currency
                            try:
                                net_proceeds_base = revenue_base - fee_base
                                wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(net_proceeds_base, base_currency, to_wallet)
                            except Exception:
                                pass
                        # Track live crypto quantity leaving the source wallet
                        if from_wallet:
                            wallet_crypto_qty[from_wallet][name] -= qty

                except Exception as e:
                    print(f"Error processing transaction for {name}: {e} | Raw tx: {tx}")
                    continue

            # Set total_value as current cost basis for compatibility (same as crypto.py)
            entry["total_value"] = entry["total_cost"]
            crypto_totals_map[name] = entry

    except Exception as e:
        print(f"Error computing crypto totals: {e}")

    # Fiat transactions affect wallet cash balances (in each wallet's currency)
    # Process regular transactions
    for transaction in transactions:
        try:

            def to_decimal_fiat(val):
                try:
                    if val is None:
                        return Decimal(0)
                    s = str(val).strip().replace(",", ".")
                    if s == "":
                        return Decimal(0)
                    return Decimal(s)
                except Exception:
                    return Decimal(0)

            # Fiat transactions: amount is the only value field; price is deprecated.
            amt = to_decimal_fiat(transaction.get("amount", 0))
            fee = to_decimal_fiat(transaction.get("fee", 0))

            tx_currency = _normalize_currency(transaction.get("currency"), base_currency)

            to_wallet = transaction.get("toWallet")
            from_wallet = transaction.get("fromWallet")
            ttype = str(transaction.get("transType") or "").strip().lower()

            # Fiat wallet logic:
            # - Income: add amount to toWallet
            # - Expense: deduct (amount + fee) from fromWallet
            # - Transfer: deduct (amount + fee) from fromWallet and add amount to toWallet (same currency)
            # - FX Transfer: deduct (amount + fee) in fromWallet's currency, credit receivedAmount in toWallet's currency
            if ttype == "income":
                if to_wallet:
                    wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(amt, tx_currency, to_wallet)
            elif ttype == "expense":
                if from_wallet:
                    total_expense = amt + fee
                    wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(
                        total_expense, tx_currency, from_wallet
                    )
            elif ttype == "transfer":
                total_move = amt + fee
                if from_wallet:
                    wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(
                        total_move, tx_currency, from_wallet
                    )
                if to_wallet:
                    wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(amt, tx_currency, to_wallet)
            elif ttype == "fx transfer":
                received_amount = to_decimal_fiat(transaction.get("receivedAmount", 0))
                if from_wallet:
                    # Amount + fee are in the fromWallet's currency
                    from_ccy = _wallet_ccy(from_wallet)
                    wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(
                        amt + fee, from_ccy, from_wallet
                    )
                if to_wallet and received_amount:
                    # Received amount is in the toWallet's currency
                    to_ccy = _wallet_ccy(to_wallet)
                    wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(
                        received_amount, to_ccy, to_wallet
                    )
            else:
                # Backward-compatible fallback based on which wallets are provided
                if from_wallet and to_wallet:
                    total_move = amt + fee
                    wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(
                        total_move, tx_currency, from_wallet
                    )
                    wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(amt, tx_currency, to_wallet)
                elif to_wallet:
                    wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(amt, tx_currency, to_wallet)
                elif from_wallet:
                    wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(
                        amt, tx_currency, from_wallet
                    )

        except Exception as e:
            print(f"Error processing transaction {transaction}: {e}")
            continue

    # Loans also affect wallet cash balances (in each wallet's currency)
    def _wallet_id(val):
        try:
            s = (str(val) if val is not None else "").strip()
            if not s or s.lower() == "none":
                return None
            return s
        except Exception:
            return None

    for loan in loans or []:
        try:

            def to_decimal_loan(val):
                try:
                    if val is None:
                        return Decimal(0)
                    s = str(val).strip().replace(",", ".")
                    if s == "":
                        return Decimal(0)
                    return Decimal(s)
                except Exception:
                    return Decimal(0)

            loan_type = str(loan.get("type") or "").strip().lower()
            if loan_type not in ("borrow", "lend", "loan"):
                loan_type = "borrow"

            action = str(loan.get("action") or "").strip().lower() or "new"
            if action not in ("new", "repay"):
                action = "new"

            amt = to_decimal_loan(loan.get("amount"))
            fee = to_decimal_loan(loan.get("fee"))

            tx_currency = _normalize_currency(loan.get("currency"), base_currency)

            from_wallet = _wallet_id(loan.get("fromWallet"))
            to_wallet = _wallet_id(loan.get("toWallet"))

            inflow_wallet = None
            outflow_wallet = None

            if action == "new":
                if loan_type in ("borrow", "loan"):
                    inflow_wallet = to_wallet
                else:  # lend
                    outflow_wallet = from_wallet
            else:  # repay
                if loan_type in ("borrow", "loan"):
                    outflow_wallet = from_wallet
                else:  # lend
                    inflow_wallet = to_wallet

            if outflow_wallet:
                wallet_fiat_balances[outflow_wallet] -= _fx_amount_to_wallet(
                    amt, tx_currency, outflow_wallet
                )
            if inflow_wallet:
                wallet_fiat_balances[inflow_wallet] += _fx_amount_to_wallet(
                    amt, tx_currency, inflow_wallet
                )

            # Fee: if we have an outflow wallet, charge it there; otherwise deduct from inflow.
            if fee:
                if outflow_wallet:
                    wallet_fiat_balances[outflow_wallet] -= _fx_amount_to_wallet(
                        fee, tx_currency, outflow_wallet
                    )
                elif inflow_wallet:
                    wallet_fiat_balances[inflow_wallet] -= _fx_amount_to_wallet(
                        fee, tx_currency, inflow_wallet
                    )

        except Exception as e:
            print(f"Error processing loan {loan}: {e}")
            continue


    # Track per-wallet stock quantities (for live valuation in Wallet Balances)
    wallet_stock_qty = defaultdict(lambda: defaultdict(lambda: Decimal(0)))

    # Stock portfolio totals (cost basis + realized revenue) for Home overview
    stock_totals_map = {}
    try:
        stock_transactions = {}
        for s in stocks:
            sym = (s.get("stockName") or "").strip().upper() or "UNKNOWN"
            stock_transactions.setdefault(sym, []).append(s)

        for sym in stock_transactions:
            stock_transactions[sym].sort(key=lambda x: x.get("tdate", ""))

        for sym, stock_txs in stock_transactions.items():
            entry = {
                "stockName": sym,
                "total_qty": Decimal(0),
                "total_cost": Decimal(0),
                "total_fee": Decimal(0),
                "total_value_buy": Decimal(0),
                "total_value_sell": Decimal(0),
                "currency": base_currency,
            }

            for tx in stock_txs:
                try:
                    qty = _to_decimal_stock(tx.get("quantity", 0))
                    price_raw = _to_decimal_stock(tx.get("price", 0))
                    fee_raw = _to_decimal_stock(tx.get("fee", 0))
                    operation = str(tx.get("operation") or tx.get("side") or "buy").lower()

                    from_wallet = tx.get("fromWallet")
                    to_wallet = tx.get("toWallet")

                    tx_currency_raw = _normalize_currency(tx.get("currency"), base_currency)
                    fee_currency_raw = _normalize_currency(tx.get("feeCurrency"), tx_currency_raw)
                    price_major, tx_ccy = _scale_minor_currency(price_raw, tx_currency_raw)
                    fee_major, fee_ccy = _scale_minor_currency(fee_raw, fee_currency_raw)

                    fx_rate = _get_fx_rate(tx_ccy, base_currency)
                    fx_fee = _get_fx_rate(fee_ccy, base_currency)
                    tx_value_base = ((qty * price_major) * fx_rate) + (fee_major * fx_fee)
                    fee_base = fee_major * fx_fee
                    revenue_base = (qty * price_major) * fx_rate

                    # Wallet holdings / cash-flow effects:
                    # - buy: cash decreases in fromWallet, stock qty increases in toWallet
                    # - sell: stock qty decreases in fromWallet, cash increases in toWallet
                    # - transfer: move stock qty from fromWallet to toWallet
                    if operation == "buy":
                        if to_wallet:
                            wallet_stock_qty[to_wallet][sym] += qty
                        if from_wallet:
                            wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(
                                tx_value_base, base_currency, from_wallet
                            )
                    elif operation == "sell":
                        if from_wallet:
                            wallet_stock_qty[from_wallet][sym] -= qty
                        if to_wallet:
                            wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(
                                (revenue_base - fee_base), base_currency, to_wallet
                            )
                    elif operation == "transfer":
                        if from_wallet:
                            wallet_stock_qty[from_wallet][sym] -= qty
                        if to_wallet:
                            wallet_stock_qty[to_wallet][sym] += qty

                    if operation == "buy":
                        entry["total_qty"] += qty
                        entry["total_cost"] += tx_value_base
                        entry["total_value_buy"] += tx_value_base
                        entry["total_fee"] += fee_base
                    elif operation == "sell":
                        if entry["total_qty"] > 0:
                            avg_cost_per_unit = entry["total_cost"] / entry["total_qty"]
                            qty_to_sell = min(qty, entry["total_qty"])
                            sold_cost = qty_to_sell * avg_cost_per_unit
                            entry["total_qty"] -= qty_to_sell
                            entry["total_cost"] -= sold_cost
                            entry["total_value_sell"] += (revenue_base - fee_base)
                            entry["total_fee"] += fee_base

                            excess_qty = qty - qty_to_sell
                            if excess_qty > 0:
                                entry["total_qty"] -= excess_qty
                        else:
                            entry["total_qty"] -= qty
                            entry["total_value_sell"] += (revenue_base - fee_base)
                            entry["total_fee"] += fee_base
                    else:
                        continue

                except Exception as e:
                    print(f"Error processing stock tx for {sym}: {e} | Raw tx: {tx}")
                    continue

            entry["total_value"] = entry["total_cost"]
            stock_totals_map[sym] = entry

    except Exception as e:
        print(f"Error computing stock totals: {e}")
        stock_totals_map = {}

    # --- Build raw crypto holdings (no live prices — frontend uses priceCache) ---
    crypto_holdings = []
    for name, entry in crypto_totals_map.items():
        crypto_holdings.append({
            "name": name,
            "qty": float(entry.get("total_qty") or Decimal(0)),
            "paid": float(entry.get("total_cost") or Decimal(0)),
            "buyTotal": float(entry.get("total_value_buy") or Decimal(0)),
            "revenue": float(entry.get("total_value_sell") or Decimal(0)),
        })

    # --- Build raw stock holdings (no live prices — frontend uses priceCache) ---
    stock_holdings = []
    for sym, entry in stock_totals_map.items():
        stock_holdings.append({
            "symbol": sym,
            "qty": float(entry.get("total_qty") or Decimal(0)),
            "paid": float(entry.get("total_cost") or Decimal(0)),
            "buyTotal": float(entry.get("total_value_buy") or Decimal(0)),
            "revenue": float(entry.get("total_value_sell") or Decimal(0)),
        })

    # --- Build per-wallet instrument qty maps (frontend multiplies by live price) ---
    wallet_crypto_qty_out: dict[str, dict[str, float]] = {}
    for w_id, per_crypto in wallet_crypto_qty.items():
        inner = {}
        for cname, q in per_crypto.items():
            try:
                q_float = float(q or Decimal(0))
            except Exception:
                q_float = 0.0
            if q_float != 0:
                inner[cname] = q_float
        if inner:
            wallet_crypto_qty_out[str(w_id)] = inner

    wallet_stock_qty_out: dict[str, dict[str, float]] = {}
    for w_id, per_sym in wallet_stock_qty.items():
        inner = {}
        for sym, q in per_sym.items():
            try:
                q_float = float(q or Decimal(0))
            except Exception:
                q_float = 0.0
            if q_float != 0:
                inner[sym] = q_float
        if inner:
            wallet_stock_qty_out[str(w_id)] = inner

    # --- Build wallet list with cash + FX info only (live values computed on client) ---
    wallet_list = []
    for wallet in wallets:
        wallet_id = wallet.get("walletId")
        wallet_name = wallet.get("walletName")
        wallet_type = (
            wallet.get("walletType")
            or wallet.get("WalletType")
            or wallet.get("type")
            or wallet.get("wallet_type")
        )
        wallet_currency = (
            wallet.get("currency")
            or wallet.get("Currency")
            or wallet.get("walletCurrency")
            or wallet.get("wallet_currency")
        )
        cash_in_wallet_ccy = wallet_fiat_balances.get(wallet_id, Decimal(0)) or Decimal(0)

        w_ccy = _normalize_currency(wallet_currency, base_currency)
        b_ccy = _normalize_currency(base_currency, "EUR")
        fx_wallet_to_base = Decimal(1)
        fx_base_to_wallet = Decimal(1)
        try:
            if w_ccy and b_ccy and w_ccy != b_ccy:
                fx_base_to_wallet = _get_fx_rate(b_ccy, w_ccy)
                fx_wallet_to_base = _get_fx_rate(w_ccy, b_ccy)
        except Exception as e:
            print(f"FX convert base->{wallet_currency} failed for wallet {wallet_id}: {e}")
            fx_base_to_wallet = Decimal(1)
            fx_wallet_to_base = Decimal(1)

        cash_base = cash_in_wallet_ccy * fx_wallet_to_base

        wallet_list.append(
            {
                "walletId": wallet_id,
                "walletName": wallet_name,
                "walletType": wallet_type,
                "currency": wallet_currency,
                "color": wallet.get("color") or "#00b09a",
                "cashWallet": float(round(cash_in_wallet_ccy, 2)),
                "cashBase": float(round(cash_base, 2)),
                "fxBaseToWallet": float(fx_base_to_wallet),
            }
        )

    # --- Loans (Home card): open positions progress (no FX) ---
    # A position is considered open if outstanding > 0.
    loanHomePositions = []

    def _to_decimal_home(val) -> Decimal:
        try:
            if val is None:
                return Decimal(0)
            s = str(val).strip().replace(",", ".")
            if s == "":
                return Decimal(0)
            return Decimal(s)
        except Exception:
            return Decimal(0)

    def _day_from_iso_home(date_str: str) -> str:
        s = (date_str or "").strip()
        return s[:10] if len(s) >= 10 else s

    def _derive_position_home(counterparty: str, currency: str, tdate: str) -> str:
        party = (counterparty or "").strip() or "—"
        ccy = (currency or "").strip().upper() or (base_currency or "EUR")
        day = _day_from_iso_home(tdate) or "unknown-date"
        return f"{party} | {ccy} | {day}"

    if loans:
        pos_map = {}
        for row in loans or []:
            try:
                t = str(row.get("type") or "").strip().lower()
                if t not in ("borrow", "lend", "loan"):
                    t = "borrow"
                action = str(row.get("action") or "").strip().lower() or "new"
                if action not in ("new", "repay"):
                    action = "new"

                party = str(row.get("counterparty") or "").strip() or "—"
                currency = str(row.get("currency") or "").strip().upper() or (base_currency or "EUR")
                amt = _to_decimal_home(row.get("amount"))
                tdate = str(row.get("tdate") or "").strip()
                position_raw = str(row.get("position") or "").strip()

                if action == "new":
                    position = position_raw or _derive_position_home(party, currency, tdate)
                else:
                    position = position_raw
                    if not position:
                        # Fallback: treat legacy repay as its own bucket.
                        position = f"{party} | {currency} | legacy"

                key = (t, position)
                if key not in pos_map:
                    pos_map[key] = {
                        "type": t,
                        "position": position,
                        "counterparty": party,
                        "currency": currency,
                        "principal": Decimal(0),
                        "repaid": Decimal(0),
                    }

                if action == "new":
                    pos_map[key]["principal"] += amt
                else:
                    pos_map[key]["repaid"] += amt
            except Exception:
                continue

        for entry in pos_map.values():
            principal = entry.get("principal") or Decimal(0)
            repaid = entry.get("repaid") or Decimal(0)
            outstanding = principal - repaid
            if outstanding <= 0:
                continue

            # Clamp overpayment to principal for progress display.
            repaid_for_pct = repaid
            if principal > 0 and repaid_for_pct > principal:
                repaid_for_pct = principal

            pct = Decimal(0)
            if principal > 0:
                pct = (repaid_for_pct / principal) * Decimal(100)
                if pct < 0:
                    pct = Decimal(0)
                if pct > 100:
                    pct = Decimal(100)

            t = entry.get("type")
            if t == "lend":
                type_label = "Lend"
            elif t == "loan":
                type_label = "Loan"
            else:
                type_label = "Borrow"

            loanHomePositions.append(
                {
                    "counterparty": entry.get("counterparty") or "—",
                    "currency": entry.get("currency") or (base_currency or "EUR"),
                    "type": t,
                    "typeLabel": type_label,
                    "principal": float(principal),
                    "repaid": float(repaid_for_pct),
                    "outstanding": float(outstanding),
                    "progressPct": float(pct),
                }
            )

        loanHomePositions.sort(
            key=lambda x: (
                -abs(Decimal(str(x.get("outstanding") or 0))),
                str(x.get("counterparty") or "").lower(),
                str(x.get("currency") or ""),
            )
        )

    ctx = {
        "baseCurrency": base_currency,
        "cryptoHoldings": crypto_holdings,
        "stockHoldings": stock_holdings,
        "walletCryptoQty": wallet_crypto_qty_out,
        "walletStockQty": wallet_stock_qty_out,
        "wallets": wallet_list,
        "loanHomePositions": loanHomePositions,
        "userId": userId,
    }
    _dashboard_cache_set(userId, base_currency, ctx)
    return jsonify(ctx)


@home_bp.route("/", methods=["GET"])
def users():
    user = session.get("user")
    if user:
        user_id = user.get("username")
        if user_id:
            _ensure_user_settings_row(user_id)

    return render_template("home.html")
