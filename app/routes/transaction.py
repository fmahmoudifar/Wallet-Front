from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth
from decimal import Decimal


transaction_bp = Blueprint('transaction', __name__)

@transaction_bp.route('/transaction', methods=['GET'])
def transaction_page():
    user = session.get('user')
    if user:
        userId = user.get('username')
        try:
            response = requests.get(f"{API_URL}/transactions", auth=aws_auth)
            transactions = response.json().get("transactions", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            transactions = []
        return render_template("transaction.html", transactions=transactions, userId=userId)
    else:
        return render_template("home.html")

@transaction_bp.route('/transaction', methods=['POST'])
def create_transaction():
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
            "price": request.form["price"],  
            "currency": request.form["currency"],
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
    else:
        print('failed')
        fd = 'Please login and try again'
        # return redirect(url_for("transaction.transaction_page"))
        return render_template("transaction.html", fd=fd)
        
 
@transaction_bp.route('/updateTrans', methods=['POST'])
def update_transaction():
    data = {
        "transId": request.form["transId"],
        "userId": request.form["userId"],
        "transType": request.form["transType"],
        "mainCat": request.form["mainCat"],
        "tdate": request.form["tdate"],    
        "amount": request.form["amount"],     
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"], 
        "price": request.form["price"],  
        "currency": request.form["currency"], 
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