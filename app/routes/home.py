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
from app.services.user_scope import filter_records_by_user

home_bp = Blueprint("home", __name__, url_prefix="/")


def _ensure_user_settings_row(user_id: str) -> None:
    """Ensure a user has a settings row; if missing, create defaults.

    Defaults match the Settings page: currency=EUR, theme=Light.
    """
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
            if first.get('theme'):
                session['theme'] = first.get('theme')
            if first.get('currency'):
                session['currency'] = first.get('currency')
        except Exception:
            pass
        return

    default_data = {
        "userId": user_id,
        "currency": "EUR",
        "theme": "Light",
    }

    try:
        upsert = requests.patch(f"{API_URL}/settings", json=default_data, auth=aws_auth, timeout=10)
        if upsert.status_code in (200, 201):
            session['currency'] = default_data['currency']
            session['theme'] = default_data['theme']
        else:
            try:
                print(f"Settings default upsert failed: {upsert.status_code} {upsert.text}")
            except Exception:
                print(f"Settings default upsert failed: {upsert.status_code}")
    except Exception as e:
        print(f"Error creating default settings (home init): {e}")

@home_bp.route("/", methods=['GET'])
def users():
    user = session.get('user')
    print(user)
    if user:
        userId = user.get('username')

        # Ensure settings row exists for this user (EUR + Light defaults)
        _ensure_user_settings_row(userId)

        base_currency = _get_user_base_currency(userId)
        cg_vs_currency = (base_currency or 'EUR').lower()
        if cg_vs_currency not in ('eur', 'usd'):
            cg_vs_currency = 'eur'
        try:
            response = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
            cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
            cryptos = filter_records_by_user(cryptos, userId)
        except Exception as e:
            print(f"Error fetching cryptos: {e}")
            cryptos = []

        # Fetch wallets early (needed to compute wallet chart values in wallet currency)
        try:
            w_resp = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth, timeout=12)
            wallets = w_resp.json().get("wallets", []) if w_resp.status_code == 200 else []
            wallets = filter_records_by_user(wallets, userId)
        except Exception as e:
            print(f"Error fetching wallets: {e}")
            wallets = []

        wallet_currency_by_id = {}
        for w in (wallets or []):
            try:
                wid = (w or {}).get('walletId')
                if not wid:
                    continue
                raw_ccy = (
                    (w or {}).get('currency')
                    or (w or {}).get('Currency')
                    or (w or {}).get('walletCurrency')
                    or (w or {}).get('wallet_currency')
                )
                wallet_currency_by_id[str(wid)] = _normalize_currency(raw_ccy, base_currency)
            except Exception:
                continue

        def _wallet_ccy(wallet_id: str) -> str:
            try:
                return wallet_currency_by_id.get(str(wallet_id), _normalize_currency(base_currency, 'EUR'))
            except Exception:
                return _normalize_currency(base_currency, 'EUR')

        def _fx_amount_to_wallet(amount: Decimal, from_currency: str, wallet_id: str) -> Decimal:
            try:
                from_ccy = _normalize_currency(from_currency, _normalize_currency(base_currency, 'EUR'))
                to_ccy = _wallet_ccy(wallet_id)
                if from_ccy == to_ccy:
                    return amount
                return amount * _get_fx_rate(from_ccy, to_ccy)
            except Exception:
                # On FX failure, fall back to no conversion.
                return amount

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
            transactions = filter_records_by_user(transactions, userId)
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            transactions = []

        # Fiat transactions affect wallet cash balances (in each wallet's currency)
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

                to_wallet = transaction.get('toWallet')
                from_wallet = transaction.get('fromWallet')
                ttype = str(transaction.get('transType') or '').strip().lower()

                # Fiat wallet logic (as requested):
                # - Income: add amount to toWallet (ignore fee)
                # - Expense: deduct amount from fromWallet (ignore fee)
                # - Transfer: deduct (amount + fee) from fromWallet and add amount to toWallet
                if ttype == 'income':
                    if to_wallet:
                        wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(amt, tx_currency, to_wallet)
                elif ttype == 'expense':
                    if from_wallet:
                        wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(amt, tx_currency, from_wallet)
                elif ttype == 'transfer':
                    total_move = amt + fee
                    if from_wallet:
                        wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(total_move, tx_currency, from_wallet)
                    if to_wallet:
                        wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(amt, tx_currency, to_wallet)
                else:
                    # Backward-compatible fallback based on which wallets are provided
                    if from_wallet and to_wallet:
                        total_move = amt + fee
                        wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(total_move, tx_currency, from_wallet)
                        wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(amt, tx_currency, to_wallet)
                    elif to_wallet:
                        wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet(amt, tx_currency, to_wallet)
                    elif from_wallet:
                        wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(amt, tx_currency, from_wallet)
                    
            except Exception as e:
                print(f"Error processing transaction {transaction}: {e}")
                continue

        # Loans also affect wallet cash balances (in each wallet's currency)
        try:
            resp = requests.get(f"{API_URL}/loans", params={"userId": userId}, auth=aws_auth, timeout=12)
            loans = resp.json().get("loans", []) if resp.status_code == 200 else []
            loans = filter_records_by_user(loans, userId)
        except Exception as e:
            print(f"Error fetching loans (home wallet balances): {e}")
            loans = []

        def _wallet_id(val):
            try:
                s = (str(val) if val is not None else '').strip()
                if not s or s.lower() == 'none':
                    return None
                return s
            except Exception:
                return None

        for loan in (loans or []):
            try:
                def to_decimal_loan(val):
                    try:
                        if val is None:
                            return Decimal(0)
                        s = str(val).strip()
                        if s == '':
                            return Decimal(0)
                        return Decimal(s)
                    except Exception:
                        return Decimal(0)

                loan_type = str(loan.get('type') or '').strip().lower()
                if loan_type not in ('borrow', 'lend', 'loan'):
                    loan_type = 'borrow'

                action = str(loan.get('action') or '').strip().lower() or 'new'
                if action not in ('new', 'repay'):
                    action = 'new'

                amt = to_decimal_loan(loan.get('amount'))
                fee = to_decimal_loan(loan.get('fee'))

                tx_currency = _normalize_currency(loan.get('currency'), base_currency)

                from_wallet = _wallet_id(loan.get('fromWallet'))
                to_wallet = _wallet_id(loan.get('toWallet'))

                inflow_wallet = None
                outflow_wallet = None

                if action == 'new':
                    if loan_type in ('borrow', 'loan'):
                        inflow_wallet = to_wallet
                    else:  # lend
                        outflow_wallet = from_wallet
                else:  # repay
                    if loan_type in ('borrow', 'loan'):
                        outflow_wallet = from_wallet
                    else:  # lend
                        inflow_wallet = to_wallet

                if outflow_wallet:
                    wallet_fiat_balances[outflow_wallet] -= _fx_amount_to_wallet(amt, tx_currency, outflow_wallet)
                if inflow_wallet:
                    wallet_fiat_balances[inflow_wallet] += _fx_amount_to_wallet(amt, tx_currency, inflow_wallet)

                # Fee: if we have an outflow wallet, charge it there; otherwise deduct from inflow.
                if fee:
                    if outflow_wallet:
                        wallet_fiat_balances[outflow_wallet] -= _fx_amount_to_wallet(fee, tx_currency, outflow_wallet)
                    elif inflow_wallet:
                        wallet_fiat_balances[inflow_wallet] -= _fx_amount_to_wallet(fee, tx_currency, inflow_wallet)

            except Exception as e:
                print(f"Error processing loan {loan}: {e}")
                continue

        # Fetch stock transactions
        try:
            response = requests.get(f"{API_URL}/stocks", params={"userId": userId}, auth=aws_auth)
            stocks = response.json().get("stocks", []) if response.status_code == 200 else []
            stocks = filter_records_by_user(stocks, userId)
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

                        tx_value_tx = (qty * price_major) + fee_major
                        revenue_tx = (qty * price_major)
                        fee_tx = fee_major

                        # Wallet holdings / cash-flow effects:
                        # - buy: cash decreases in fromWallet, stock qty increases in toWallet
                        # - sell: stock qty decreases in fromWallet, cash increases in toWallet
                        # - transfer: move stock qty from fromWallet to toWallet
                        if operation == 'buy':
                            if to_wallet:
                                wallet_stock_qty[to_wallet][sym] += qty
                            if from_wallet:
                                wallet_fiat_balances[from_wallet] -= _fx_amount_to_wallet(tx_value_tx, tx_ccy, from_wallet)
                        elif operation == 'sell':
                            if from_wallet:
                                wallet_stock_qty[from_wallet][sym] -= qty
                            if to_wallet:
                                wallet_fiat_balances[to_wallet] += _fx_amount_to_wallet((revenue_tx - fee_tx), tx_ccy, to_wallet)
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
            wallet_type = (
                wallet.get('walletType')
                or wallet.get('WalletType')
                or wallet.get('type')
                or wallet.get('wallet_type')
            )
            wallet_currency = (
                wallet.get('currency')
                or wallet.get('Currency')
                or wallet.get('walletCurrency')
                or wallet.get('wallet_currency')
            )
            # wallet_fiat_balances is already in the wallet's own currency.
            cash_in_wallet_ccy = wallet_fiat_balances.get(wallet_id, Decimal(0)) or Decimal(0)

            # Crypto/stock live values are computed in base_currency; convert them into the wallet currency.
            w_ccy = _normalize_currency(wallet_currency, base_currency)
            b_ccy = _normalize_currency(base_currency, 'EUR')
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

            crypto_base = wallet_live_values.get(wallet_id, Decimal(0)) or Decimal(0)
            stock_base = wallet_stock_live_values.get(wallet_id, Decimal(0)) or Decimal(0)
            crypto_in_wallet_ccy = crypto_base * fx_base_to_wallet
            stock_in_wallet_ccy = stock_base * fx_base_to_wallet

            cash_base = cash_in_wallet_ccy * fx_wallet_to_base
            balance_base = cash_base + crypto_base + stock_base
            balance_wallet = cash_in_wallet_ccy + crypto_in_wallet_ccy + stock_in_wallet_ccy

            wallet_list.append({
                'walletId': wallet_id,
                'walletName': wallet_name,
                'walletType': wallet_type,
                'currency': wallet_currency,
                # Chart bars use base/setting currency.
                'balance': float(round(balance_base, 2)),
                'balanceBase': float(round(balance_base, 2)),
                # Tooltip can show wallet-native currency.
                'balanceWallet': float(round(balance_wallet, 2)),
            })

        # --- Loans (Home card): open positions progress (no FX) ---
        # A position is considered open if outstanding > 0.
        loanHomePositions = []

        def _to_decimal_home(val) -> Decimal:
            try:
                if val is None:
                    return Decimal(0)
                s = str(val).strip()
                if s == '':
                    return Decimal(0)
                return Decimal(s)
            except Exception:
                return Decimal(0)

        def _day_from_iso_home(date_str: str) -> str:
            s = (date_str or '').strip()
            return s[:10] if len(s) >= 10 else s

        def _derive_position_home(counterparty: str, currency: str, tdate: str) -> str:
            party = (counterparty or '').strip() or '—'
            ccy = (currency or '').strip().upper() or (base_currency or 'EUR')
            day = _day_from_iso_home(tdate) or 'unknown-date'
            return f"{party} | {ccy} | {day}"

        try:
            l_resp = requests.get(f"{API_URL}/loans", params={"userId": userId}, auth=aws_auth, timeout=12)
            _loans = l_resp.json().get('loans', []) if l_resp.status_code == 200 else []
            _loans = filter_records_by_user(_loans, userId)
        except Exception as e:
            print(f"Error fetching loans (home): {e}")
            _loans = []

        if _loans:
            pos_map = {}
            for row in (_loans or []):
                try:
                    t = str(row.get('type') or '').strip().lower()
                    if t not in ('borrow', 'lend', 'loan'):
                        t = 'borrow'
                    action = str(row.get('action') or '').strip().lower() or 'new'
                    if action not in ('new', 'repay'):
                        action = 'new'

                    party = str(row.get('counterparty') or '').strip() or '—'
                    currency = (str(row.get('currency') or '').strip().upper() or (base_currency or 'EUR'))
                    amt = _to_decimal_home(row.get('amount'))
                    tdate = str(row.get('tdate') or '').strip()
                    position_raw = str(row.get('position') or '').strip()

                    if action == 'new':
                        position = position_raw or _derive_position_home(party, currency, tdate)
                    else:
                        position = position_raw
                        if not position:
                            # Fallback: treat legacy repay as its own bucket.
                            position = f"{party} | {currency} | legacy"

                    key = (t, position)
                    if key not in pos_map:
                        pos_map[key] = {
                            'type': t,
                            'position': position,
                            'counterparty': party,
                            'currency': currency,
                            'principal': Decimal(0),
                            'repaid': Decimal(0),
                        }

                    if action == 'new':
                        pos_map[key]['principal'] += amt
                    else:
                        pos_map[key]['repaid'] += amt
                except Exception:
                    continue

            for entry in pos_map.values():
                principal = entry.get('principal') or Decimal(0)
                repaid = entry.get('repaid') or Decimal(0)
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

                t = entry.get('type')
                if t == 'lend':
                    type_label = 'Lend'
                elif t == 'loan':
                    type_label = 'Loan'
                else:
                    type_label = 'Borrow'

                loanHomePositions.append({
                    'counterparty': entry.get('counterparty') or '—',
                    'currency': entry.get('currency') or (base_currency or 'EUR'),
                    'type': t,
                    'typeLabel': type_label,
                    'principal': float(principal),
                    'repaid': float(repaid_for_pct),
                    'outstanding': float(outstanding),
                    'progressPct': float(pct),
                })

            loanHomePositions.sort(key=lambda x: (
                -abs(Decimal(str(x.get('outstanding') or 0))),
                str(x.get('counterparty') or '').lower(),
                str(x.get('currency') or ''),
            ))

        # --- Loans chart (Home card): outstanding by counterparty (no FX) ---
        # Chart a single currency to avoid mixing units:
        # - Prefer base currency if present
        # - Else choose the currency with the largest total outstanding
        def _to_decimal_home(val) -> Decimal:
            try:
                if val is None:
                    return Decimal(0)
                s = str(val).strip()
                if s == '':
                    return Decimal(0)
                return Decimal(s)
            except Exception:
                return Decimal(0)

        def _day_from_iso_home(date_str: str) -> str:
            s = (date_str or '').strip()
            return s[:10] if len(s) >= 10 else s

        def _derive_position_home(counterparty: str, currency: str, tdate: str) -> str:
            party = (counterparty or '').strip() or '—'
            ccy = (currency or '').strip().upper() or (base_currency or 'EUR')
            day = _day_from_iso_home(tdate) or 'unknown-date'
            return f"{party} | {ccy} | {day}"

        loanHomeChart = {'currency': '', 'labels': [], 'values': []}

        try:
            l_resp = requests.get(f"{API_URL}/loans", params={"userId": userId}, auth=aws_auth, timeout=12)
            _loans = l_resp.json().get('loans', []) if l_resp.status_code == 200 else []
            _loans = filter_records_by_user(_loans, userId)
        except Exception as e:
            print(f"Error fetching loans (home): {e}")
            _loans = []

        if _loans:
            # Per-position ledger so paid-off/overpaid positions don't affect open ones.
            pos_map = {}
            for row in (_loans or []):
                try:
                    t = str(row.get('type') or '').strip().lower()
                    if t not in ('borrow', 'lend', 'loan'):
                        t = 'borrow'
                    action = str(row.get('action') or '').strip().lower() or 'new'
                    if action not in ('new', 'repay'):
                        action = 'new'

                    party = str(row.get('counterparty') or '').strip() or '—'
                    currency = (str(row.get('currency') or '').strip().upper() or (base_currency or 'EUR'))
                    amt = _to_decimal_home(row.get('amount'))
                    tdate = str(row.get('tdate') or '').strip()
                    position_raw = str(row.get('position') or '').strip()

                    if action == 'new':
                        position = position_raw or _derive_position_home(party, currency, tdate)
                    else:
                        # If legacy repay has no position, bucket it under a stable key.
                        position = position_raw or f"{party} | {currency} | legacy"

                    key = (t, party.lower(), currency, position)
                    if key not in pos_map:
                        pos_map[key] = {
                            'type': t,
                            'counterparty': party,
                            'currency': currency,
                            'principal': Decimal(0),
                            'repaid': Decimal(0),
                        }
                    if action == 'new':
                        pos_map[key]['principal'] += amt
                    else:
                        pos_map[key]['repaid'] += amt
                except Exception:
                    continue

            totals_by_ccy = defaultdict(lambda: defaultdict(lambda: Decimal(0)))
            for entry in pos_map.values():
                principal = entry.get('principal') or Decimal(0)
                repaid = entry.get('repaid') or Decimal(0)
                outstanding = principal - repaid
                if outstanding <= 0:
                    continue
                ccy = entry.get('currency') or (base_currency or 'EUR')
                party = entry.get('counterparty') or '—'
                signed = outstanding if entry.get('type') == 'lend' else -outstanding
                totals_by_ccy[ccy][party] += signed

            base_ccy = (base_currency or 'EUR').strip().upper() or 'EUR'
            chosen_ccy = base_ccy if totals_by_ccy.get(base_ccy) else ''
            if not chosen_ccy:
                best_ccy = ''
                best_total = Decimal(0)
                for ccy, party_map in totals_by_ccy.items():
                    total_abs = sum((abs(v) for v in (party_map or {}).values()), Decimal(0))
                    if total_abs > best_total:
                        best_total = total_abs
                        best_ccy = ccy
                chosen_ccy = best_ccy

            if chosen_ccy and totals_by_ccy.get(chosen_ccy):
                party_map = totals_by_ccy[chosen_ccy]
                items = [(k, v) for k, v in party_map.items() if v is not None and v != 0]
                items.sort(key=lambda kv: abs(kv[1]), reverse=True)
                items = items[:8]
                loanHomeChart = {
                    'currency': chosen_ccy,
                    'labels': [k for k, _ in items],
                    'values': [float(v) for _, v in items],
                }

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
            loanHomePositions=loanHomePositions,
            userId=userId
        )
 
    else:
        return render_template("home.html")
