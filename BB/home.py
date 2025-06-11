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
    if user:
        username = user.get('username')
    #    return render_template('home.html', user=user)
    # else:
        # return redirect(url_for('auth.login'))
        try:
            response = requests.get(f"{API_URL}/cryptos", auth=aws_auth) 
            cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        except Exception:
            cryptos = []

        crypto_totals = defaultdict(float)
        to_wallet_totals = defaultdict(float)

        for crypto in cryptos:
            quantity = float(crypto.get("quantity") or 0)
            crypto_totals[crypto["cryptoName"]] += quantity
            to_wallet_totals[crypto["toWallet"]] += quantity
 
        # try:
        #     response = requests.get(f"{API_URL}/stocks", auth=aws_auth)  
        #     stocks = response.json().get("stocks", []) if response.status_code == 200 else []
        # except Exception:
        #     stocks = []

        # stock_totals = defaultdict(float)
        # to_wallet_totals_s = defaultdict(float)

        # for stock in stocks:
        #     print("Print this:")
        #     print(stock["quantity"])
        #     quantity = float(stock.get("quantity") or 0)
        #     stock_totals[stock["stockName"]] += quantity
        #     to_wallet_totals_s[stock["toWallet"]] += quantity    
            
        # try:
        #     response = requests.get(f"{API_URL}/transactions", auth=aws_auth)  
        #     transactions = response.json().get("transactions", []) if response.status_code == 200 else []
        # except Exception:
        #     transactions = []

        # transaction_totals = defaultdict(float)
        # to_wallet_totals_t = defaultdict(float)

        # for transaction in transactions:
        #     amount = float(transaction["amount"])
        #     transaction_totals[transaction["mainCat"]] += amount
        #     to_wallet_totals_t[transactions["toWallet"]] += amount           
        

        return render_template("home.html", cryptos=cryptos, crypto_totals=crypto_totals, to_wallet_totals=to_wallet_totals,
                               user = user, username = username
                            #    , transactions=transactions, transaction_totals=transaction_totals, to_wallet_totals_t=to_wallet_totals_t,
                            #    stocks=stocks, stock_totals=stock_totals, to_wallet_totals_s=to_wallet_totals_s 
                               )
 
    else:
        # return redirect(url_for('auth.login'))
        return render_template("home.html")
