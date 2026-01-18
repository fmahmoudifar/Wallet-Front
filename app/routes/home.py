from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
from collections import defaultdict
from config import API_URL, aws_auth
from decimal import Decimal
from datetime import datetime
import time

# Reuse the same currency/FX/CoinGecko helpers used by the Crypto page
from .crypto import (
    _get_user_base_currency,
    _get_fx_rate,
    _normalize_currency,
    _format_number_trim,
    _COINGECKO_MARKETS_CACHE,
    _COINGECKO_MARKETS_TTL_SECONDS,
)

# Reuse stock quote + scaling helpers for the Home stock pie
from .stock import (
    _provider_mode,
    _av_get,
    _av_metadata_for_symbol,
    _yh_quote,
    _scale_minor_currency,
    _to_decimal as _to_decimal_stock,
)

home_bp = Blueprint("home", __name__, url_prefix="/")

@home_bp.route("/", methods=['GET'])
def users():
    user = session.get('user')
    print(user)
    if user:
        userId = user.get('username')

        base_currency = _get_user_base_currency(userId)
        cg_vs_currency = (base_currency or 'EUR').lower()
        if cg_vs_currency not in ('eur', 'usd'):
            cg_vs_currency = 'eur'
        try:
            response = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
            cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching cryptos: {e}")
            cryptos = []

        # Real-time wallet balance calculation (legacy cash-flow style kept for reference)
        wallet_balances = defaultdict(lambda: Decimal('0'))
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
                    s = str(val).strip()
                    if s == '':
                        return Decimal(0)
                    return Decimal(s)
                except Exception:
                    return Decimal(0)

            # Group transactions by crypto and sort by date to process chronologically
            crypto_transactions = {}
            for c in cryptos:
                name = c.get('cryptoName') or 'Unknown'
                if name not in crypto_transactions:
                    crypto_transactions[name] = []
                crypto_transactions[name].append(c)
            
            # Sort each crypto's transactions by date
            for name in crypto_transactions:
                crypto_transactions[name].sort(key=lambda x: x.get('tdate', ''))
            
            # Process each crypto's transactions chronologically (exact same logic as crypto.py)
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

                        to_wallet = tx.get("toWallet")
                        from_wallet = tx.get("fromWallet")

                        # TRANSFER: fee is crypto quantity.
                        # Net received quantity = max(0, qty - fee).
                        # - From wallet loses qty (gross)
                        # - To wallet receives (qty - fee) (net)
                        # Portfolio delta = (+net if toWallet else 0) - (qty if fromWallet else 0)
                        if operation == 'transfer':
                            fee_qty = fee
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
                                entry['total_qty'] += delta_qty
                            elif delta_qty < 0:
                                qty_to_remove = -delta_qty
                                if entry['total_qty'] and entry['total_qty'] > 0:
                                    avg_cost_per_unit = entry['total_cost'] / entry['total_qty']
                                    can_remove = min(qty_to_remove, entry['total_qty'])
                                    entry['total_qty'] -= can_remove
                                    entry['total_cost'] -= (can_remove * avg_cost_per_unit)
                                    remaining = qty_to_remove - can_remove
                                    if remaining > 0:
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
                            
                            # Wallet balance: money goes out of from_wallet, crypto goes into to_wallet
                            if from_wallet:
                                wallet_balances[from_wallet] -= tx_value_base  # Cash out (base currency)
                            if to_wallet:
                                wallet_balances[to_wallet] += tx_value_base   # Crypto asset in (base currency)
                                # Track live crypto quantity by destination wallet
                                wallet_crypto_qty[to_wallet][name] += qty
                                
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
                        
                            # Wallet balance: crypto goes out of from_wallet, money goes into to_wallet
                            if from_wallet:
                                wallet_balances[from_wallet] -= tx_value_base  # Crypto asset out (base currency)
                            if to_wallet:
                                wallet_balances[to_wallet] += revenue_base   # Cash in (base currency)
                            # Track live crypto quantity leaving the source wallet
                            if from_wallet:
                                wallet_crypto_qty[from_wallet][name] -= qty
                            
                    except Exception as e:
                        print(f"Error processing transaction for {name}: {e} | Raw tx: {tx}")
                        continue
                
                # Set total_value as current cost basis for compatibility (same as crypto.py)
                entry['total_value'] = entry['total_cost']
                crypto_totals_map[name] = entry
                
        except Exception as e:
            print(f"Error computing crypto totals: {e}")

        # Fetch and process regular transactions
        try:
            response = requests.get(f"{API_URL}/transactions", params={"userId": userId}, auth=aws_auth)
            transactions = response.json().get("transactions", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            transactions = []

        # Fiat transactions affect wallet cash balances (in base currency)
        wallet_fiat_balances = defaultdict(lambda: Decimal('0'))
            
        # Process regular transactions
        for transaction in transactions:
            try:
                def to_decimal_fiat(val):
                    try:
                        if val is None:
                            return Decimal(0)
                        s = str(val).strip()
                        if s == '':
                            return Decimal(0)
                        return Decimal(s)
                    except Exception:
                        return Decimal(0)

                # Fiat transactions: amount is the only value field; price is deprecated.
                amt = to_decimal_fiat(transaction.get('amount', 0))
                fee = to_decimal_fiat(transaction.get('fee', 0))

                tx_currency = _normalize_currency(transaction.get('currency'), base_currency)
                fx_rate = _get_fx_rate(tx_currency, base_currency)
                amt_base = amt * fx_rate
                fee_base = fee * fx_rate

                to_wallet = transaction.get('toWallet')
                from_wallet = transaction.get('fromWallet')
                ttype = str(transaction.get('transType') or '').strip().lower()

                # Fiat wallet logic (as requested):
                # - Income: add amount to toWallet (ignore fee)
                # - Expense: deduct amount from fromWallet (ignore fee)
                # - Transfer: deduct (amount + fee) from fromWallet and add amount to toWallet
                if ttype == 'income':
                    if to_wallet:
                        wallet_fiat_balances[to_wallet] += amt_base
                elif ttype == 'expense':
                    if from_wallet:
                        wallet_fiat_balances[from_wallet] -= amt_base
                elif ttype == 'transfer':
                    total_move = amt_base + fee_base
                    if from_wallet:
                        wallet_fiat_balances[from_wallet] -= total_move
                    if to_wallet:
                        wallet_fiat_balances[to_wallet] += amt_base
                else:
                    # Backward-compatible fallback based on which wallets are provided
                    if from_wallet and to_wallet:
                        total_move = amt_base + fee_base
                        wallet_fiat_balances[from_wallet] -= total_move
                        wallet_fiat_balances[to_wallet] += amt_base
                    elif to_wallet:
                        wallet_fiat_balances[to_wallet] += amt_base
                    elif from_wallet:
                        wallet_fiat_balances[from_wallet] -= amt_base
                    
            except Exception as e:
                print(f"Error processing transaction {transaction}: {e}")
                continue

        # Fetch stock transactions
        try:
            response = requests.get(f"{API_URL}/stocks", params={"userId": userId}, auth=aws_auth)
            stocks = response.json().get("stocks", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching stocks: {e}")
            stocks = []

        # Track per-wallet stock quantities (for live valuation in Wallet Balances)
        wallet_stock_qty = defaultdict(lambda: defaultdict(lambda: Decimal(0)))

        # Stock portfolio totals (cost basis + realized revenue) for Home overview
        stock_totals_map = {}
        try:
            stock_transactions = {}
            for s in stocks:
                sym = (s.get('stockName') or '').strip().upper() or 'UNKNOWN'
                stock_transactions.setdefault(sym, []).append(s)

            for sym in stock_transactions:
                stock_transactions[sym].sort(key=lambda x: x.get('tdate', ''))

            for sym, transactions in stock_transactions.items():
                entry = {
                    'stockName': sym,
                    'total_qty': Decimal(0),
                    'total_cost': Decimal(0),
                    'total_fee': Decimal(0),
                    'total_value_buy': Decimal(0),
                    'total_value_sell': Decimal(0),
                    'currency': base_currency,
                }

                for tx in transactions:
                    try:
                        qty = _to_decimal_stock(tx.get('quantity', 0))
                        price_raw = _to_decimal_stock(tx.get('price', 0))
                        fee_raw = _to_decimal_stock(tx.get('fee', 0))
                        operation = str(tx.get('operation') or tx.get('side') or 'buy').lower()

                        from_wallet = tx.get('fromWallet')
                        to_wallet = tx.get('toWallet')

                        tx_currency_raw = _normalize_currency(tx.get('currency'), base_currency)
                        price_major, tx_ccy = _scale_minor_currency(price_raw, tx_currency_raw)
                        fee_major, _ = _scale_minor_currency(fee_raw, tx_currency_raw)

                        fx_rate = _get_fx_rate(tx_ccy, base_currency)
                        tx_value_base = ((qty * price_major) + fee_major) * fx_rate
                        fee_base = fee_major * fx_rate
                        revenue_base = (qty * price_major) * fx_rate

                        # Wallet holdings / cash-flow effects:
                        # - buy: cash decreases in fromWallet, stock qty increases in toWallet
                        # - sell: stock qty decreases in fromWallet, cash increases in toWallet
                        # - transfer: move stock qty from fromWallet to toWallet
                        if operation == 'buy':
                            if to_wallet:
                                wallet_stock_qty[to_wallet][sym] += qty
                            if from_wallet:
                                wallet_fiat_balances[from_wallet] -= tx_value_base
                        elif operation == 'sell':
                            if from_wallet:
                                wallet_stock_qty[from_wallet][sym] -= qty
                            if to_wallet:
                                wallet_fiat_balances[to_wallet] += (revenue_base - fee_base)
                        elif operation == 'transfer':
                            if from_wallet:
                                wallet_stock_qty[from_wallet][sym] -= qty
                            if to_wallet:
                                wallet_stock_qty[to_wallet][sym] += qty

                        if operation == 'buy':
                            entry['total_qty'] += qty
                            entry['total_cost'] += tx_value_base
                            entry['total_value_buy'] += tx_value_base
                            entry['total_fee'] += fee_base
                        elif operation == 'sell':
                            if entry['total_qty'] > 0:
                                avg_cost_per_unit = entry['total_cost'] / entry['total_qty']
                                qty_to_sell = min(qty, entry['total_qty'])
                                sold_cost = qty_to_sell * avg_cost_per_unit
                                entry['total_qty'] -= qty_to_sell
                                entry['total_cost'] -= sold_cost
                                entry['total_value_sell'] += revenue_base
                                entry['total_fee'] += fee_base

                                excess_qty = qty - qty_to_sell
                                if excess_qty > 0:
                                    entry['total_qty'] -= excess_qty
                            else:
                                entry['total_qty'] -= qty
                                entry['total_value_sell'] += revenue_base
                                entry['total_fee'] += fee_base
                        else:
                            continue

                    except Exception as e:
                        print(f"Error processing stock tx for {sym}: {e} | Raw tx: {tx}")
                        continue

                entry['total_value'] = entry['total_cost']
                stock_totals_map[sym] = entry

        except Exception as e:
            print(f"Error computing stock totals: {e}")
            stock_totals_map = {}

        # Fetch wallet names
        try:
            response = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
            wallets = response.json().get("wallets", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching wallets: {e}")
            wallets = []

        # Fetch live prices from CoinGecko for chart values and compute per-wallet live values
        crypto_chart_now = {}
        crypto_chart_paid = {}
        wallet_live_values = defaultdict(lambda: Decimal(0))
        try:
            # Get top coins from CoinGecko
            cg_url = "https://api.coingecko.com/api/v3/coins/markets"
            cg_params = {"vs_currency": cg_vs_currency, "order": "market_cap_desc", "per_page": 500, "page": 1}

            now_ts = time.time()
            cache_bucket = _COINGECKO_MARKETS_CACHE.get(cg_vs_currency) or {'ts': 0.0, 'data': []}
            if cache_bucket['data'] and (now_ts - cache_bucket['ts'] < _COINGECKO_MARKETS_TTL_SECONDS):
                coins = cache_bucket['data']
            else:
                cg_headers = {
                    'Accept': 'application/json',
                    'User-Agent': 'Wallet-Front/1.0'
                }
                cg_resp = requests.get(cg_url, params=cg_params, headers=cg_headers, timeout=10)
                if cg_resp.status_code == 200:
                    coins = cg_resp.json()
                    _COINGECKO_MARKETS_CACHE[cg_vs_currency] = {'data': coins, 'ts': now_ts}
                else:
                    if cache_bucket['data']:
                        coins = cache_bucket['data']
                        print(f"CoinGecko returned status {cg_resp.status_code}; using cached markets")
                    else:
                        coins = []
                        print(f"CoinGecko returned status {cg_resp.status_code}")

            if coins:
                
                # Helper function to find coin by name
                def find_coin_for_name(name_to_find):
                    if not name_to_find:
                        return None
                    s = (name_to_find or '').strip()
                    parts = [s]
                    if ' - ' in s:
                        left, right = s.split(' - ', 1)
                        parts.insert(0, left.strip())
                        parts.append(right.strip())
                    parts.append(s.replace(' ', '').strip())
                    parts = [p.lower() for p in parts if p]

                    for coin in coins:
                        coin_id = (coin.get('id') or '').lower()
                        coin_symbol = (coin.get('symbol') or '').lower()
                        coin_name = (coin.get('name') or '').lower()
                        
                        for part in parts:
                            if part in [coin_id, coin_symbol, coin_name]:
                                return coin
                    return None
                
                # Calculate total values with live prices (exact same logic as crypto.py)
                price_by_name = {}
                for name, entry in crypto_totals_map.items():
                    # Show ALL cryptos (including zero/negative holdings) to match crypto page
                    # Try to find latest price for this crypto name (same as crypto.py)
                    coin = find_coin_for_name(entry['cryptoName'])
                    if coin and coin.get('current_price') is not None:
                        try:
                            latest_price = Decimal(str(coin.get('current_price', 0)))
                        except Exception:
                            latest_price = Decimal(0)
                    else:
                        # fallback: use 0 for latest price if not found (same as crypto.py)
                        latest_price = Decimal(0)
                    price_by_name[name] = latest_price
                    
                    # Compute live total value based on latest price (exact same formula as crypto.py)
                    total_value_live = entry['total_qty'] * (latest_price or Decimal(0))
                    crypto_chart_now[name] = float(total_value_live) if total_value_live > 0 else 0.0
                    crypto_chart_paid[name] = float(entry.get('total_value_buy', Decimal(0)) or Decimal(0))

                # Compute live values per wallet: sum(qty_by_wallet_crypto * latest_price)
                for w_id, per_crypto in wallet_crypto_qty.items():
                    total_live = Decimal(0)
                    for cname, q in per_crypto.items():
                        p = price_by_name.get(cname, Decimal(0)) or Decimal(0)
                        total_live += (q * p)
                    wallet_live_values[w_id] = total_live

                # DEBUG: Print wallet live valuation details
                print("=== WALLET LIVE VALUES DEBUG ===")
                for w_id, per_crypto in wallet_crypto_qty.items():
                    print(f"Wallet {w_id}:")
                    for cname, q in per_crypto.items():
                        p = price_by_name.get(cname, Decimal(0)) or Decimal(0)
                        print(f"  {cname}: qty={q} latest_price={p} live_value={(q*p):.4f}")
                    print(f"  Total Live Value: {wallet_live_values.get(w_id, Decimal(0)):.4f}")
                print("===============================")
                            
            else:
                print(f"CoinGecko returned status {cg_resp.status_code}")
                # Fallback: use 0 for live values if no price available (same as crypto.py)
                for name, entry in crypto_totals_map.items():
                    # Show ALL cryptos (including zero/negative holdings) to match crypto page
                    total_value_live = entry['total_qty'] * Decimal(0)  # No price = 0 value
                    crypto_chart_now[name] = 0.0
                    crypto_chart_paid[name] = float(entry.get('total_value_buy', Decimal(0)) or Decimal(0))
                # Without prices, wallet live values are 0
                wallet_live_values = defaultdict(lambda: Decimal(0))
                        
        except Exception as e:
            print(f"Error fetching CoinGecko prices: {e}")
            # Fallback: use 0 for live values if no price available (same as crypto.py)
            for name, entry in crypto_totals_map.items():
                # Show ALL cryptos (including zero/negative holdings) to match crypto page
                total_value_live = entry['total_qty'] * Decimal(0)  # No price = 0 value
                crypto_chart_now[name] = 0.0
                crypto_chart_paid[name] = float(entry.get('total_value_buy', Decimal(0)) or Decimal(0))
            wallet_live_values = defaultdict(lambda: Decimal(0))

        cryptoLabels = list(crypto_chart_now.keys())
        cryptoNowValues = list(crypto_chart_now.values())
        cryptoPaidValues = list(crypto_chart_paid.values())

        # Overall portfolio summary (same P&L logic as crypto.py)
        total_paid = Decimal(0)
        total_now = Decimal(0)
        total_revenue = Decimal(0)
        for entry in crypto_totals_map.values():
            total_paid += (entry.get('total_value_buy') or Decimal(0))
            total_revenue += (entry.get('total_value_sell') or Decimal(0))

        # Sum of current holdings market value across all cryptos
        for v in crypto_chart_now.values():
            try:
                total_now += Decimal(str(v))
            except Exception:
                pass

        total_current_worth = total_now + total_revenue
        gain_amount = total_current_worth - total_paid
        if total_paid and total_paid != 0:
            gain_pct = (gain_amount / total_paid) * Decimal(100)
            gain_pct_display = f"{float(gain_pct):.2f}%"
        else:
            gain_pct_display = 'N/A'

        gain_amount_abs_display = _format_number_trim(abs(gain_amount), 2)
        total_paid_display = _format_number_trim(total_paid, 2)
        total_now_display = _format_number_trim(total_now, 2)

        # Stock chart values for Home (per-symbol now/paid, summed client-side like crypto)
        stock_chart_now = {}
        stock_chart_paid = {}
        price_base_by_symbol = {}
        try:
            mode = _provider_mode()

            def _fetch_stock_quote(sym: str):
                s = (sym or '').strip().upper()
                if not s:
                    return {"symbol": "", "name": "", "currency": base_currency, "price": None, "asof": ""}

                if mode in ('auto', 'alphavantage'):
                    try:
                        data = _av_get({"function": "GLOBAL_QUOTE", "symbol": s})
                        gq = data.get('Global Quote') or {}
                        price_raw = (gq.get('05. price') or '').strip()
                        try:
                            price = float(price_raw)
                        except Exception:
                            price = None
                        meta = _av_metadata_for_symbol(s)
                        return {
                            "symbol": s,
                            "name": meta.get('name') or '',
                            "currency": meta.get('currency') or base_currency,
                            "price": price,
                            "asof": (gq.get('07. latest trading day') or ''),
                        }
                    except Exception:
                        if mode == 'alphavantage':
                            raise
                        return _yh_quote(s)

                return _yh_quote(s)

            for sym, entry in stock_totals_map.items():
                quote = _fetch_stock_quote(sym)
                q_price = quote.get('price')
                q_currency_raw = quote.get('currency') or base_currency

                # Convert quote price into base currency (and scale minor units like GBX)
                price_major, q_ccy = _scale_minor_currency(_to_decimal_stock(q_price), q_currency_raw)
                fx = _get_fx_rate(q_ccy, base_currency)
                price_base = price_major * fx
                price_base_by_symbol[sym] = price_base

                total_value_live = (entry.get('total_qty') or Decimal(0)) * (price_base or Decimal(0))
                stock_chart_now[sym] = float(total_value_live) if total_value_live > 0 else 0.0
                stock_chart_paid[sym] = float(entry.get('total_value_buy') or Decimal(0))

        except Exception as e:
            print(f"Error fetching stock prices for Home: {e}")
            for sym, entry in stock_totals_map.items():
                stock_chart_now[sym] = 0.0
                stock_chart_paid[sym] = float(entry.get('total_value_buy') or Decimal(0))

        stockNowValues = list(stock_chart_now.values())
        stockPaidValues = list(stock_chart_paid.values())

        # Overall stock portfolio P&L (same logic as crypto)
        stock_total_paid = Decimal(0)
        stock_total_now = Decimal(0)
        stock_total_revenue = Decimal(0)
        for entry in stock_totals_map.values():
            stock_total_paid += (entry.get('total_value_buy') or Decimal(0))
            stock_total_revenue += (entry.get('total_value_sell') or Decimal(0))
        for v in stock_chart_now.values():
            try:
                stock_total_now += Decimal(str(v))
            except Exception:
                pass

        stock_total_current_worth = stock_total_now + stock_total_revenue
        stock_gain_amount = stock_total_current_worth - stock_total_paid
        if stock_total_paid and stock_total_paid != 0:
            stock_gain_pct = (stock_gain_amount / stock_total_paid) * Decimal(100)
            stock_gain_pct_display = f"{float(stock_gain_pct):.2f}%"
        else:
            stock_gain_pct_display = 'N/A'

        stock_gain_amount_abs_display = _format_number_trim(abs(stock_gain_amount), 2)
        stock_total_paid_display = _format_number_trim(stock_total_paid, 2)
        stock_total_now_display = _format_number_trim(stock_total_now, 2)

        # Compute per-wallet live stock values and build wallet list (fiat + crypto + stock)
        wallet_stock_live_values = defaultdict(lambda: Decimal(0))
        try:
            for w_id, per_sym in wallet_stock_qty.items():
                total_live = Decimal(0)
                for sym, q in per_sym.items():
                    p = price_base_by_symbol.get(sym, Decimal(0)) or Decimal(0)
                    total_live += (q * p)
                wallet_stock_live_values[w_id] = total_live
        except Exception as e:
            print(f"Error computing wallet stock live values: {e}")

        wallet_list = []
        for wallet in wallets:
            wallet_id = wallet.get('walletId')
            wallet_name = wallet.get('walletName')
            balance = (
                (wallet_fiat_balances.get(wallet_id, Decimal(0)) or Decimal(0))
                + (wallet_live_values.get(wallet_id, Decimal(0)) or Decimal(0))
                + (wallet_stock_live_values.get(wallet_id, Decimal(0)) or Decimal(0))
            )
            wallet_list.append({
                'walletId': wallet_id,
                'walletName': wallet_name,
                'balance': float(round(balance, 2))
            })

        return render_template(
            "home.html",
            cryptoLabels=cryptoLabels,
            cryptoNowValues=cryptoNowValues,
            cryptoPaidValues=cryptoPaidValues,
            stockNowValues=stockNowValues,
            stockPaidValues=stockPaidValues,
            baseCurrency=base_currency,
            cryptoPortfolioPaidDisplay=total_paid_display,
            cryptoPortfolioNowDisplay=total_now_display,
            cryptoPortfolioGainAbsDisplay=gain_amount_abs_display,
            cryptoPortfolioGainIsPositive=(gain_amount >= 0),
            cryptoPortfolioGainPctDisplay=gain_pct_display,
            stockPortfolioPaidDisplay=stock_total_paid_display,
            stockPortfolioNowDisplay=stock_total_now_display,
            stockPortfolioGainAbsDisplay=stock_gain_amount_abs_display,
            stockPortfolioGainIsPositive=(stock_gain_amount >= 0),
            stockPortfolioGainPctDisplay=stock_gain_pct_display,
            wallets=wallet_list,
            userId=userId
        )
 
    else:
        return render_template("home.html")
