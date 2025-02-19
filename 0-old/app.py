from flask import Flask, jsonify, render_template, request, redirect, url_for
import boto3
import requests
from requests_aws4auth import AWS4Auth
from dotenv import load_dotenv
import os
import uuid  # Import UUID module

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
    """Create a new wallet with a unique UUID."""
    wallet_id = str(uuid.uuid4())  # Generate a unique wallet ID
    # user_id = request.form["userId"]
    user_id = "123"
    wallet_name = request.form["walletName"]
    wallet_type = request.form["walletType"]
    account_number = request.form["accountNumber"]
    note = request.form["note"]
    currency = request.form["currency"]
    balance = request.form["balance"]

    data = {
        "walletId": wallet_id,  # Include UUID
        "userId": user_id,
        "walletName": wallet_name,
        "walletType": wallet_type,
        "accountNumber": account_number,
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
    wallet_id = request.form["walletId"]
    # user_id = request.form["userId"]
    user_id = "123"
    currency = request.form["currency"]
    new_walletName = request.form["walletName"]
    new_walletType = request.form["walletType"]
    new_accountNumber = request.form["accountNumber"]
    new_balance = request.form["balance"]
    new_note = request.form["note"]

    data = {
        "walletId": wallet_id,
        "userId": user_id,
        "currency": currency,
        "walletName": new_walletName,
        "walletType": new_walletType,
        "accountNumber": new_accountNumber,
        "balance": new_balance,
        "note": new_note
    }
    print(f"🔄 [DEBUG] Updating wallet: {data}")

    try:
        response = requests.patch(f"{API_URL}/wallet", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("index"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update wallet: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@app.route('/delete/<wallet_id>/<user_id>', methods=['POST'])
def delete_wallet(wallet_id, user_id):
    """Delete a wallet."""
    data = {
        "walletId": wallet_id,
        "userId": user_id
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
