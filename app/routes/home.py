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
       return render_template('home.html', user=user)
    else:
        # return redirect(url_for('auth.login'))
        try:
            response = requests.get(f"{API_URL}/cryptos")  
            cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        except Exception:
            cryptos = []

        crypto_totals = defaultdict(float)
        to_wallet_totals = defaultdict(float)

        for crypto in cryptos:
            quantity = float(crypto["quantity"])
            crypto_totals[crypto["cryptoName"]] += quantity
            to_wallet_totals[crypto["toWallet"]] += quantity

        return render_template("home.html", cryptos=cryptos, crypto_totals=crypto_totals, to_wallet_totals=to_wallet_totals, user = user)
 
