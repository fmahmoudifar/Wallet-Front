from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
from collections import defaultdict
from config import API_URL, aws_auth
from decimal import Decimal
from datetime import datetime

home_bp = Blueprint("home", __name__, url_prefix="/")

@home_bp.route("/", methods=['GET'])
def users():
    user = session.get('user')
    print(user)
    if user:
        userId = user.get('username')
        try:
            response = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
            cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching cryptos: {e}")
            cryptos = []

        # Real-time wallet balance calculation
        wallet_balances = defaultdict(lambda: Decimal('0'))
        
        # Calculate crypto totals using exact same method as crypto.py
        crypto_totals_map = {}
        try:
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
                    'currency': ''
                }
                
                for tx in transactions:
                    try:
                        qty = Decimal(str(tx.get('quantity', 0)))
                        price = Decimal(str(tx.get('price', 0)))
                        fee = Decimal(str(tx.get('fee', 0)))
                        side = str(tx.get('side', 'buy')).lower()
                        
                        # Transaction value (qty * price + fee)
                        tx_value = (qty * price) + fee
                        
                        to_wallet = tx.get("toWallet")
                        from_wallet = tx.get("fromWallet")
                        
                        if side == 'buy':
                            # BUY: Add to holdings and cost basis
                            entry['total_qty'] += qty
                            entry['total_cost'] += tx_value
                            entry['total_value_buy'] += tx_value
                            entry['total_fee'] += fee
                            
                            # Wallet balance: money goes out of from_wallet, crypto goes into to_wallet
                            if from_wallet:
                                wallet_balances[from_wallet] -= tx_value  # Cash out
                            if to_wallet:
                                wallet_balances[to_wallet] += tx_value   # Crypto asset in
                                
                        elif side == 'sell':
                            # SELL: Use weighted average to calculate cost of sold portion
                            if entry['total_qty'] > 0:
                                # Calculate current weighted average cost per unit
                                avg_cost_per_unit = entry['total_cost'] / entry['total_qty']
                                
                                # Cost basis of the sold quantity
                                sold_cost = qty * avg_cost_per_unit
                                
                                # Update holdings
                                entry['total_qty'] -= qty
                                entry['total_cost'] -= sold_cost
                                entry['total_value_sell'] += (qty * price)  # Revenue from sale (excluding fee)
                                entry['total_fee'] += fee
                                
                                # Ensure we don't go negative
                                if entry['total_qty'] < 0:
                                    entry['total_qty'] = Decimal(0)
                                if entry['total_cost'] < 0:
                                    entry['total_cost'] = Decimal(0)
                            else:
                                # Selling without holdings (short sell) - track as negative
                                entry['total_qty'] -= qty
                                entry['total_value_sell'] += (qty * price)
                                entry['total_fee'] += fee
                        
                            # Wallet balance: crypto goes out of from_wallet, money goes into to_wallet
                            revenue = qty * price
                            if from_wallet:
                                wallet_balances[from_wallet] -= tx_value  # Crypto asset out (at cost basis)
                            if to_wallet:
                                wallet_balances[to_wallet] += revenue   # Cash in (minus fees)
                        
                        # Set currency from first transaction
                        if not entry['currency'] and tx.get('currency'):
                            entry['currency'] = tx.get('currency')
                            
                    except Exception as e:
                        print(f"Error processing transaction for {name}: {e}")
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
            
        # Process regular transactions
        for transaction in transactions:
            try:
                amt = Decimal(str(transaction.get('amount', 0) or transaction.get('price', 0)))
                fee = Decimal(str(transaction.get('fee', 0)))
                
                # Calculate total transaction value (amount + fee)
                transaction_value = amt + fee
                
                to_wallet = transaction.get('toWallet')
                from_wallet = transaction.get('fromWallet')
                
                if to_wallet:
                    wallet_balances[to_wallet] += transaction_value
                if from_wallet:
                    wallet_balances[from_wallet] -= transaction_value
                    
            except Exception as e:
                print(f"Error processing transaction {transaction}: {e}")
                continue        # Fetch and process stock transactions  
        try:
            response = requests.get(f"{API_URL}/stocks", params={"userId": userId}, auth=aws_auth)
            stocks = response.json().get("stocks", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching stocks: {e}")
            stocks = []
        # Process stocks
        for stock in stocks:
            try:
                quantity = Decimal(str(stock.get('quantity', 0)))
                price = Decimal(str(stock.get('price', 0)))
                fee = Decimal(str(stock.get('fee', 0)))
                side = stock.get('side', 'buy')
                wallet = stock.get('wallet')
                
                # Calculate transaction value using quantity * price + fee
                transaction_value = quantity * price + fee
                
                if wallet:
                    if side.lower() == 'buy':
                        wallet_balances[wallet] -= transaction_value  # Money spent (including fee)
                    else:  # sell
                        wallet_balances[wallet] += (quantity * price - fee)  # Money received (minus fee)
                        
            except Exception as e:
                print(f"Error processing stock {stock}: {e}")
                continue

        # Fetch wallet names
        try:
            response = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
            wallets = response.json().get("wallets", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching wallets: {e}")
            wallets = []

        # Build wallet list with balances
        wallet_list = []
        for wallet in wallets:
            wallet_id = wallet.get('walletId')
            wallet_name = wallet.get('walletName')
            balance = wallet_balances.get(wallet_id, Decimal('0'))
            
            wallet_list.append({
                'walletId': wallet_id,
                'walletName': wallet_name,
                'balance': float(round(balance, 2))
            })

        # Fetch live prices from CoinGecko for chart values
        crypto_chart_data = {}
        try:
            # Get top coins from CoinGecko
            cg_url = "https://api.coingecko.com/api/v3/coins/markets"
            cg_params = {"vs_currency": "eur", "order": "market_cap_desc", "per_page": 500, "page": 1}
            cg_resp = requests.get(cg_url, params=cg_params, timeout=10)
            if cg_resp.status_code == 200:
                coins = cg_resp.json()
                
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
                for name, entry in crypto_totals_map.items():
                    if entry['total_qty'] > 0:  # Only show cryptos with positive holdings
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
                        
                        # Compute live total value based on latest price (exact same formula as crypto.py)
                        total_value_live = entry['total_qty'] * (latest_price or Decimal(0))
                        crypto_chart_data[name] = float(total_value_live)
                            
            else:
                print(f"CoinGecko returned status {cg_resp.status_code}")
                # Fallback: use 0 for live values if no price available (same as crypto.py)
                for name, entry in crypto_totals_map.items():
                    if entry['total_qty'] > 0:
                        total_value_live = entry['total_qty'] * Decimal(0)  # No price = 0 value
                        crypto_chart_data[name] = float(total_value_live)
                        
        except Exception as e:
            print(f"Error fetching CoinGecko prices: {e}")
            # Fallback: use 0 for live values if no price available (same as crypto.py)
            for name, entry in crypto_totals_map.items():
                if entry['total_qty'] > 0:
                    total_value_live = entry['total_qty'] * Decimal(0)  # No price = 0 value
                    crypto_chart_data[name] = float(total_value_live)

        cryptoLabels = list(crypto_chart_data.keys())    
        cryptoValues = list(crypto_chart_data.values()) 

        return render_template("home.html", cryptoLabels=cryptoLabels, cryptoValues=cryptoValues, 
                               wallets=wallet_list, userId=userId)
 
    else:
        return render_template("home.html")
