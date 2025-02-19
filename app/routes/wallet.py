# from flask import Blueprint, render_template

# wallet_bp = Blueprint("wallet", __name__, url_prefix="/wallet")

# @wallet_bp.route("/")
# def wallet():
#     return render_template("wallet.html")

from flask import Blueprint, render_template, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth

wallet_bp = Blueprint('wallet', __name__)

@wallet_bp.route('/')
def index():
    try:
        response = requests.get(f"{API_URL}/wallets", auth=aws_auth)
        wallets = response.json().get("wallets", []) if response.status_code == 200 else []
    except Exception:
        wallets = []
    return render_template("wallet.html", wallets=wallets)

@wallet_bp.route('/wallet', methods=['POST'])
def create_wallet():
    wallet_id = str(uuid.uuid4())
    user_id = "123"
    data = {
        "walletId": wallet_id,
        "userId": user_id,
        "walletName": request.form["walletName"],
        "walletType": request.form["walletType"],
        "accountNumber": request.form["accountNumber"],
        "note": request.form["note"],
        "currency": request.form["currency"],
        "balance": request.form["balance"]
    }
    try:
        requests.post(f"{API_URL}/wallet", json=data, auth=aws_auth)
        return redirect(url_for("wallet.index"))
    except:
        return jsonify({"error": "Internal Server Error"}), 500

@wallet_bp.route('/update', methods=['POST'])
def update_wallet():
    data = {
        "walletId": request.form["walletId"],
        "userId": "123",
        "currency": request.form["currency"],
        "walletName": request.form["walletName"],
        "walletType": request.form["walletType"],
        "accountNumber": request.form["accountNumber"],
        "balance": request.form["balance"],
        "note": request.form["note"]
    }
    try:
        requests.patch(f"{API_URL}/wallet", json=data, auth=aws_auth)
        return redirect(url_for("wallet.index"))
    except:
        return jsonify({"error": "Internal Server Error"}), 500

@wallet_bp.route('/delete/<wallet_id>/<user_id>', methods=['POST'])
def delete_wallet(wallet_id, user_id):
    data = {"walletId": wallet_id, "userId": user_id}
    try:
        requests.delete(f"{API_URL}/wallet", json=data, auth=aws_auth)
        return redirect(url_for("wallet.index"))
    except:
        return jsonify({"error": "Internal Server Error"}), 500
