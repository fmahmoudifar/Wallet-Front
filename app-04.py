from flask import Flask, jsonify, render_template, request, redirect, url_for, session
import boto3
import requests
from requests_aws4auth import AWS4Auth
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)

AWS_ACCESS_KEY = os.getenv("ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("SECRET_KEY")
AWS_REGION = "eu-north-1"
AWS_SERVICE = "execute-api" 

# API Gateway details
API_URL = "https://e31gpskeu0.execute-api.eu-north-1.amazonaws.com/PROD"

aws_auth = AWS4Auth(AWS_ACCESS_KEY, AWS_SECRET_KEY, AWS_REGION, AWS_SERVICE)

@app.route('/')
def index():
    """Fetch all wallets and display them."""
    print("🔹 [DEBUG] Fetching all wallets...")
    try:
        response = requests.get(f"{API_URL}/wallets", auth=aws_auth)
        wallets = response.json().get("wallets", []) if response.status_code == 200 else []
    except Exception as e:
        print(f"❌ [ERROR] Failed to fetch wallets: {str(e)}")
        wallets = []

    return render_template("index.html", wallets=wallets)

@app.route('/wallet', methods=['POST'])
def create_wallet():
    """Create a new wallet."""
    username = request.form["username"]
    wallet_name = request.form["walletName"]
    wallet_type = request.form["walletType"]
    accountNumber = request.form["accountNumber"]
    note = request.form["note"]
    currency = request.form["currency"]
    balance = request.form["balance"]

    data = {
        "username": username,
        "walletName": wallet_name,
        "walletType": wallet_type,
        "accountNumber": accountNumber,
        "note": note,
        "currency": currency,
        "balance": balance
    }
    print(f"➕ [DEBUG] Creating wallet: {data}")

    try:
        response = requests.post(f"{API_URL}/wallet", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("index"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create wallet: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@app.route('/update', methods=['POST'])
def update_wallet():
    """Update an existing wallet balance."""
    wallet_name = request.form["walletName"]
    username = request.form["username"]
    new_balance = request.form["balance"]

    data = {
        "walletName": wallet_name,
        "username": username,
        "updateKey": "balance",
        "updateValue": new_balance
    }
    print(f"🔄 [DEBUG] Updating wallet: {data}")

    try:
        response = requests.patch(f"{API_URL}/wallet", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("index"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update wallet: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@app.route('/delete/<wallet_name>/<username>', methods=['POST'])
def delete_wallet(wallet_name, username):
    """Delete a wallet."""
    data = {
        "walletName": wallet_name,
        "username": username
    }
    print(f"🗑️ [DEBUG] Deleting wallet: {data}")

    try:
        response = requests.delete(f"{API_URL}/wallet", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("index"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete wallet: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

if __name__ == '__main__':
    app.run(debug=True)