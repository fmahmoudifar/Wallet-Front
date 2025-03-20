from flask import Blueprint, render_template, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth
from decimal import Decimal
from datetime import datetime


crypto_bp = Blueprint('crypto', __name__)

@crypto_bp.route('/crypto', methods=['GET'])
def crypto_page():
    try:
        response = requests.get(f"{API_URL}/cryptos", auth=aws_auth)
        cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
    except Exception:
        cryptos = []
    return render_template("crypto.html", cryptos=cryptos)


# @crypto_bp.route('/crypto', methods=['GET'])
# def crypto_page():
#     try:
#         response = requests.get(f"{API_URL}/cryptos", auth=aws_auth)
#         cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        
#         # Convert 'tdate' to datetime for sorting
#         for crypto in cryptos:
#             crypto['tdate'] = datetime.strptime(crypto['tdate'], "%Y-%m-%dT%H:%M")

#         # Sort by 'tdate' (latest first)
#         cryptos.sort(key=lambda x: x['tdate'], reverse=True)

#         # Convert back to string format
#         for crypto in cryptos:
#             crypto['tdate'] = crypto['tdate'].strftime("%Y-%m-%d %H:%M")

#     except Exception:
#         cryptos = []

#     return render_template("crypto.html", cryptos=cryptos)


@crypto_bp.route('/crypto', methods=['POST'])
def create_crypto():
    crypto_id = str(uuid.uuid4())
    user_id = "123"
    data = {
        "cryptoId": crypto_id,
        "userId": user_id,
        "cryptoName": request.form["cryptoName"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "quantity": request.form["quantity"],  
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
        "cryptoName": request.form["cryptoName"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "quantity": request.form["quantity"],  
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