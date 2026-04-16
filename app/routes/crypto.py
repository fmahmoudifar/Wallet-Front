import math
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import ROUND_HALF_UP, Decimal

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.services.user_scope import filter_records_by_user
from config import API_URL, aws_auth, CMC_API_KEY

crypto_bp = Blueprint("crypto", __name__)

# CoinMarketCap cache (in-process) to reduce API calls and respect rate limits
_CMC_QUOTE_CACHE = {}  # Caches full crypto data by symbol
_CMC_QUOTE_TTL_SECONDS = 300  # 5 minutes

_BINANCE_PRICE_CACHE = {}
_BINANCE_PRICE_TTL_SECONDS = 60

# FX rates cache (in-process) to convert transaction currency -> website currency
_FX_RATES_CACHE = {}
_FX_RATES_TTL_SECONDS = 3600


def _normalize_currency(code: str, default: str = "") -> str:
    try:
        c = (code or "").strip().upper()
        return c if c else default
    except Exception:
        return default


def _get_user_base_currency(user_id: str) -> str:
    """Return the website/base currency (from Settings). Defaults to EUR."""
    # Prefer session value (updated when visiting/updating settings)
    base = _normalize_currency(session.get("currency"), "")
    if base:
        return base

    # Fallback: fetch settings directly
    try:
        resp = requests.get(f"{API_URL}/settings", params={"userId": user_id}, auth=aws_auth, timeout=10)
        if resp.status_code == 200:
            settings = resp.json().get("settings", [])
            if settings and isinstance(settings, list):
                currency = _normalize_currency((settings[0] or {}).get("currency"), "EUR")
                if currency:
                    session["currency"] = currency
                    return currency
    except Exception:
        pass

    return "EUR"


def _get_fx_rate(from_currency: str, to_currency: str) -> Decimal:
    """Get latest FX rate from_currency -> to_currency using Frankfurter (cached)."""
    from_ccy = _normalize_currency(from_currency)
    to_ccy = _normalize_currency(to_currency)
    if from_ccy == to_ccy:
        return Decimal(1)

    key = (from_ccy, to_ccy)
    now = time.time()
    cached = _FX_RATES_CACHE.get(key)
    if cached and (now - cached.get("ts", 0.0) < _FX_RATES_TTL_SECONDS):
        return cached["rate"]

    # Frankfurter supports many fiat currencies. Crypto tx currencies should be normalized before calling.
    url = "https://api.frankfurter.app/latest"
    params = {"from": from_ccy, "to": to_ccy}
    headers = {"Accept": "application/json", "User-Agent": "Wallet-Front/1.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json() or {}
            rates = data.get("rates") or {}
            rate_val = rates.get(to_ccy)
            if rate_val is None:
                raise ValueError(f"Missing FX rate {from_ccy}->{to_ccy}")
            rate = Decimal(str(rate_val))
            _FX_RATES_CACHE[key] = {"ts": now, "rate": rate}
            return rate

        # Non-200: fall back to cached, if available
        if cached:
            return cached["rate"]
        raise RuntimeError(f"FX API returned {r.status_code}")
    except Exception:
        # On network/parsing errors, prefer cache rather than breaking totals.
        if cached:
            return cached["rate"]
        raise


def _format_number_trim(val, max_decimals: int) -> str:
    """Format a numeric value to <= max_decimals, trimming trailing zeros.

    Examples:
      93.40 (2) -> '93.4'
      1173.00340000 (8) -> '1173.0034'
    """
    try:
        d = Decimal(str(val))
    except Exception:
        return "0"

    try:
        if max_decimals is None:
            s = format(d, "f")
        else:
            q = Decimal("1").scaleb(-int(max_decimals))  # 10^-max_decimals
            d = d.quantize(q, rounding=ROUND_HALF_UP)
            s = format(d, "f")
    except Exception:
        s = str(d)

    if "." in s:
        s = s.rstrip("0").rstrip(".")
    # avoid showing '-0'
    if s in ("-0", "-0.0"):
        s = "0"
    return s or "0"


# ===== CoinMarketCap API Functions =====

def _cmc_get_crypto_info(symbol: str) -> dict:
    """Fetch crypto info from CoinMarketCap by symbol.
    
    Returns dict with keys: id, name, symbol, price_usd
    Returns empty dict if not found or API unavailable.
    """
    if not CMC_API_KEY:
        print("Warning: CMC_API_KEY not configured")
        return {}

    sym = (symbol or "").strip().upper()
    if not sym:
        return {}

    # Check cache first
    now = time.time()
    cached = _CMC_QUOTE_CACHE.get(sym)
    if cached and (now - cached.get("ts", 0.0) < _CMC_QUOTE_TTL_SECONDS):
        return cached.get("data", {})

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
        "Accept": "application/json",
        "User-Agent": "Wallet-Front/1.0",
    }

    try:
        r = requests.get(
            url,
            params={"symbol": sym, "convert": "USD"},
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            return {}

        data = r.json() or {}
        if "data" not in data or not data["data"]:
            return {}

        # CMC returns data keyed by symbol
        crypto_data = data["data"].get(sym) if isinstance(data["data"], dict) else None
        if not crypto_data:
            return {}

        result = {
            "id": crypto_data.get("id"),
            "name": crypto_data.get("name", sym),
            "symbol": crypto_data.get("symbol", sym),
            "price_usd": float(
                (crypto_data.get("quote", {}).get("USD", {}).get("price")) or 0
            ),
        }

        # Cache the result
        _CMC_QUOTE_CACHE[sym] = {"ts": now, "data": result}
        return result

    except Exception as e:
        print(f"Error fetching CMC data for {sym}: {e}")
        return {}


def _cmc_search_symbols(query: str) -> list:
    """Search for cryptocurrencies on CoinMarketCap.
    
    Returns list of dicts with id, symbol, name, and rank.
    """
    if not CMC_API_KEY:
        return []

    q = (query or "").strip()
    if not q or len(q) < 1:
        return []

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
        "Accept": "application/json",
        "User-Agent": "Wallet-Front/1.0",
    }

    try:
        # CMC map endpoint supports symbol and listing_status filters
        # First try exact symbol match
        r = requests.get(
            url,
            params={
                "symbol": q.upper(),
                "listing_status": "active,inactive",
            },
            headers=headers,
            timeout=10,
        )

        if r.status_code != 200:
            return []

        data = r.json() or {}
        cryptocurrencies = data.get("data", [])

        if not cryptocurrencies:
            # Try search by name if symbol didn't match
            r = requests.get(
                url,
                params={
                    "start": 1,
                    "limit": 50,
                    "listing_status": "active,inactive",
                },
                headers=headers,
                timeout=10,
            )
            if r.status_code != 200:
                return []

            data = r.json() or {}
            cryptocurrencies = data.get("data", [])
            # Filter by name/symbol match
            q_upper = q.upper()
            cryptocurrencies = [
                c
                for c in cryptocurrencies
                if q_upper in str(c.get("name", "")).upper()
                or q_upper in str(c.get("symbol", "")).upper()
            ][:20]

        result = []
        for c in cryptocurrencies:
            result.append(
                {
                    "id": c.get("id"),
                    "symbol": c.get("symbol"),
                    "name": c.get("name"),
                    "rank": c.get("rank"),
                }
            )
        return result

    except Exception as e:
        print(f"Error searching CMC for '{q}': {e}")
        return []


def _cmc_price_usd(symbol: str) -> Decimal:
    """Get USD price for a crypto symbol from CoinMarketCap."""
    if not symbol:
        return Decimal(0)

    # Handle stablecoins
    if symbol.upper() in ("USD", "USDT", "USDC", "DAI", "BUSD", "FDUSD"):
        return Decimal(1)

    info = _cmc_get_crypto_info(symbol)
    if not info:
        return Decimal(0)

    try:
        price = Decimal(str(info.get("price_usd", 0)))
        return price if price > 0 else Decimal(0)
    except Exception:
        return Decimal(0)


def _dexscreener_search(query: str) -> list:
    """DEPRECATED - Use CMC via _cmc_search_symbols() instead."""
    return []


def _dexscreener_best_price_usd(query: str) -> Decimal:
    """DEPRECATED - Use CMC only via _best_price_usd()"""
    return Decimal(0)


def _binance_price_usd(symbol: str) -> Decimal:
    """DEPRECATED - Use CMC only via _best_price_usd(). 
    
    Kept for backward compatibility - only returns stablecoins.
    """
    sym = (symbol or "").strip().upper()
    
    # Common stablecoins
    if sym in ("USD", "USDT", "USDC", "DAI", "FDUSD", "BUSD"):
        return Decimal(1)
    
    return Decimal(0)


def _best_price_usd(query: str) -> Decimal:
    """Unified price lookup using CoinMarketCap.
    
    Extracts symbol from query (handles "SYMBOL - Name" format) and fetches price.
    CMC-only, no fallbacks to legacy sources.
    """
    q = (query or "").strip()
    if not q:
        return Decimal(0)

    # Extract symbol from query
    sym = ""
    if " - " in q:
        sym = q.split(" - ", 1)[0].strip().upper()
    else:
        # If query looks like a symbol, treat it as such
        if " " not in q and len(q) <= 15 and not q.startswith("0x"):
            sym = q.strip().upper()
        else:
            parts = q.split()
            if parts and 1 <= len(parts[0]) <= 15 and not parts[0].startswith("0x"):
                sym = parts[0].strip().upper()

    # Use CMC for price lookup (no fallbacks)
    if sym:
        return _cmc_price_usd(sym)

    return Decimal(0)


@crypto_bp.get("/crypto/search")
def crypto_search():
    """Autocomplete helper: return a small list of token suggestions from CoinMarketCap.

    Response shape matches the client-side expectations: {coins:[{id,symbol,name}, ...]}.
    """
    user = session.get("user")
    if not user:
        return jsonify({"coins": []})

    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify({"coins": []})

    # Search CMC for matching cryptos
    results = _cmc_search_symbols(q)
    if not results:
        return jsonify({"coins": []})

    coins = []
    for c in results[:20]:  # Limit to 20 results
        coins.append({
            "id": str(c.get("id", "")).lower(),
            "symbol": c.get("symbol") or "",
            "name": f"{c.get('symbol') or ''} - {c.get('name') or ''}",
        })

    return jsonify({"coins": coins})


@crypto_bp.route("/crypto", methods=["GET"])
def crypto_page():
    user = session.get("user")
    if user:
        userId = user.get("username")
        base_currency = _get_user_base_currency(userId)
        return render_template("crypto.html", baseCurrency=base_currency, userId=userId)
    else:
        return render_template("home.html")


def _dexscreener_token_name(query: str) -> str:
    """DEPRECATED - Use CMC via _cmc_get_crypto_info() instead."""
    return ""



@crypto_bp.route("/crypto/quote", methods=["GET"])
def crypto_quote():
    """Per-symbol live price endpoint (called client-side to hydrate portfolio cards).
    
    Uses CMC as sole price source. Returns price in user's base currency.
    """
    user = session.get("user")
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    symbol = (request.args.get("symbol") or "").strip()
    if not symbol:
        return jsonify({"error": "Missing symbol"}), 400

    user_id = user.get("username")
    base_currency = _get_user_base_currency(user_id)

    try:
        # Extract symbol from "SYMBOL - Name" format if present
        if " - " in symbol:
            sym_to_fetch = symbol.split(" - ", 1)[0].strip()
        else:
            sym_to_fetch = symbol.strip()

        # Fetch price in USD from CMC (CMC-only, no fallbacks)
        price_usd = _best_price_usd(sym_to_fetch)
        
        # If no price found, return null (frontend will display "—")
        if not price_usd or price_usd <= 0:
            return jsonify({
                "symbol": symbol,
                "price": None,
                "currency": base_currency,
            })

        # Convert USD price to user's base currency
        usd_to_base = _get_fx_rate("USD", base_currency)
        price_base = float(price_usd * usd_to_base)

        # Get crypto name from CMC
        crypto_info = _cmc_get_crypto_info(sym_to_fetch)
        name = ""
        if crypto_info and crypto_info.get("name"):
            name = f"{crypto_info.get('symbol', sym_to_fetch)} - {crypto_info.get('name', '')}"
        else:
            # Fallback: use what we have
            if " - " in symbol:
                name = symbol
            else:
                name = sym_to_fetch

        return jsonify({
            "symbol": symbol,
            "price": price_base,
            "currency": base_currency,
            "name": name,
        })

    except Exception as e:
        print(f"Error in crypto_quote for '{symbol}': {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "symbol": symbol,
            "price": None,
            "currency": base_currency,
        })


@crypto_bp.route("/api/crypto-data", methods=["GET"])
def crypto_data():
    user = session.get("user")
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    userId = user.get("username")
    base_currency = _get_user_base_currency(userId)

    # --- Fetch Cryptos + Wallets in parallel ---
    cryptos = []
    wallets = []

    def _fetch_cryptos():
        resp = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
        items = resp.json().get("cryptos", []) if resp.status_code == 200 else []
        return filter_records_by_user(items, userId)

    def _fetch_wallets():
        resp = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
        items = resp.json().get("wallets", []) if resp.status_code == 200 else []
        return filter_records_by_user(items, userId)

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_c = ex.submit(_fetch_cryptos)
            fut_w = ex.submit(_fetch_wallets)
            cryptos = fut_c.result()
            wallets = fut_w.result()
    except Exception as e:
        print(f"Error fetching cryptos/wallets: {e}")

    # Map wallet names to ids to handle APIs that store walletName in fromWallet/toWallet.
    wallet_ids_set = set()
    wallet_id_by_name = {}
    try:
        for w in wallets or []:
            wid = (w.get("walletId") or "").strip()
            wname = (w.get("walletName") or "").strip()
            if wid:
                wallet_ids_set.add(wid)
            if wid and wname:
                wallet_id_by_name[wname] = wid
    except Exception:
        wallet_ids_set = set()
        wallet_id_by_name = {}

    def _resolve_wallet_ref(val):
        s = (str(val) if val is not None else "").strip()
        if not s:
            return ""
        if s in wallet_ids_set:
            return s
        return wallet_id_by_name.get(s, s)

    # --- Build coin list for autocomplete from the user's saved crypto names ---
    # This keeps the current UX (selection-only autocomplete) without relying on a provider-wide coin list.
    coins = []
    try:
        seen = set()
        for tx in cryptos or []:
            raw = (tx.get("cryptoName") or "").strip()
            if not raw:
                continue
            sym = raw
            name = raw
            if " - " in raw:
                left, right = raw.split(" - ", 1)
                sym = left.strip() or raw
                name = right.strip() or raw
            sym_up = sym.strip().upper()
            if not sym_up or sym_up in seen:
                continue
            seen.add(sym_up)
            coins.append(
                {
                    "id": sym_up.lower(),
                    "symbol": sym_up,
                    "name": name.strip() or sym_up,
                }
            )

        # Enrich coins that only have SYMBOL as name (e.g. 'BTC' -> 'Bitcoin').
        # Uses CoinMarketCap data so autocomplete shows "SYMBOL - Full Name".
        def _best_token_name_for_symbol(symbol: str) -> str:
            sym_q = (symbol or "").strip().upper()
            if not sym_q:
                return ""
            info = _cmc_get_crypto_info(sym_q)
            if not info:
                return ""
            return info.get("name", "")

        # Collect symbols that need name enrichment
        symbols_to_lookup = []
        for c in coins:
            try:
                sym_up = (c.get("symbol") or "").strip().upper()
                nm = (c.get("name") or "").strip()
                if not sym_up:
                    continue
                if nm and nm.upper() != sym_up:
                    continue
                symbols_to_lookup.append(sym_up)
            except Exception:
                continue

        # Parallel name enrichment (up to 20 symbols)
        symbols_to_lookup = symbols_to_lookup[:20]
        name_cache = {}
        if symbols_to_lookup:
            with ThreadPoolExecutor(max_workers=10) as name_ex:
                fut_map = {name_ex.submit(_best_token_name_for_symbol, s): s
                           for s in symbols_to_lookup}
                for fut in as_completed(fut_map):
                    sym = fut_map[fut]
                    try:
                        name_cache[sym] = fut.result()
                    except Exception:
                        name_cache[sym] = ""

        for c in coins:
            try:
                sym_up = (c.get("symbol") or "").strip().upper()
                full = name_cache.get(sym_up, "")
                if full and full.strip() and full.strip().upper() != sym_up:
                    c["name"] = full.strip()
            except Exception:
                continue

        coins.sort(key=lambda c: (c.get("symbol") or "").lower())
    except Exception:
        coins = []

    # --- Compute totals per crypto using weighted average price method ---
    totals_map = {}
    wallet_crypto_qty = defaultdict(lambda: defaultdict(lambda: Decimal(0)))
    wallet_ids_seen = set()
    try:
        # Group transactions by crypto and sort by date to process chronologically
        crypto_transactions = {}
        for c in cryptos:
            name = c.get("cryptoName") or "Unknown"
            if name not in crypto_transactions:
                crypto_transactions[name] = []
            crypto_transactions[name].append(c)

        def to_decimal(val):
            """Safely convert API value to Decimal; treat blanks/None as 0."""
            try:
                if val is None:
                    return Decimal(0)
                s = str(val).strip().replace(",", ".")
                if s == "":
                    return Decimal(0)
                return Decimal(s)
            except Exception:
                return Decimal(0)

        def norm_crypto_key(raw_name):
            """Normalize stored cryptoName to a stable key for wallet holdings.
            Prefers the leading symbol in patterns like 'BTC - Bitcoin'; otherwise uses the trimmed upper string.
            """
            s = (str(raw_name) if raw_name is not None else "").strip()
            if not s:
                return "UNKNOWN"
            if " - " in s:
                s = s.split(" - ", 1)[0].strip()
            # Remove common wrappers
            s = s.replace("(", " ").replace(")", " ")
            s = " ".join(s.split())
            return s.upper()

        # Sort each crypto's transactions by date
        for name in crypto_transactions:
            crypto_transactions[name].sort(key=lambda x: x.get("tdate", ""))

        # Process each crypto's transactions chronologically
        for name, transactions in crypto_transactions.items():
            entry = {
                "cryptoName": name,
                "total_qty": Decimal(0),  # Current holding quantity
                "total_cost": Decimal(0),  # Current total cost basis
                "total_fee": Decimal(0),  # Total fees paid
                "total_value_buy": Decimal(0),  # Total spent on purchases
                "total_value_sell": Decimal(0),  # Total received from sales
                "currency": base_currency,
            }

            for tx in transactions:
                try:
                    qty = to_decimal(tx.get("quantity", 0))
                    price = to_decimal(tx.get("price", 0))
                    fee = to_decimal(tx.get("fee", 0))
                    operation = str(tx.get("operation") or tx.get("side") or "buy").lower()
                    fee_unit = str(tx.get("feeUnit") or "").strip().lower()
                    ckey = norm_crypto_key(tx.get("cryptoName") or name)

                    # TRANSFER: fee is a crypto quantity.
                    # Net received quantity = max(0, qty - fee).
                    # - From wallet loses qty (gross)
                    # - To wallet receives (qty - fee) (net)
                    # Portfolio delta = (+net if toWallet else 0) - (qty if fromWallet else 0)
                    if operation == "transfer":
                        from_wallet = _resolve_wallet_ref(tx.get("fromWallet"))
                        to_wallet = _resolve_wallet_ref(tx.get("toWallet"))

                        # Transfer fee is expected to be a crypto quantity when feeUnit == 'crypto'.
                        # If older records have feeUnit missing or set differently, treat fee as 0 for qty math.
                        fee_qty = fee if (fee_unit == "crypto" or fee_unit == "") else Decimal(0)
                        qty_total = qty
                        qty_net = qty_total - fee_qty
                        if qty_net < 0:
                            qty_net = Decimal(0)

                        if from_wallet:
                            wallet_ids_seen.add(from_wallet)
                        if to_wallet:
                            wallet_ids_seen.add(to_wallet)

                        # Track per-wallet holdings (wallet-level quantities)
                        if from_wallet:
                            wallet_crypto_qty[from_wallet][ckey] -= qty_total
                        if to_wallet:
                            wallet_crypto_qty[to_wallet][ckey] += qty_net

                        qty_in = qty_net if to_wallet else Decimal(0)
                        qty_out = qty_total if from_wallet else Decimal(0)
                        delta_qty = qty_in - qty_out

                        if delta_qty > 0:
                            # Incoming transfer (no cost basis info here) -> add qty with zero cost basis.
                            entry["total_qty"] += delta_qty
                        elif delta_qty < 0:
                            # Outgoing amount reduces holdings at average cost (no revenue).
                            qty_to_remove = -delta_qty
                            if entry["total_qty"] and entry["total_qty"] > 0:
                                avg_cost_per_unit = entry["total_cost"] / entry["total_qty"]
                                can_remove = min(qty_to_remove, entry["total_qty"])
                                entry["total_qty"] -= can_remove
                                entry["total_cost"] -= can_remove * avg_cost_per_unit
                                remaining = qty_to_remove - can_remove
                                if remaining > 0:
                                    # reflect as negative holdings (no additional cost basis change)
                                    entry["total_qty"] -= remaining
                            else:
                                entry["total_qty"] -= qty_to_remove
                        continue

                    tx_currency = _normalize_currency(tx.get("currency"), base_currency)
                    fx_rate = _get_fx_rate(tx_currency, base_currency)

                    # Transaction value (qty * price + fee)
                    tx_value = (qty * price) + fee
                    tx_value_base = tx_value * fx_rate
                    fee_base = fee * fx_rate
                    revenue_base = (qty * price) * fx_rate

                    if operation == "buy":
                        # BUY: Add to holdings and cost basis
                        entry["total_qty"] += qty
                        entry["total_cost"] += tx_value_base
                        entry["total_value_buy"] += tx_value_base
                        entry["total_fee"] += fee_base

                        # Wallet holdings: buys land in toWallet
                        from_wallet = _resolve_wallet_ref(tx.get("fromWallet"))
                        to_wallet = _resolve_wallet_ref(tx.get("toWallet"))
                        hold_wallet = to_wallet or from_wallet
                        if from_wallet:
                            wallet_ids_seen.add(from_wallet)
                        if to_wallet:
                            wallet_ids_seen.add(to_wallet)
                        # If user didn't provide toWallet, fall back to fromWallet so wallet contents still updates.
                        if hold_wallet:
                            wallet_crypto_qty[hold_wallet][ckey] += qty

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
                            entry["total_value_sell"] += revenue_base  # Revenue from sale (excluding fee)
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
                            entry["total_value_sell"] += revenue_base
                            entry["total_fee"] += fee_base

                        # Wallet holdings: sells leave fromWallet
                        from_wallet = _resolve_wallet_ref(tx.get("fromWallet"))
                        to_wallet = _resolve_wallet_ref(tx.get("toWallet"))
                        hold_wallet = from_wallet or to_wallet
                        if from_wallet:
                            wallet_ids_seen.add(from_wallet)
                        if to_wallet:
                            wallet_ids_seen.add(to_wallet)
                        # If user didn't provide fromWallet, fall back to toWallet so wallet contents still updates.
                        if hold_wallet:
                            wallet_crypto_qty[hold_wallet][ckey] -= qty

                except Exception as e:
                    print(f"Error processing transaction for {name}: {e} | Raw tx: {tx}")
                    continue

            # Set total_value as current cost basis for compatibility
            entry["total_value"] = entry["total_cost"]
            totals_map[name] = entry

    except Exception as e:
        print(f"Error computing crypto totals: {e}")

    # --- Set placeholder values for live price fields (prices fetched client-side) ---
    for name_key, v in totals_map.items():
        v["latest_price"] = None
        v["currency"] = base_currency
        v["total_value_live"] = None
        v["value_change_amount"] = None

        # compute weighted average buy price from current cost basis
        # Only calculate avg_buy_price if we have actual purchases (total_cost > 0)
        # If holdings came entirely from transfers (cost = 0), set to None to display as "—"
        try:
            if v["total_qty"] and v["total_qty"] > 0:
                if v["total_cost"] and v["total_cost"] > 0:
                    v["avg_buy_price"] = v["total_cost"] / v["total_qty"]
                else:
                    # Holdings without purchases (transferred in) - show as N/A
                    v["avg_buy_price"] = None
            else:
                v["avg_buy_price"] = Decimal(0)
        except Exception:
            v["avg_buy_price"] = Decimal(0)

    # --- Compute holdings by wallet (quantities only; live values hydrated client-side) ---
    wallet_holdings = []
    try:
        wallet_name_by_id = {}
        for w in wallets or []:
            wid = (w.get("walletId") or "").strip()
            if wid:
                wallet_name_by_id[wid] = w.get("walletName") or wid

        ordered_wallet_ids = [w.get("walletId") for w in (wallets or []) if w.get("walletId")]
        for wid in ordered_wallet_ids:
            if wallet_ids_seen and wid not in wallet_ids_seen:
                continue
            per_crypto = wallet_crypto_qty.get(wid) or {}
            holdings_rows = []
            for cname, qty in per_crypto.items():
                try:
                    q = qty or Decimal(0)
                    if q == 0:
                        continue
                    holdings_rows.append(
                        {
                            "cryptoName": cname,
                            "qty": float(q),
                            "qty_display": _format_number_trim(q, 8),
                            "value_live": None,
                            "value_live_display": "\u2014",
                        }
                    )
                except Exception:
                    continue

            holdings_rows.sort(key=lambda r: abs(r.get("qty", 0.0)), reverse=True)

            wallet_holdings.append(
                {
                    "walletId": wid,
                    "walletName": wallet_name_by_id.get(wid, wid),
                    "total_value_live": None,
                    "total_value_live_display": "\u2014",
                    "holdings": holdings_rows,
                }
            )
    except Exception as e:
        print(f"Error computing wallet holdings: {e}")
        wallet_holdings = []

    # Convert Decimal values to floats for template rendering
    totals = []
    for v in totals_map.values():
        try:
            tq = float(v["total_qty"])
        except Exception:
            tq = 0.0
        try:
            tv = float(v["total_value"])
        except Exception:
            tv = 0.0
        # Correctly retrieve total amount paid on buy transactions (includes buy fees)
        try:
            tv_buy = float(v.get("total_value_buy", 0) or 0)
        except Exception:
            tv_buy = 0.0
        try:
            lp = float(v.get("latest_price", 0) or 0)
        except Exception:
            lp = 0.0
        try:
            tv_live = float(v.get("total_value_live", 0) or 0)
        except Exception:
            tv_live = 0.0

        # compute percent change compared to average stored price (per-unit) and value
        price_pct = None
        value_pct = None
        pct_fill = 0.0
        price_multiplier = None
        price_pct_display = None
        value_pct_display = None
        try:
            if tq and tv:
                avg_price = tv / tq
                if avg_price and avg_price != 0:
                    price_pct = ((lp - avg_price) / avg_price) * 100.0
                    # multiplier (how many times current price vs avg stored price)
                    if avg_price > 0:
                        price_multiplier = (lp / avg_price) if lp is not None else None
            # percent change based on total stored value
            if tv and tv != 0:
                value_pct = ((tv_live - tv) / tv) * 100.0
        except Exception:
            price_pct = None
            value_pct = None

        # create friendly display strings
        try:
            # price percent display: show percentage change based on price_pct
            if price_pct is None:
                price_pct_display = "N/A"
            else:
                # for extremely large or tiny values, use scientific notation
                if abs(price_pct) > 10000:
                    price_pct_display = f"{price_pct:.2e}%"
                else:
                    price_pct_display = f"{price_pct:.2f}%"

            # value percent display
            if value_pct is None:
                value_pct_display = "N/A"
            else:
                # cap large values for readability
                if abs(value_pct) > 10000:
                    value_pct_display = f"{value_pct:.2e}%"
                else:
                    value_pct_display = f"{value_pct:.2f}%"
        except Exception:
            price_pct_display = "N/A"
            value_pct_display = "N/A"

        # visual fill: use a log scale on multiplier to keep bars readable for huge changes
        try:
            # Use absolute percent change to derive a visual fill. Map percent -> 0..100
            # Small changes (0%) -> 0; 100% -> 50; >100% scale up toward 100. Use log scaling
            if price_pct is None:
                pct_fill = 0.0
            else:
                abs_pct = abs(price_pct)
                # normalize: 0% -> 0, 100% -> 50, 10000%+ -> 100
                # map via log10 to compress large ranges
                if abs_pct <= 0:
                    pct_fill = 0.0
                else:
                    lf = math.log10(abs_pct + 1)
                    # choose divisor so that log10(10000)/div ~ 1 -> div ~= 4
                    pct_fill = float(min(100.0, (lf / 4.0) * 100.0))
        except Exception:
            pct_fill = 0.0

        try:
            abp = v.get("avg_buy_price")
            avg_buy = float(abp) if abp is not None else None
        except Exception:
            avg_buy = None
        try:
            fee_total = float(v.get("total_fee", 0) or 0)
        except Exception:
            fee_total = 0.0
        try:
            change_amt = float(v.get("value_change_amount", 0) or 0)
        except Exception:
            change_amt = 0.0

        try:
            tv_sell = float(v.get("total_value_sell", 0) or 0)
        except Exception:
            tv_sell = 0.0

        totals.append(
            {
                "cryptoName": v["cryptoName"],
                "total_qty": tq,
                "total_qty_display": _format_number_trim(v.get("total_qty", 0), 8),
                "total_value": tv,
                "total_value_buy": tv_buy,
                "total_value_buy_display": _format_number_trim(v.get("total_value_buy", 0), 2),
                "total_value_sell": tv_sell,
                "latest_price": lp,
                "latest_price_display": _format_number_trim(v.get("latest_price", 0), 6) if lp else "\u2014",
                "total_value_live": tv_live,
                "total_value_live_display": _format_number_trim(v.get("total_value_live", 0), 2) if tv_live else "\u2014",
                "currency": v.get("currency", ""),
                "price_pct": price_pct if price_pct is not None else 0.0,
                "value_pct": value_pct if value_pct is not None else 0.0,
                "price_multiplier": price_multiplier if price_multiplier is not None else 0.0,
                "price_pct_display": price_pct_display,
                "value_pct_display": value_pct_display,
                "pct_fill": pct_fill,
                "pct_fill_str": f"{pct_fill:.2f}",
                "avg_buy_price": avg_buy,
                "avg_buy_price_display": _format_number_trim(v.get("avg_buy_price", 0), 12) if v.get("avg_buy_price") is not None else "—",
                "total_fee": fee_total,
                "total_fee_display": _format_number_trim(v.get("total_fee", 0), 10) if v.get("total_fee", 0) > 0 else "—",
                "value_change_amount": change_amt,
                "value_change_amount_abs_display": "\u2014" if change_amt is None else _format_number_trim(
                    abs(change_amt), 2
                ),
            }
        )

    # Return all data as JSON for async frontend rendering
    return jsonify({
        "cryptos": cryptos,
        "wallets": wallets,
        "coins": coins,
        "totals": totals,
        "walletHoldings": wallet_holdings,
        "baseCurrency": base_currency,
        "userId": userId,
    })


# @crypto_bp.route('/crypto', methods=['GET'])
# def crypto_page():
#     user = session.get('user')
#     if user:
#         userId = user.get('username')
#         try:
#             response = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
#             cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
#         except Exception as e:
#             print(f"Error fetching cryptos: {e}")
#             cryptos = []

#         try:
#             response = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
#             cryptos = response.json().get("wallets", []) if response.status_code == 200 else []
#         except Exception as e:
#             print(f"Error fetching wallets: {e}")
#             wallets = []

#         return render_template("crypto.html", cryptos=cryptos, wallets=wallets, userId=userId)
#     else:
#         return render_template("home.html")


@crypto_bp.route("/crypto", methods=["POST"])
def create_crypto():
    crypto_id = str(uuid.uuid4())
    user = session.get("user")
    user_id = user.get("username")
    data = {
        "cryptoId": crypto_id,
        "userId": user_id,
        "cryptoName": request.form["cryptoName"],
        "tdate": request.form["tdate"],
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "operation": request.form.get("operation") or request.form.get("side"),
        "feeUnit": request.form.get("feeUnit"),
        "quantity": request.form["quantity"],
        "price": request.form["price"],
        "currency": request.form["currency"],
        "fee": request.form["fee"],
        "note": request.form["note"],
    }

    print(data)
    try:
        response = requests.post(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


@crypto_bp.route("/updateCrypto", methods=["POST"])
def update_crypto():
    user = session.get("user")
    if not user:
        return redirect(url_for("home.home_page"))
    user_id = user.get("username")

    data = {
        "cryptoId": request.form["cryptoId"],
        # Never trust userId from the client; scope to the logged-in user.
        "userId": user_id,
        "cryptoName": request.form["cryptoName"],
        "tdate": request.form["tdate"],
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "operation": request.form.get("operation") or request.form.get("side"),
        "feeUnit": request.form.get("feeUnit"),
        "quantity": request.form["quantity"],
        "price": request.form["price"],
        "currency": request.form["currency"],
        "fee": request.form["fee"],
        "note": request.form["note"],
    }
    print(f"🔄 [DEBUG] Updating crypto: {data}")

    try:
        response = requests.patch(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


@crypto_bp.route("/deleteCrypto/<crypto_id>/<user_id>", methods=["POST"])
def delete_crypto(crypto_id, user_id):
    """Delete a crypto."""
    user = session.get("user")
    if not user:
        return redirect(url_for("home.home_page"))
    session_user_id = user.get("username")

    data = {
        "cryptoId": crypto_id,
        # Never trust userId from the URL; scope to the logged-in user.
        "userId": session_user_id,
    }
    print(f"🗑️ [DEBUG] Deleting crypto: {data}")

    try:
        response = requests.delete(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
