from flask import Blueprint, render_template, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth
from decimal import Decimal


crypto_bp = Blueprint('crypto', __name__)

@crypto_bp.route('/crypto', methods=['GET'])
def crypto_page():
    try:
        response = requests.get(f"{API_URL}/cryptos", auth=aws_auth)
        cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
    except Exception:
        cryptos = []
    return render_template("crypto.html", cryptos=cryptos)

@crypto_bp.route('/crypto', methods=['POST'])
def create_crypto():
    crypto_id = str(uuid.uuid4())
    user_id = "123"
    data = {
        "cryptoId": crypto_id,
        "userId": user_id,
        "mtype": request.form["mtype"],
        "cryptoType": request.form["cryptoType"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "mainCat": request.form["mainCat"],
        "subCat": request.form["subCat"],
        "amount": request.form["amount"],  
        "price": request.form["price"],  
        "currency": request.form["currency"],
        "fee": request.form["fee"],
        "note": request.form["note"]
    }

    print(data)
    try:
        response = requests.post(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@crypto_bp.route('/updateCrypto', methods=['POST'])
def update_crypto():
    data = {
        "cryptoId": request.form["cryptoId"],
        "userId": request.form["userId"],
        "mtype": request.form["mtype"],
        "cryptoType": request.form["cryptoType"],
        "mainCat": request.form["mainCat"],
        "subCat": request.form["subCat"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "amount": request.form["amount"],  
        "price": request.form["price"],  
        "currency": request.form["currency"], 
        "fee": request.form["fee"],  
        "note": request.form["note"]
    }
    print(f"🔄 [DEBUG] Updating crypto: {data}")
    
    try:
        response = requests.patch(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@crypto_bp.route('/deleteCrypto/<crypto_id>/<user_id>', methods=['POST'])
def delete_crypto(crypto_id, user_id):
    """Delete a crypto."""
    data = {
        "cryptoId": crypto_id,
        "userId": user_id
    }
    print(f"🗑️ [DEBUG] Deleting crypto: {data}")

    try:
        response = requests.delete(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500