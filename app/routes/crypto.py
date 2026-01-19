from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth
from collections import defaultdict
from decimal import Decimal
from decimal import ROUND_HALF_UP
import math
from datetime import datetime
import time

crypto_bp = Blueprint('crypto', __name__)

# CoinGecko markets cache (in-process) to reduce rate-limit and avoid showing 0 prices.
# Cache is keyed by vs_currency so EUR/USD results don't get mixed.
_COINGECKO_MARKETS_CACHE = {}
_COINGECKO_MARKETS_TTL_SECONDS = 300

# FX rates cache (in-process) to convert transaction currency -> website currency
_FX_RATES_CACHE = {}
_FX_RATES_TTL_SECONDS = 3600


def _normalize_currency(code: str, default: str = '') -> str:
    try:
        c = (code or '').strip().upper()
        return c if c else default
    except Exception:
        return default


def _get_user_base_currency(user_id: str) -> str:
    """Return the website/base currency (from Settings). Defaults to EUR."""
    # Prefer session value (updated when visiting/updating settings)
    base = _normalize_currency(session.get('currency'), '')
    if base:
        return base

    # Fallback: fetch settings directly
    try:
        resp = requests.get(f"{API_URL}/settings", params={"userId": user_id}, auth=aws_auth, timeout=10)
        if resp.status_code == 200:
            settings = resp.json().get('settings', [])
            if settings and isinstance(settings, list):
                currency = _normalize_currency((settings[0] or {}).get('currency'), 'EUR')
                if currency:
                    session['currency'] = currency
                    return currency
    except Exception:
        pass

    return 'EUR'


def _get_fx_rate(from_currency: str, to_currency: str) -> Decimal:
    """Get latest FX rate from_currency -> to_currency using Frankfurter (cached)."""
    from_ccy = _normalize_currency(from_currency)
    to_ccy = _normalize_currency(to_currency)
    if from_ccy == to_ccy:
        return Decimal(1)

    key = (from_ccy, to_ccy)
    now = time.time()
    cached = _FX_RATES_CACHE.get(key)
    if cached and (now - cached.get('ts', 0.0) < _FX_RATES_TTL_SECONDS):
        return cached['rate']

    # Frankfurter supports many fiat currencies. Crypto tx currencies should be normalized before calling.
    url = "https://api.frankfurter.app/latest"
    params = {"from": from_ccy, "to": to_ccy}
    headers = {
        'Accept': 'application/json',
        'User-Agent': 'Wallet-Front/1.0'
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json() or {}
            rates = data.get('rates') or {}
            rate_val = rates.get(to_ccy)
            if rate_val is None:
                raise ValueError(f"Missing FX rate {from_ccy}->{to_ccy}")
            rate = Decimal(str(rate_val))
            _FX_RATES_CACHE[key] = {"ts": now, "rate": rate}
            return rate

        # Non-200: fall back to cached, if available
        if cached:
            return cached['rate']
        raise RuntimeError(f"FX API returned {r.status_code}")
    except Exception:
        # On network/parsing errors, prefer cache rather than breaking totals.
        if cached:
            return cached['rate']
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
        return '0'

    try:
        if max_decimals is None:
            s = format(d, 'f')
        else:
            q = Decimal('1').scaleb(-int(max_decimals))  # 10^-max_decimals
            d = d.quantize(q, rounding=ROUND_HALF_UP)
            s = format(d, 'f')
    except Exception:
        s = str(d)

    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    # avoid showing '-0'
    if s in ('-0', '-0.0'):
        s = '0'
    return s or '0'

@crypto_bp.route('/crypto', methods=['GET'])
def crypto_page():
    user = session.get('user')
    if user:
        userId = user.get('username')

        base_currency = _get_user_base_currency(userId)
        # CoinGecko vs_currency must be lowercase; only use commonly supported fiats here.
        cg_vs_currency = (base_currency or 'EUR').lower()
        if cg_vs_currency not in ('eur', 'usd'):
            cg_vs_currency = 'eur'

        # --- Fetch Cryptos ---
        try:
            response = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
            cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching cryptos: {e}")
            cryptos = []

        # --- Fetch Wallets ---
        try:
            response = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
            wallets = response.json().get("wallets", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching wallets: {e}")
            wallets = []

        # Map wallet names to ids to handle APIs that store walletName in fromWallet/toWallet.
        wallet_ids_set = set()
        wallet_id_by_name = {}
        try:
            for w in (wallets or []):
                wid = (w.get('walletId') or '').strip()
                wname = (w.get('walletName') or '').strip()
                if wid:
                    wallet_ids_set.add(wid)
                if wid and wname:
                    wallet_id_by_name[wname] = wid
        except Exception:
            wallet_ids_set = set()
            wallet_id_by_name = {}

        def _resolve_wallet_ref(val):
            s = (str(val) if val is not None else '').strip()
            if not s:
                return ''
            if s in wallet_ids_set:
                return s
            return wallet_id_by_name.get(s, s)

        # --- Fetch top coins from CoinGecko (markets) to populate dropdown ---
        coins = []
        try:
            cg_url = "https://api.coingecko.com/api/v3/coins/markets"
            cg_params = {"vs_currency": cg_vs_currency, "order": "market_cap_desc", "per_page": 500, "page": 1}
            now = time.time()

            cache_bucket = _COINGECKO_MARKETS_CACHE.get(cg_vs_currency) or {'ts': 0.0, 'data': []}

            # Serve from cache if still fresh
            if cache_bucket['data'] and (now - cache_bucket['ts'] < _COINGECKO_MARKETS_TTL_SECONDS):
                coins = cache_bucket['data']
            else:
                cg_headers = {
                    'Accept': 'application/json',
                    'User-Agent': 'Wallet-Front/1.0'
                }
                cg_resp = requests.get(cg_url, params=cg_params, headers=cg_headers, timeout=10)
                if cg_resp.status_code == 200:
                    coins = cg_resp.json()
                    _COINGECKO_MARKETS_CACHE[cg_vs_currency] = {'data': coins, 'ts': now}
                else:
                    # If CoinGecko fails (e.g. rate limit), fall back to the last cached data.
                    if cache_bucket['data']:
                        coins = cache_bucket['data']
                        print(f"CoinGecko returned status {cg_resp.status_code}; using cached markets")
                    else:
                        print(f"CoinGecko returned status {cg_resp.status_code}")
        except Exception as e:
            # On transient network errors, prefer cache rather than showing 0s everywhere.
            cache_bucket = _COINGECKO_MARKETS_CACHE.get(cg_vs_currency) or {'ts': 0.0, 'data': []}
            if cache_bucket['data']:
                coins = cache_bucket['data']
                print(f"Error fetching CoinGecko coins: {e}; using cached markets")
            else:
                print(f"Error fetching CoinGecko coins: {e}")

        # --- Compute totals per crypto using weighted average price method ---
        totals_map = {}
        wallet_crypto_qty = defaultdict(lambda: defaultdict(lambda: Decimal(0)))
        wallet_ids_seen = set()
        try:
            # Group transactions by crypto and sort by date to process chronologically
            crypto_transactions = {}
            for c in cryptos:
                name = c.get('cryptoName') or 'Unknown'
                if name not in crypto_transactions:
                    crypto_transactions[name] = []
                crypto_transactions[name].append(c)
            
            def to_decimal(val):
                """Safely convert API value to Decimal; treat blanks/None as 0."""
                try:
                    if val is None:
                        return Decimal(0)
                    s = str(val).strip()
                    if s == '':
                        return Decimal(0)
                    return Decimal(s)
                except Exception:
                    return Decimal(0)

            def norm_crypto_key(raw_name):
                """Normalize stored cryptoName to a stable key for wallet holdings.
                Prefers the leading symbol in patterns like 'BTC - Bitcoin'; otherwise uses the trimmed upper string.
                """
                s = (str(raw_name) if raw_name is not None else '').strip()
                if not s:
                    return 'UNKNOWN'
                if ' - ' in s:
                    s = s.split(' - ', 1)[0].strip()
                # Remove common wrappers
                s = s.replace('(', ' ').replace(')', ' ')
                s = ' '.join(s.split())
                return s.upper()
            
            # Sort each crypto's transactions by date
            for name in crypto_transactions:
                crypto_transactions[name].sort(key=lambda x: x.get('tdate', ''))
            
            # Process each crypto's transactions chronologically
            for name, transactions in crypto_transactions.items():
                entry = {
                    'cryptoName': name,
                    'total_qty': Decimal(0),           # Current holding quantity
                    'total_cost': Decimal(0),          # Current total cost basis
                    'total_fee': Decimal(0),           # Total fees paid
                    'total_value_buy': Decimal(0),     # Total spent on purchases
                    'total_value_sell': Decimal(0),    # Total received from sales
                    'currency': base_currency
                }
                
                for tx in transactions:
                    try:
                        qty = to_decimal(tx.get('quantity', 0))
                        price = to_decimal(tx.get('price', 0))
                        fee = to_decimal(tx.get('fee', 0))
                        operation = str(tx.get('operation') or tx.get('side') or 'buy').lower()
                        fee_unit = str(tx.get('feeUnit') or '').strip().lower()
                        ckey = norm_crypto_key(tx.get('cryptoName') or name)

                        # TRANSFER: fee is a crypto quantity.
                        # Net received quantity = max(0, qty - fee).
                        # - From wallet loses qty (gross)
                        # - To wallet receives (qty - fee) (net)
                        # Portfolio delta = (+net if toWallet else 0) - (qty if fromWallet else 0)
                        if operation == 'transfer':
                            from_wallet = _resolve_wallet_ref(tx.get('fromWallet'))
                            to_wallet = _resolve_wallet_ref(tx.get('toWallet'))

                            # Transfer fee is expected to be a crypto quantity when feeUnit == 'crypto'.
                            # If older records have feeUnit missing or set differently, treat fee as 0 for qty math.
                            fee_qty = fee if (fee_unit == 'crypto' or fee_unit == '') else Decimal(0)
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
                                entry['total_qty'] += delta_qty
                            elif delta_qty < 0:
                                # Outgoing amount reduces holdings at average cost (no revenue).
                                qty_to_remove = -delta_qty
                                if entry['total_qty'] and entry['total_qty'] > 0:
                                    avg_cost_per_unit = entry['total_cost'] / entry['total_qty']
                                    can_remove = min(qty_to_remove, entry['total_qty'])
                                    entry['total_qty'] -= can_remove
                                    entry['total_cost'] -= (can_remove * avg_cost_per_unit)
                                    remaining = qty_to_remove - can_remove
                                    if remaining > 0:
                                        # reflect as negative holdings (no additional cost basis change)
                                        entry['total_qty'] -= remaining
                                else:
                                    entry['total_qty'] -= qty_to_remove
                            continue

                        tx_currency = _normalize_currency(tx.get('currency'), base_currency)
                        fx_rate = _get_fx_rate(tx_currency, base_currency)
                        
                        # Transaction value (qty * price + fee)
                        tx_value = (qty * price) + fee
                        tx_value_base = tx_value * fx_rate
                        fee_base = fee * fx_rate
                        revenue_base = (qty * price) * fx_rate
                        
                        if operation == 'buy':
                            # BUY: Add to holdings and cost basis
                            entry['total_qty'] += qty
                            entry['total_cost'] += tx_value_base
                            entry['total_value_buy'] += tx_value_base
                            entry['total_fee'] += fee_base

                            # Wallet holdings: buys land in toWallet
                            from_wallet = _resolve_wallet_ref(tx.get('fromWallet'))
                            to_wallet = _resolve_wallet_ref(tx.get('toWallet'))
                            hold_wallet = to_wallet or from_wallet
                            if from_wallet:
                                wallet_ids_seen.add(from_wallet)
                            if to_wallet:
                                wallet_ids_seen.add(to_wallet)
                            # If user didn't provide toWallet, fall back to fromWallet so wallet contents still updates.
                            if hold_wallet:
                                wallet_crypto_qty[hold_wallet][ckey] += qty
                            
                        elif operation == 'sell':
                            # SELL: Use weighted average to calculate cost of sold portion
                            if entry['total_qty'] > 0:
                                # Calculate current weighted average cost per unit
                                avg_cost_per_unit = entry['total_cost'] / entry['total_qty']
                                
                                # Determine how much we can actually sell from current holdings
                                qty_to_sell = min(qty, entry['total_qty'])
                                
                                # Cost basis of the sold quantity
                                sold_cost = qty_to_sell * avg_cost_per_unit
                                
                                # Update holdings
                                entry['total_qty'] -= qty_to_sell
                                entry['total_cost'] -= sold_cost
                                entry['total_value_sell'] += revenue_base  # Revenue from sale (excluding fee)
                                entry['total_fee'] += fee_base
                                
                                # If selling more than we have, handle the excess as short position
                                excess_qty = qty - qty_to_sell
                                if excess_qty > 0:
                                    # Track the excess as negative position
                                    entry['total_qty'] -= excess_qty
                                    # No additional cost basis change for short position
                                
                            else:
                                # Selling without holdings (short sell) - track as negative
                                entry['total_qty'] -= qty
                                entry['total_value_sell'] += revenue_base
                                entry['total_fee'] += fee_base

                            # Wallet holdings: sells leave fromWallet
                            from_wallet = _resolve_wallet_ref(tx.get('fromWallet'))
                            to_wallet = _resolve_wallet_ref(tx.get('toWallet'))
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
                entry['total_value'] = entry['total_cost']
                totals_map[name] = entry
                
        except Exception as e:
            print(f"Error computing crypto totals: {e}")

        # --- Compute live prices from CoinGecko coins list and attach live totals ---
        try:
            # helper: find coin from coins list by matching symbol/name/id (case-insensitive)
            def find_coin_for_name(name_to_find):
                """Try multiple heuristics to match stored cryptoName to a CoinGecko coin entry.
                Handles values like 'BTC', 'Bitcoin', 'BTC - Bitcoin', and case/whitespace differences.
                """
                if not name_to_find:
                    return None
                s = (name_to_find or '').strip()
                # common pattern: 'SYMBOL - Name'
                parts = [s]
                if ' - ' in s:
                    left, right = s.split(' - ', 1)
                    parts.insert(0, left.strip())
                    parts.append(right.strip())
                # also try removing spaces and parentheses
                parts.append(s.replace(' ', '').strip())
                parts = [p.lower() for p in parts if p]

                for coin in coins:
                    if not coin:
                        continue
                    c_sym = (coin.get('symbol') or '').lower()
                    c_name = (coin.get('name') or '').lower()
                    c_id = (coin.get('id') or '').lower()

                    for cand in parts:
                        if not cand:
                            continue
                        # exact matches
                        if cand == c_sym or cand == c_name or cand == c_id:
                            return coin
                        # substring matches (handle 'bitcoin' vs 'bitcoin cash' etc.)
                        if cand in c_sym or cand in c_name or cand in c_id:
                            return coin
                        if c_sym in cand or c_name in cand or c_id in cand:
                            return coin

                # no match found
                print(f"[DEBUG] No CoinGecko match for crypto name: '{name_to_find}'")
                return None

            for v in totals_map.values():
                    # try to find latest price for this crypto name
                    found = find_coin_for_name(v['cryptoName'])
                    if found and found.get('current_price') is not None:
                        try:
                            v['latest_price'] = Decimal(str(found.get('current_price', 0)))
                        except Exception:
                            v['latest_price'] = Decimal(0)
                    else:
                        # fallback: use 0 for latest price if not found
                        v['latest_price'] = Decimal(0)

                    # compute live total value based on latest price
                    v['total_value_live'] = v['total_qty'] * (v['latest_price'] or Decimal(0))

                    # compute weighted average buy price from current cost basis
                    try:
                        if v['total_qty'] and v['total_qty'] > 0:
                            # Use current cost basis divided by current quantity
                            v['avg_buy_price'] = (v['total_cost'] / v['total_qty'])
                        else:
                            v['avg_buy_price'] = Decimal(0)
                    except Exception:
                        v['avg_buy_price'] = Decimal(0)

                    # We no longer compute a combined average; downstream template will show:
                    # - 'latest_price' as the unit price now
                    # - 'total_value_live' as the total value now (qty * latest_price)
                    # Compute gain/loss: (Current Value + Revenue from Sales) - Total Investment
                    # For accurate P&L calculation across all scenarios (holding, sold, oversold)
                    try:
                        total_revenue = v.get('total_value_sell', Decimal(0))
                        total_investment = v.get('total_value_buy', Decimal(0))
                        current_market_value = v['total_value_live']
                        
                        # Total current worth = market value of holdings + cash received from sales
                        total_current_worth = current_market_value + total_revenue
                        
                        # Gain/Loss = What we have now - What we invested
                        v['value_change_amount'] = total_current_worth - total_investment
                    except Exception:
                        v['value_change_amount'] = Decimal(0)
        except Exception as e:
            print(f"Error computing live prices: {e}")

        # --- Compute holdings by wallet (qty + live value) ---
        wallet_holdings = []
        try:
            # Map SYMBOL -> latest unit price in base currency (CoinGecko vs_currency)
            latest_price_by_symbol = {}
            for coin in (coins or []):
                try:
                    sym = (coin.get('symbol') or '').strip().upper()
                    cp = coin.get('current_price', None)
                    if sym and cp is not None:
                        latest_price_by_symbol[sym] = Decimal(str(cp))
                except Exception:
                    continue

            # Wallet id -> wallet name
            wallet_name_by_id = {}
            for w in (wallets or []):
                wid = (w.get('walletId') or '').strip()
                if wid:
                    wallet_name_by_id[wid] = (w.get('walletName') or wid)

            # Build an ordered list of wallets so UI is stable
            ordered_wallet_ids = [w.get('walletId') for w in (wallets or []) if w.get('walletId')]
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
                        # cname is a normalized symbol key
                        p = latest_price_by_symbol.get(str(cname).upper(), Decimal(0)) or Decimal(0)
                        live_val = q * p
                        holdings_rows.append({
                            'cryptoName': cname,
                            'qty': float(q),
                            'qty_display': _format_number_trim(q, 8),
                            'value_live': float(live_val),
                            'value_live_display': _format_number_trim(live_val, 2),
                        })
                    except Exception:
                        continue

                # Sort holdings by live value desc (fallback: qty)
                holdings_rows.sort(key=lambda r: (r.get('value_live', 0.0), r.get('qty', 0.0)), reverse=True)

                wallet_total_live = Decimal(0)
                for r in holdings_rows:
                    try:
                        wallet_total_live += Decimal(str(r.get('value_live', 0.0) or 0.0))
                    except Exception:
                        pass
                wallet_holdings.append({
                    'walletId': wid,
                    'walletName': wallet_name_by_id.get(wid, wid),
                    'total_value_live': float(wallet_total_live),
                    'total_value_live_display': _format_number_trim(wallet_total_live, 2),
                    'holdings': holdings_rows,
                })
        except Exception as e:
            print(f"Error computing wallet holdings: {e}")
            wallet_holdings = []

        # Convert Decimal values to floats for template rendering
        totals = []
        for v in totals_map.values():
            try:
                tq = float(v['total_qty'])
            except Exception:
                tq = 0.0
            try:
                tv = float(v['total_value'])
            except Exception:
                tv = 0.0
            # Correctly retrieve total amount paid on buy transactions (includes buy fees)
            try:
                tv_buy = float(v.get('total_value_buy', 0) or 0)
            except Exception:
                tv_buy = 0.0
            try:
                lp = float(v.get('latest_price', 0) or 0)
            except Exception:
                lp = 0.0
            try:
                tv_live = float(v.get('total_value_live', 0) or 0)
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
                    price_pct_display = 'N/A'
                else:
                    # for extremely large or tiny values, use scientific notation
                    if abs(price_pct) > 10000:
                        price_pct_display = f"{price_pct:.2e}%"
                    else:
                        price_pct_display = f"{price_pct:.2f}%"

                # value percent display
                if value_pct is None:
                    value_pct_display = 'N/A'
                else:
                    # cap large values for readability
                    if abs(value_pct) > 10000:
                        value_pct_display = f"{value_pct:.2e}%"
                    else:
                        value_pct_display = f"{value_pct:.2f}%"
            except Exception:
                price_pct_display = 'N/A'
                value_pct_display = 'N/A'

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
                avg_buy = float(v.get('avg_buy_price', 0) or 0)
            except Exception:
                avg_buy = 0.0
            try:
                fee_total = float(v.get('total_fee', 0) or 0)
            except Exception:
                fee_total = 0.0
            try:
                change_amt = float(v.get('value_change_amount', 0) or 0)
            except Exception:
                change_amt = 0.0

            totals.append({
                'cryptoName': v['cryptoName'],
                'total_qty': tq,
                'total_qty_display': _format_number_trim(v.get('total_qty', 0), 8),
                # total_value: kept for backwards-compatibility (raw stored aggregate)
                'total_value': tv,
                # total_value_buy: the total amount user spent on buy transactions (includes buy fees)
                'total_value_buy': tv_buy,
                'total_value_buy_display': _format_number_trim(v.get('total_value_buy', 0), 2),
                'latest_price': lp,
                'latest_price_display': _format_number_trim(v.get('latest_price', 0), 6),
                'total_value_live': tv_live,
                'total_value_live_display': _format_number_trim(v.get('total_value_live', 0), 2),
                'currency': v.get('currency',''),
                'price_pct': price_pct if price_pct is not None else 0.0,
                'value_pct': value_pct if value_pct is not None else 0.0,
                'price_multiplier': price_multiplier if price_multiplier is not None else 0.0,
                'price_pct_display': price_pct_display,
                'value_pct_display': value_pct_display,
                'pct_fill': pct_fill,
                'pct_fill_str': f"{pct_fill:.2f}",
                'avg_buy_price': avg_buy,
                'avg_buy_price_display': _format_number_trim(v.get('avg_buy_price', 0), 6),
                'total_fee': fee_total,
                'total_fee_display': _format_number_trim(v.get('total_fee', 0), 2),
                'value_change_amount': change_amt
                ,
                'value_change_amount_abs_display': _format_number_trim(abs(v.get('value_change_amount', Decimal(0))), 2)
            })

        # Send both to template (include coin list and totals)
        return render_template(
            "crypto.html",
            cryptos=cryptos,
            wallets=wallets,
            coins=coins,
            totals=totals,
            walletHoldings=wallet_holdings,
            baseCurrency=base_currency,
            userId=userId,
        )
    else:
        return render_template("home.html")

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

@crypto_bp.route('/crypto', methods=['POST'])
def create_crypto():
    crypto_id = str(uuid.uuid4())
    user = session.get('user')
    user_id = user.get('username')
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
        "note": request.form["note"]
    }

    print(data)
    try:
        response = requests.post(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@crypto_bp.route('/updateCrypto', methods=['POST'])
def update_crypto():
    data = {
        "cryptoId": request.form["cryptoId"],
        "userId": request.form["userId"],
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
        "note": request.form["note"]
    }
    print(f"🔄 [DEBUG] Updating crypto: {data}")
    
    try:
        response = requests.patch(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@crypto_bp.route('/deleteCrypto/<crypto_id>/<user_id>', methods=['POST'])
def delete_crypto(crypto_id, user_id):
    """Delete a crypto."""
    data = {
        "cryptoId": crypto_id,
        "userId": user_id
    }
    print(f"🗑️ [DEBUG] Deleting crypto: {data}")

    try:
        response = requests.delete(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
    
