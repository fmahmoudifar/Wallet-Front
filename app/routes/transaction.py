from flask import Blueprint, render_template, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth
from decimal import Decimal


transaction_bp = Blueprint('transaction', __name__)

@transaction_bp.route('/transaction', methods=['GET'])
def transaction_page():
    try:
        response = requests.get(f"{API_URL}/transactions", auth=aws_auth)
        transactions = response.json().get("transactions", []) if response.status_code == 200 else []
    except Exception:
        transactions = []
    return render_template("transaction.html", transactions=transactions)

@transaction_bp.route('/transaction', methods=['POST'])
def create_transaction():
    trans_id = str(uuid.uuid4())
    user_id = "123"
    data = {
        "transId": trans_id,
        "userId": user_id,
        "mtype": request.form["mtype"],
        "transType": request.form["transType"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "mainCat": request.form["mainCat"],
        "subCat": request.form["subCat"],
        # "amount": float(request.form["amount"]),  
        # "price": float(request.form["price"]),  
        "amount": request.form["amount"],  
        "price": request.form["price"],  
        "currency": request.form["currency"],
        # "fee": float(request.form["fee"]),
        "fee": request.form["fee"],
        "note": request.form["note"]
    }

    print(data)
    try:
        response = requests.post(f"{API_URL}/transaction", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("transaction.transaction_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create transaction: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@transaction_bp.route('/updateTrans', methods=['POST'])
def update_transaction():
    data = {
        "transId": request.form["transId"],
        "userId": request.form["userId"],
        "mtype": request.form["mtype"],
        "transType": request.form["transType"],
        "mainCat": request.form["mainCat"],
        "subCat": request.form["subCat"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        # "amount": float(request.form["amount"]),  
        # "price": float(request.form["price"]),  
        "amount": request.form["amount"],  
        "price": request.form["price"],  
        "currency": request.form["currency"],
        # "fee": float(request.form["fee"]),  
        "fee": request.form["fee"],  
        "note": request.form["note"]
    }
    print(f"🔄 [DEBUG] Updating transaction: {data}")
    
    try:
        response = requests.patch(f"{API_URL}/transaction", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("transaction.transaction_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update transaction: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@transaction_bp.route('/deleteTrans/<trans_id>/<user_id>', methods=['POST'])
def delete_transaction(trans_id, user_id):
    """Delete a transaction."""
    data = {
        "transId": trans_id,
        "userId": user_id
    }
    print(f"🗑️ [DEBUG] Deleting transaction: {data}")

    try:
        response = requests.delete(f"{API_URL}/transaction", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("transaction.transaction_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete transaction: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500