from flask import Blueprint, render_template,session, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth
from decimal import Decimal
from datetime import datetime

stock_bp = Blueprint('stock', __name__)

@stock_bp.route('/stock', methods=['GET'])
def stock_page():
    user = session.get('user')
    if user:
        userId = user.get('username')
        try:
            # Assuming your API accepts a username filter as a query parameter
            response = requests.get(f"{API_URL}/stocks", params={"userId": userId}, auth=aws_auth)
            stocks = response.json().get("stocks", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching stocks: {e}")
            stocks = []
        return render_template("stock.html", stocks=stocks, userId=userId)
    else:
        return render_template("home.html")
    
# @stock_bp.route('/stock', methods=['GET'])
# def stock_page():
#     try:
#         response = requests.get(f"{API_URL}/stocks", auth=aws_auth)
#         stocks = response.json().get("stocks", []) if response.status_code == 200 else []
#     except Exception:
#         stocks = []
#     return render_template("stock.html", stocks=stocks)

@stock_bp.route('/stock', methods=['POST'])
def create_stock():
    stock_id = str(uuid.uuid4())
    user = session.get('user')
    user_id = user.get('username')
    data = {
        "stockId": stock_id,
        "userId": user_id,
        "stockName": request.form["stockName"],
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
        response = requests.post(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@stock_bp.route('/updateStock', methods=['POST'])
def update_stock():
    data = {
        "stockId": request.form["stockId"],
        "userId": request.form["userId"],
        "stockName": request.form["stockName"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "quantity": request.form["quantity"],  
        "price": request.form["price"],  
        "currency": request.form["currency"], 
        "fee": request.form["fee"],  
        "note": request.form["note"]
    }
    print(f"🔄 [DEBUG] Updating stock: {data}")
    
    try:
        response = requests.patch(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@stock_bp.route('/deletestock/<stock_id>/<user_id>', methods=['POST'])
def delete_stock(stock_id, user_id):
    """Delete a stock."""
    data = {
        "stockId": stock_id,
        "userId": user_id
    }
    print(f"🗑️ [DEBUG] Deleting stock: {data}")

    try:
        response = requests.delete(f"{API_URL}/stock", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("stock.stock_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete stock: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500