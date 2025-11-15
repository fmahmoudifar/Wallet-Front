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

        crypto_totals = defaultdict(float)
        
        # Real-time wallet balance calculation
        wallet_balances = defaultdict(lambda: Decimal('0'))
        
        # Process crypto transactions (considering side: buy/sell)
        for crypto in cryptos:
            try:
                quantity = Decimal(str(crypto.get("quantity") or 0))
                price = Decimal(str(crypto.get("price") or 0))
                side = crypto.get("side", "buy").lower()
                
                # Calculate value
                value = quantity * price
                
                to_wallet = crypto.get("toWallet")
                from_wallet = crypto.get("fromWallet")
                
                if side == "buy":
                    # Buy: money goes out of from_wallet, crypto goes into to_wallet
                    if from_wallet:
                        wallet_balances[from_wallet] -= value  # Cash out
                    if to_wallet:
                        wallet_balances[to_wallet] += value   # Crypto asset in
                elif side == "sell":
                    # Sell: crypto goes out of from_wallet, money goes into to_wallet
                    if from_wallet:
                        wallet_balances[from_wallet] -= value  # Crypto asset out
                    if to_wallet:
                        wallet_balances[to_wallet] += value   # Cash in
                
                # For chart display
                crypto_totals[crypto.get("cryptoName")] += float(quantity)
                
            except Exception as e:
                print(f"Error processing crypto transaction: {e}")
                continue

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

        cryptoLabels = list(crypto_totals.keys())    
        cryptoValues = list(crypto_totals.values()) 

        return render_template("home.html", cryptoLabels=cryptoLabels, cryptoValues=cryptoValues, 
                               wallets=wallet_list, userId=userId)
 
    else:
        return render_template("home.html")
