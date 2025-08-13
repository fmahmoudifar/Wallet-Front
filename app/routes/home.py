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
        #     response = requests.get(f"{API_URL}/cryptos", auth=aws_auth) 
        #     cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        # except Exception:
        #     cryptos = []
            response = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
            cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching cryptos: {e}")
            cryptos = []

        crypto_totals = defaultdict(float)
        to_wallet_totals = defaultdict(float)

        for crypto in cryptos:
            quantity = float(crypto.get("quantity") or 0)
            crypto_totals[crypto["cryptoName"]] += quantity
            to_wallet_totals[crypto["toWallet"]] += quantity

        return render_template("home.html", cryptos=cryptos, crypto_totals=crypto_totals, to_wallet_totals=to_wallet_totals,
                               userId=userId)
 
    else:
        return render_template("home.html")
