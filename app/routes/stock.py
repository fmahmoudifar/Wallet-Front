from flask import Blueprint, render_template,session, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth, ALPHA_VANTAGE_API_KEY
from decimal import Decimal
from datetime import datetime
import time

# Reuse Settings currency + FX conversion helpers (same as fiat/home)
from .crypto import (
    _get_user_base_currency,
    _get_fx_rate,
    _normalize_currency,
)

stock_bp = Blueprint('stock', __name__)

ALPHAVANTAGE_BASE_URL = "https://www.alphavantage.co/query"

# In-process caches (same approach as crypto.py)
_AV_SEARCH_CACHE = {}
_AV_SEARCH_TTL_SECONDS = 900
_AV_QUOTE_CACHE = {}
_AV_QUOTE_TTL_SECONDS = 120


def _to_decimal(val) -> Decimal:
    try:
        if val is None:
            return Decimal(0)
        s = str(val).strip()
        if s == '':
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
    if ccy == 'GBX':
        try:
            return (amount * Decimal('0.01'), 'GBP')
        except Exception:
            return (amount, 'GBP')
    return (amount, ccy)


def _require_user():
    user = session.get('user')
    if not user:
        return None
    return user


def _av_get(params):
    if not ALPHA_VANTAGE_API_KEY:
        raise RuntimeError("Missing ALPHA_VANTAGE_API_KEY")
    p = dict(params or {})
    p["apikey"] = ALPHA_VANTAGE_API_KEY
    headers = {
        'Accept': 'application/json',
        'User-Agent': 'Wallet-Front/1.0'
    }
    r = requests.get(ALPHAVANTAGE_BASE_URL, params=p, headers=headers, timeout=12)
    r.raise_for_status()
    data = r.json() if r.content else {}
    if isinstance(data, dict):
        if data.get('Note'):
            raise RuntimeError(data.get('Note'))
        if data.get('Error Message'):
            raise RuntimeError(data.get('Error Message'))
    return data


def _av_search(keywords: str, limit: int = 8):
    q = (keywords or '').strip()
    if not q:
        return []
    now = time.time()
    cached = _AV_SEARCH_CACHE.get(q.lower())
    if cached and (now - cached.get('ts', 0.0) < _AV_SEARCH_TTL_SECONDS):
        return cached.get('data') or []

    data = _av_get({"function": "SYMBOL_SEARCH", "keywords": q})
    out = []
    for m in (data.get('bestMatches') or []):
        sym = (m.get('1. symbol') or '').strip()
        name = (m.get('2. name') or '').strip()
        region = (m.get('4. region') or '').strip()
        currency = (m.get('8. currency') or '').strip()
        if sym and name:
            out.append({"symbol": sym, "name": name, "region": region, "currency": currency})
        if len(out) >= max(1, int(limit)):
            break
    _AV_SEARCH_CACHE[q.lower()] = {"ts": now, "data": out}
    return out


def _av_metadata_for_symbol(symbol: str):
    s = (symbol or '').strip().upper()
    if not s:
        return {"name": "", "currency": "", "region": ""}
    matches = _av_search(s, limit=10)
    for m in matches:
        if (m.get('symbol') or '').strip().upper() == s:
            return {"name": m.get('name') or '', "currency": m.get('currency') or '', "region": m.get('region') or ''}
    if matches:
        m = matches[0]
        return {"name": m.get('name') or '', "currency": m.get('currency') or '', "region": m.get('region') or ''}
    return {"name": "", "currency": "", "region": ""}


@stock_bp.route('/stock/search', methods=['GET'])
def stock_search():
    user = _require_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({"results": []})

    try:
        matches = _av_search(q, limit=8)
        return jsonify({"results": matches})
    except Exception as e:
        msg = str(e)
        if "Missing ALPHA_VANTAGE_API_KEY" in msg:
            return jsonify({"error": msg, "results": []}), 500
        # rate-limit/provider errors
        if msg.startswith('Thank you for using Alpha Vantage') or 'rate limit' in msg.lower():
            return jsonify({"error": msg, "results": []}), 429
        return jsonify({"error": "Failed to search", "details": msg, "results": []}), 500


@stock_bp.route('/stock/quote', methods=['GET'])
def stock_quote():
    user = _require_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = user.get('username')
    base_currency = _get_user_base_currency(user_id)

    symbol = (request.args.get('symbol') or '').strip()
    if not symbol:
        return jsonify({"error": "Missing symbol"}), 400

    sym = symbol.strip().upper()
    now = time.time()
    # Include a version tag so behavior changes don't serve stale cached values.
    cache_key = (sym, base_currency, 'quote_v2')
    cached = _AV_QUOTE_CACHE.get(cache_key)
    if cached and (now - cached.get('ts', 0.0) < _AV_QUOTE_TTL_SECONDS):
        return jsonify(cached.get('data') or {})

    try:
        data = _av_get({"function": "GLOBAL_QUOTE", "symbol": sym})
        gq = data.get('Global Quote') or {}
        price_raw = (gq.get('05. price') or '').strip()
        try:
            price = float(price_raw)
        except Exception:
            price = None

        meta = _av_metadata_for_symbol(sym)

        region_txt = str((meta.get('region') or '')).strip().upper()
        quote_ccy_raw = _normalize_currency(meta.get('currency'), '') or base_currency

        # Heuristic: Many LSE instruments are quoted in pence (GBX). Alpha Vantage can
        # return large numeric values (e.g. 6315) that should be interpreted as 63.15.
        # We treat UK/LSE tickers as pence when the quote price is "too large" for GBP.
        effective_ccy = quote_ccy_raw
        try:
            is_uk = ('UNITED KINGDOM' in region_txt) or sym.endswith('.LON') or sym.endswith('.L')
            if is_uk and price is not None:
                p = float(price)
                # Prices in GBP rarely exceed 1000; 1000+ is almost always pence.
                if p >= 1000:
                    effective_ccy = 'GBX'
        except Exception:
            pass

        price_major, quote_ccy = _scale_minor_currency(
            _to_decimal(price) if price is not None else Decimal(0),
            effective_ccy,
        )

        # Preserve original numeric price for reference; but if the quote currency was minor-unit,
        # normalize the displayed price to major units.
        price_display = None
        if price is not None:
            try:
                # If we normalized minor units, show the major-unit price.
                price_display = float(price_major) if effective_ccy == 'GBX' else float(price)
            except Exception:
                price_display = price

        price_base = None
        try:
            if price_display is not None:
                fx = _get_fx_rate(quote_ccy, base_currency)
                price_base = float(_to_decimal(price_display) * fx)
        except Exception:
            # If FX conversion fails (unsupported currency / network), keep quote currency only.
            price_base = None

        out = {
            "symbol": sym,
            "name": meta.get('name') or '',
            "currency": quote_ccy,
            "price": price_display,
            "currencyBase": base_currency,
            "priceBase": price_base,
            "asof": (gq.get('07. latest trading day') or ''),
        }
        _AV_QUOTE_CACHE[cache_key] = {"ts": now, "data": out}
        return jsonify(out)
    except Exception as e:
        msg = str(e)
        if "Missing ALPHA_VANTAGE_API_KEY" in msg:
            return jsonify({"error": msg, "symbol": sym, "price": None}), 500
        if msg.startswith('Thank you for using Alpha Vantage') or 'rate limit' in msg.lower():
            return jsonify({"error": msg, "symbol": sym, "price": None}), 429
        return jsonify({"error": "Failed to fetch quote", "details": msg, "symbol": sym, "price": None}), 500

@stock_bp.route('/stock', methods=['GET'])
def stock_page():
    user = session.get('user')
    if user:
        userId = user.get('username')

        base_currency = _get_user_base_currency(userId)
        fx_warning = False
        try:
            # Assuming your API accepts a username filter as a query parameter
            response = requests.get(f"{API_URL}/stocks", params={"userId": userId}, auth=aws_auth)
            stocks = response.json().get("stocks", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching stocks: {e}")
            stocks = []

        # Convert transaction price/fee/value into website/base currency for portfolio display.
        for s in stocks:
            try:
                tx_ccy_raw = _normalize_currency(s.get('currency'), base_currency)
                qty = _to_decimal(s.get('quantity', 0))
                price_raw = _to_decimal(s.get('price', 0))
                fee_raw = _to_decimal(s.get('fee', 0))

                price_major, tx_ccy = _scale_minor_currency(price_raw, tx_ccy_raw)
                fee_major, _ = _scale_minor_currency(fee_raw, tx_ccy_raw)

                # Only convert when tx currency differs.
                fx = _get_fx_rate(tx_ccy, base_currency)

                s['currencyBase'] = base_currency
                s['priceBase'] = float(price_major * fx)
                s['feeBase'] = float(fee_major * fx)
                s['valuePaidBase'] = float(((qty * price_major) + fee_major) * fx)
                s['baseCurrency'] = base_currency
            except Exception:
                # If FX fails, leave base fields absent and let UI fall back to original.
                fx_warning = True

        # --- Fetch Wallets (for From/To dropdowns) ---
        try:
            response = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
            wallets = response.json().get("wallets", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching wallets: {e}")
            wallets = []

        return render_template(
            "stock.html",
            stocks=stocks,
            wallets=wallets,
            userId=userId,
            base_currency=base_currency,
            fx_warning=fx_warning,
        )
    else:
        return render_template("home.html")
    
# @stock_bp.route('/stock', methods=['GET'])
# def stock_page():
#     try:
#         response = requests.get(f"{API_URL}/stocks", auth=aws_auth)
#         stocks = response.json().get("stocks", []) if response.status_code == 200 else []
#     except Exception:
#         stocks = []
#     return render_template("stock.html", stocks=stocks)

@stock_bp.route('/stock', methods=['POST'])
def create_stock():
    stock_id = str(uuid.uuid4())
    user = session.get('user')
    user_id = user.get('username')
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
        "note": request.form["note"]
    }

    print(data)
    try:
        response = requests.post(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@stock_bp.route('/updateStock', methods=['POST'])
def update_stock():
    data = {
        "stockId": request.form["stockId"],
        "userId": request.form["userId"],
        "stockName": request.form["stockName"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "operation": request.form.get("operation") or request.form.get("side"),
        "quantity": request.form["quantity"],  
        "price": request.form["price"],  
        "currency": request.form["currency"], 
        "fee": request.form["fee"],  
        "note": request.form["note"]
    }
    print(f"🔄 [DEBUG] Updating stock: {data}")
    
    try:
        response = requests.patch(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@stock_bp.route('/deletestock/<stock_id>/<user_id>', methods=['POST'])
def delete_stock(stock_id, user_id):
    """Delete a stock."""
    data = {
        "stockId": stock_id,
        "userId": user_id
    }
    print(f"🗑️ [DEBUG] Deleting stock: {data}")

    try:
        response = requests.delete(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500