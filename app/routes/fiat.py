from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth


fiat_bp = Blueprint('fiat', __name__)


@fiat_bp.route('/fiat', methods=['GET'])
def fiat_page():
    user = session.get('user')
    if user:
        userId = user.get('username')
        try:
            response = requests.get(f"{API_URL}/transactions", params={"userId": userId}, auth=aws_auth)
            transactions = response.json().get("transactions", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            transactions = []

        try:
            w_resp = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
            wallets = w_resp.json().get("wallets", []) if w_resp.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching wallets: {e}")
            wallets = []

        return render_template("fiat.html", transactions=transactions, userId=userId, wallets=wallets)
    else:
        return render_template("home.html")


@fiat_bp.route('/fiat', methods=['POST'])
def create_fiat_transaction():
    trans_id = str(uuid.uuid4())
    user = session.get('user')
    if user:
        user_id = user.get('username')
        data = {
            "transId": trans_id,
            "userId": user_id,
            "transType": request.form["transType"],
            "tdate": request.form["tdate"],
            "fromWallet": request.form["fromWallet"],
            "amount": request.form["amount"],
            "toWallet": request.form["toWallet"],
            "mainCat": request.form["mainCat"],
            "currency": request.form["currency"],
            "fee": request.form["fee"],
            "note": request.form["note"],
        }
        # Backwards compatibility: legacy backend schema may still expect a price field.
        # Fiat transactions no longer expose or use price in the UI, so default to 0.
        data["price"] = request.form.get("price", "0")
        print(data)
        try:
            response = requests.post(f"{API_URL}/transaction", json=data, auth=aws_auth)
            print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")
            return redirect(url_for("fiat.fiat_page"))
        except Exception as e:
            print(f"❌ [ERROR] Failed to create transaction: {str(e)}")
            return jsonify({"error": "Internal Server Error"}), 500
    else:
        print('failed')
        fd = 'Please login and try again'
        return render_template("fiat.html", fd=fd)


@fiat_bp.route('/updateFiat', methods=['POST'])
def update_fiat_transaction():
    data = {
        "transId": request.form["transId"],
        "userId": request.form["userId"],
        "transType": request.form["transType"],
        "mainCat": request.form["mainCat"],
        "tdate": request.form["tdate"],
        "amount": request.form["amount"],
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "currency": request.form["currency"],
        "fee": request.form["fee"],
        "note": request.form["note"],
    }
    # Backwards compatibility: legacy backend schema may still expect a price field.
    data["price"] = request.form.get("price", "0")
    print(f"🔄 [DEBUG] Updating transaction: {data}")

    try:
        response = requests.patch(f"{API_URL}/transaction", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")
        return redirect(url_for("fiat.fiat_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update transaction: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


@fiat_bp.route('/deleteFiat/<trans_id>/<user_id>', methods=['POST'])
def delete_fiat_transaction(trans_id, user_id):
    data = {
        "transId": trans_id,
        "userId": user_id,
    }
    print(f"🗑️ [DEBUG] Deleting transaction: {data}")

    try:
        response = requests.delete(f"{API_URL}/transaction", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")
        return redirect(url_for("fiat.fiat_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete transaction: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
