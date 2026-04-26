import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.services.user_scope import filter_records_by_user
from config import API_URL, aws_auth

# Reuse Settings currency + FX conversion helpers (same as fiat/home)
from .crypto import (
    _get_fx_rate,
    _get_user_base_currency,
    _normalize_currency,
)

stock_bp = Blueprint("stock", __name__)

# Yahoo Finance endpoints (public)
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

# In-process cache for Yahoo queries
_YH_SEARCH_CACHE = {}
_YH_SEARCH_TTL_SECONDS = 900
_YH_QUOTE_CACHE = {}
_YH_QUOTE_TTL_SECONDS = 3600  # 1 hour


def _to_decimal(val) -> Decimal:
    try:
        if val is None:
            return Decimal(0)
        s = str(val).strip().replace(",", ".")
        if s == "":
            return Decimal(0)
        return Decimal(s)
    except Exception:
        return Decimal(0)


def _scale_minor_currency(amount: Decimal, currency: str):
    """Convert minor-unit quoted amounts into major units.

    Some exchanges quote in minor units (e.g., GBX = pence). Alpha Vantage can
    return those raw values; if we treat them as GBP, prices/values are 100x.

    Returns: (scaled_amount, normalized_currency)
    """
    ccy = _normalize_currency(currency)
    if ccy == "GBX":
        try:
            return (amount * Decimal("0.01"), "GBP")
        except Exception:
            return (amount, "GBP")
    return (amount, ccy)


def _require_user():
    user = session.get("user")
    if not user:
        return None
    return user



def _yh_get(url: str, params: dict):
    headers = {"Accept": "application/json", "User-Agent": "Wallet-Front/1.0"}
    r = requests.get(url, params=params, headers=headers, timeout=12)
    r.raise_for_status()
    return r.json() if r.content else {}


def _yh_search(keywords: str, limit: int = 8):
    q = (keywords or "").strip()
    if not q:
        return []
    now = time.time()
    cached = _YH_SEARCH_CACHE.get(q.lower())
    if cached and (now - cached.get("ts", 0.0) < _YH_SEARCH_TTL_SECONDS):
        return cached.get("data") or []

    data = _yh_get(
        YAHOO_SEARCH_URL,
        {
            "q": q,
            "quotesCount": max(1, int(limit)),
            "newsCount": 0,
            "enableFuzzyQuery": True,
            "lang": "en-US",
            "region": "US",
        },
    )

    out = []
    for item in data.get("quotes") or []:
        sym = (item.get("symbol") or "").strip()
        name = (item.get("shortname") or item.get("longname") or item.get("quoteType") or "").strip()
        region = (item.get("exchange") or item.get("fullExchangeName") or item.get("exchDisp") or "").strip()
        currency_raw = (item.get("currency") or "").strip()
        # Yahoo sometimes returns GBp for LSE quotes (pence). Treat as GBX so we can scale.
        if currency_raw and currency_raw.upper() == "GBP" and currency_raw != currency_raw.upper():
            currency = "GBX"
        else:
            currency = currency_raw
        # Keep results consistent with Alpha Vantage response
        if sym and name:
            out.append({"symbol": sym, "name": name, "region": region, "currency": currency})
        if len(out) >= max(1, int(limit)):
            break

    _YH_SEARCH_CACHE[q.lower()] = {"ts": now, "data": out}
    return out


def _yh_quote(symbol: str):
    requested = (symbol or "").strip().upper()
    if not requested:
        return {"symbol": "", "name": "", "currency": "", "price": None, "asof": ""}

    # Normalize some common provider suffixes for Yahoo.
    # Alpha Vantage often uses .LON whereas Yahoo uses .L
    yahoo_sym = requested
    try:
        if yahoo_sym.endswith(".LON"):
            yahoo_sym = yahoo_sym[:-4] + ".L"
    except Exception:
        yahoo_sym = requested

    now = time.time()
    cached = _YH_QUOTE_CACHE.get(requested)
    if cached and (now - cached.get("ts", 0.0) < _YH_QUOTE_TTL_SECONDS):
        return cached.get("data") or {}

    # NOTE: In this environment Yahoo's v7/finance/quote responds 401.
    # The chart endpoint works and provides currency + regularMarketPrice in meta.
    url = f"{YAHOO_CHART_URL}/{yahoo_sym}"
    data = _yh_get(url, {"interval": "1d", "range": "1d"})
    chart = data.get("chart") or {}
    results = chart.get("result") or []
    meta = (results[0] or {}).get("meta") if results else {}

    price = meta.get("regularMarketPrice")
    if price is None:
        price = meta.get("chartPreviousClose")

    currency_raw = str(meta.get("currency") or "").strip()
    # Yahoo sometimes returns GBp for LSE quotes (pence). Treat as GBX so we can scale.
    if currency_raw and currency_raw.upper() == "GBP" and currency_raw != currency_raw.upper():
        currency = "GBX"
    else:
        currency = _normalize_currency(currency_raw, "")

    name = str(meta.get("shortName") or meta.get("longName") or "").strip()
    asof = ""
    try:
        ts = meta.get("regularMarketTime")
        if ts:
            asof = datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        asof = ""

    out = {
        "symbol": requested,
        "name": name,
        "currency": currency,
        "price": float(price) if isinstance(price, int | float) else None,
        "asof": asof,
    }
    _YH_QUOTE_CACHE[requested] = {"ts": now, "data": out}
    return out


def _yh_quote_batch(symbols: list) -> dict:
    """Fetch multiple stock quotes in parallel using ThreadPoolExecutor.
    
    Args:
        symbols: List of stock symbols (e.g., ['AAPL', 'SGLN.L', 'MSFT'])
    
    Returns:
        Dict mapping symbol -> {symbol, name, currency, price, asof}
    """
    if not symbols:
        return {}

    now = time.time()
    
    # Separate cached vs uncached symbols
    result = {}
    uncached_syms = []
    
    for sym in symbols:
        sym_upper = (sym or "").strip().upper()
        if not sym_upper:
            continue
            
        cached = _YH_QUOTE_CACHE.get(sym_upper)
        if cached and (now - cached.get("ts", 0.0) < _YH_QUOTE_TTL_SECONDS):
            result[sym_upper] = cached.get("data", {})
        else:
            uncached_syms.append(sym_upper)
    
    # If all cached, return early
    if not uncached_syms:
        return result
    
    # Parallelize uncached symbol fetches (5 workers)
    def _fetch_one(sym):
        try:
            return _yh_quote(sym)
        except Exception as e:
            print(f"Error fetching {sym}: {e}")
            return {
                "symbol": sym,
                "name": "",
                "currency": "",
                "price": None,
                "asof": "",
            }
    
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_one, sym): sym for sym in uncached_syms}
            for future in as_completed(futures):
                quote_data = future.result()
                sym = quote_data.get("symbol", "")
                if sym:
                    result[sym] = quote_data
    except Exception as e:
        print(f"Error in ThreadPoolExecutor: {e}")
        # Fallback to sequential
        for sym in uncached_syms:
            quote_data = _fetch_one(sym)
            sym_key = quote_data.get("symbol", "")
            if sym_key:
                result[sym_key] = quote_data
    
    return result

@stock_bp.route("/stock/search", methods=["GET"])
def stock_search():
    user = _require_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})

    # Use Yahoo Finance for stock search
    try:
        matches = _yh_search(q, limit=8)
        return jsonify({"results": matches, "provider": "yahoo"})
    except Exception as e:
        return jsonify({"error": "Failed to search", "details": str(e), "results": []}), 500


@stock_bp.route("/stock/quote", methods=["GET"])
def stock_quote():
    user = _require_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = user.get("username")
    base_currency = _get_user_base_currency(user_id)

    symbol = (request.args.get("symbol") or "").strip()
    if not symbol:
        return jsonify({"error": "Missing symbol"}), 400

    sym = symbol.strip().upper()
    now = time.time()

    def _finalize_quote(
        symbol_out: str, name: str, currency_raw: str, price_num, asof: str
    ):
        quote_ccy_raw = _normalize_currency(currency_raw, "") or base_currency
        
        # Detect UK stocks (ending in .L or .LON) and treat as GBX if price suggests pence
        effective_ccy = quote_ccy_raw
        try:
            if price_num is not None and quote_ccy_raw == "GBP":
                is_uk = symbol_out.endswith(".L") or symbol_out.endswith(".LON")
                # If UK stock with high price, likely in pence (GBX)
                if is_uk and float(price_num) >= 100:
                    effective_ccy = "GBX"
        except Exception:
            pass

        price_major, quote_ccy = _scale_minor_currency(
            _to_decimal(price_num) if price_num is not None else Decimal(0),
            effective_ccy,
        )

        price_display = None
        if price_num is not None:
            try:
                price_display = float(price_major)
            except Exception:
                price_display = price_num

        price_base = None
        try:
            if price_display is not None:
                fx = _get_fx_rate(quote_ccy, base_currency)
                price_base = float(_to_decimal(price_display) * fx)
        except Exception:
            price_base = None

        out = {
            "symbol": symbol_out,
            "name": name or "",
            "currency": quote_ccy,
            "price": price_display,
            "currencyBase": base_currency,
            "priceBase": price_base,
            "asof": asof or "",
            "provider": "yahoo",
        }
        return out

    # Use Yahoo Finance for quotes
    cached = _YH_QUOTE_CACHE.get(sym)
    if cached and (now - cached.get("ts", 0.0) < _YH_QUOTE_TTL_SECONDS):
        yh = cached.get("data") or {}
        out = _finalize_quote(
            sym,
            yh.get("name") or "",
            yh.get("currency") or base_currency,
            yh.get("price"),
            yh.get("asof") or "",
        )
        return jsonify(out)

    try:
        yh = _yh_quote(sym)
        out = _finalize_quote(
            sym,
            yh.get("name") or "",
            yh.get("currency") or base_currency,
            yh.get("price"),
            yh.get("asof") or "",
        )
        return jsonify(out)
    except Exception as e:
        return (
            jsonify({"error": "Failed to fetch quote", "details": str(e), "symbol": sym, "price": None}),
            500,
        )


@stock_bp.route("/stock/quotes", methods=["GET"])
def stock_quotes_bulk():
    """Bulk stock prices endpoint. Takes comma-separated symbols and returns all prices in one request.
    
    Uses parallelized Yahoo Finance calls with caching.
    
    Query params:
    - symbols: comma-separated list of stock symbols (e.g., "AAPL,MSFT,SGLN.L")
    
    Returns: {AAPL: {price, currency, priceBase, currencyBase, ...}, MSFT: {...}, ...}
    """
    user = _require_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    symbols_param = request.args.get("symbols", "").strip()
    if not symbols_param:
        return jsonify({})

    user_id = user.get("username")
    base_currency = _get_user_base_currency(user_id)

    # Parse comma-separated symbols
    symbols = [s.strip().upper() for s in symbols_param.split(",") if s.strip()]
    if not symbols:
        return jsonify({})

    def _finalize_quote_bulk(
        symbol_out: str, name: str, currency_raw: str, price_num, asof: str
    ):
        quote_ccy_raw = _normalize_currency(currency_raw, "") or base_currency
        
        # Detect UK stocks (ending in .L or .LON) and treat as GBX if price suggests pence
        effective_ccy = quote_ccy_raw
        try:
            if price_num is not None and quote_ccy_raw == "GBP":
                is_uk = symbol_out.endswith(".L") or symbol_out.endswith(".LON")
                # If UK stock with high price, likely in pence (GBX)
                if is_uk and float(price_num) >= 100:
                    effective_ccy = "GBX"
        except Exception:
            pass

        price_major, quote_ccy = _scale_minor_currency(
            _to_decimal(price_num) if price_num is not None else Decimal(0),
            effective_ccy,
        )

        price_display = None
        if price_num is not None:
            try:
                price_display = float(price_major)
            except Exception:
                price_display = price_num

        price_base = None
        try:
            if price_display is not None:
                fx = _get_fx_rate(quote_ccy, base_currency)
                price_base = float(_to_decimal(price_display) * fx)
        except Exception:
            price_base = None

        return {
            "symbol": symbol_out,
            "name": name or "",
            "currency": quote_ccy,
            "price": price_display,
            "currencyBase": base_currency,
            "priceBase": price_base,
            "asof": asof or "",
            "provider": "yahoo",
        }

    # BATCH fetch all stocks in parallel (not sequential!)
    quotes_batch = _yh_quote_batch(symbols)

    # Build response
    out = {}
    try:
        for sym in symbols:
            try:
                yh = quotes_batch.get(sym, {})
                
                q = _finalize_quote_bulk(
                    sym,
                    yh.get("name") or "",
                    yh.get("currency") or base_currency,
                    yh.get("price"),
                    yh.get("asof") or "",
                )
                out[sym] = q
            except Exception as e:
                print(f"Error in stock_quotes_bulk for '{sym}': {e}")
                out[sym] = {"error": "Failed to fetch quote", "symbol": sym, "price": None}
    except Exception as e:
        print(f"Error in stock_quotes_bulk: {e}")

    return jsonify(out)


@stock_bp.route("/stock", methods=["GET"])
def stock_page():
    user = session.get("user")
    if user:
        userId = user.get("username")
        base_currency = _get_user_base_currency(userId)
        return render_template("stock.html", base_currency=base_currency, userId=userId)
    else:
        return render_template("home.html")


@stock_bp.route("/api/stock-data", methods=["GET"])
def stock_data():
    user = session.get("user")
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    userId = user.get("username")
    base_currency = _get_user_base_currency(userId)
    fx_warning = False

    stocks = []
    wallets = []

    def _fetch_stocks():
        resp = requests.get(f"{API_URL}/stocks", params={"userId": userId}, auth=aws_auth)
        items = resp.json().get("stocks", []) if resp.status_code == 200 else []
        return filter_records_by_user(items, userId)

    def _fetch_wallets():
        resp = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
        items = resp.json().get("wallets", []) if resp.status_code == 200 else []
        return filter_records_by_user(items, userId)

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_s = ex.submit(_fetch_stocks)
            fut_w = ex.submit(_fetch_wallets)
            stocks = fut_s.result()
            wallets = fut_w.result()
    except Exception as e:
        print(f"Error fetching stocks/wallets: {e}")

    # Convert transaction price/fee/value into website/base currency for portfolio display.
    for s in stocks:
        try:
            tx_ccy_raw = _normalize_currency(s.get("currency"), base_currency)
            fee_ccy_raw = _normalize_currency(s.get("feeCurrency"), tx_ccy_raw)
            qty = _to_decimal(s.get("quantity", 0))
            price_raw = _to_decimal(s.get("price", 0))
            fee_raw = _to_decimal(s.get("fee", 0))

            price_major, tx_ccy = _scale_minor_currency(price_raw, tx_ccy_raw)
            fee_major, fee_ccy = _scale_minor_currency(fee_raw, fee_ccy_raw)

            fx_price = _get_fx_rate(tx_ccy, base_currency)
            fx_fee = _get_fx_rate(fee_ccy, base_currency)

            s["currencyBase"] = base_currency
            s["priceBase"] = float(price_major * fx_price)
            s["feeBase"] = float(fee_major * fx_fee)
            s["valuePaidBase"] = float((qty * price_major * fx_price) + (fee_major * fx_fee))
            s["baseCurrency"] = base_currency
            s["feeCurrency"] = fee_ccy
        except Exception:
            fx_warning = True

    return jsonify({
        "stocks": stocks,
        "wallets": wallets,
        "base_currency": base_currency,
        "fx_warning": fx_warning,
        "userId": userId,
    })


# @stock_bp.route('/stock', methods=['GET'])
# def stock_page():
#     try:
#         response = requests.get(f"{API_URL}/stocks", auth=aws_auth)
#         stocks = response.json().get("stocks", []) if response.status_code == 200 else []
#     except Exception:
#         stocks = []
#     return render_template("stock.html", stocks=stocks)


@stock_bp.route("/stock", methods=["POST"])
def create_stock():
    stock_id = str(uuid.uuid4())
    user = session.get("user")
    user_id = user.get("username")
    data = {
        "stockId": stock_id,
        "userId": user_id,
        "stockName": request.form["stockName"],
        "tdate": request.form["tdate"],
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "operation": request.form.get("operation") or request.form.get("side"),
        "quantity": request.form["quantity"],
        "price": request.form["price"],
        "currency": request.form["currency"],
        "fee": request.form["fee"],
        "feeCurrency": request.form.get("feeCurrency") or request.form.get("currency"),
        "note": request.form["note"],
    }

    print(data)
    try:
        response = requests.post(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


@stock_bp.route("/updateStock", methods=["POST"])
def update_stock():
    user = session.get("user")
    if not user:
        return redirect(url_for("home.home_page"))
    user_id = user.get("username")

    data = {
        "stockId": request.form["stockId"],
        # Never trust userId from the client; scope to the logged-in user.
        "userId": user_id,
        "stockName": request.form["stockName"],
        "tdate": request.form["tdate"],
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "operation": request.form.get("operation") or request.form.get("side"),
        "quantity": request.form["quantity"],
        "price": request.form["price"],
        "currency": request.form["currency"],
        "fee": request.form["fee"],
        "feeCurrency": request.form.get("feeCurrency") or request.form.get("currency"),
        "note": request.form["note"],
    }
    print(f"🔄 [DEBUG] Updating stock: {data}")

    try:
        response = requests.patch(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


@stock_bp.route("/deletestock/<stock_id>/<user_id>", methods=["POST"])
def delete_stock(stock_id, user_id):
    """Delete a stock."""
    user = session.get("user")
    if not user:
        return redirect(url_for("home.home_page"))
    session_user_id = user.get("username")

    data = {
        "stockId": stock_id,
        # Never trust userId from the URL; scope to the logged-in user.
        "userId": session_user_id,
    }
    print(f"🗑️ [DEBUG] Deleting stock: {data}")

    try:
        response = requests.delete(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
