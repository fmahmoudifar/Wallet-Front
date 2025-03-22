from flask import Blueprint, render_template, request, redirect, url_for, jsonify
import requests
from collections import defaultdict
from config import API_URL, aws_auth
from decimal import Decimal
from datetime import datetime

home_bp = Blueprint("home", __name__, url_prefix="/")

@home_bp.route("/", methods=['GET'])
def users():
#     return render_template("home.html")

#     try:
#         response = requests.get(f"{API_URL}/cryptos", auth=aws_auth)
#         cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
#     except Exception:
#         cryptos = []
#     return render_template("crypto.html", cryptos=cryptos)



# API_URL = "https://your-api-url.com"  # Replace with your actual API URL

# @app.route('/crypto', methods=['GET'])
# def crypto_page():
    try:
        response = requests.get(f"{API_URL}/cryptos")  
        cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
    except Exception:
        cryptos = []

    # Aggregate quantities by crypto name
    crypto_totals = defaultdict(float)
    for crypto in cryptos:
        crypto_totals[crypto["cryptoName"]] += float(crypto["quantity"])

    return render_template("home.html", cryptos=cryptos, crypto_totals=crypto_totals)

