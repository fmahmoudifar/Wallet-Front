import uuid
from decimal import Decimal

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.services.user_scope import filter_records_by_user
from config import API_URL, aws_auth

# Reuse the same Settings currency + FX conversion helpers
from .crypto import (
    _get_fx_rate,
    _get_user_base_currency,
    _normalize_currency,
)

fiat_bp = Blueprint("fiat", __name__)


def _to_decimal(val) -> Decimal:
    try:
        if val is None:
            return Decimal(0)
        s = str(val).strip().replace(",", ".")
        if s == "":
            return Decimal(0)
        return Decimal(s)
    except Exception:
        return Decimal(0)


@fiat_bp.route("/fiat", methods=["GET"])
def fiat_page():
    user = session.get("user")
    if user:
        userId = user.get("username")

        base_currency = _get_user_base_currency(userId)
        fx_warning = False
        try:
            response = requests.get(f"{API_URL}/transactions", params={"userId": userId}, auth=aws_auth)
            transactions = response.json().get("transactions", []) if response.status_code == 200 else []
            transactions = filter_records_by_user(transactions, userId)
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            transactions = []

        # For overview charts/totals only: convert amounts/fees to the user's settings currency.
        # History remains original amount + original currency.
        for tx in transactions or []:
            try:
                tx_currency = _normalize_currency(tx.get("currency"), base_currency)
                amt_raw = _to_decimal(tx.get("amount", 0))
                fee_raw = _to_decimal(tx.get("fee", 0))

                fx_rate = _get_fx_rate(tx_currency, base_currency)
                tx["amountSetting"] = float(amt_raw * fx_rate)
                tx["feeSetting"] = float(fee_raw * fx_rate)
                tx["settingCurrency"] = base_currency
            except Exception:
                fx_warning = True
                try:
                    tx["amountSetting"] = float(_to_decimal(tx.get("amount", 0)))
                    tx["feeSetting"] = float(_to_decimal(tx.get("fee", 0)))
                except Exception:
                    tx["amountSetting"] = 0
                    tx["feeSetting"] = 0
                tx["settingCurrency"] = base_currency

        try:
            w_resp = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
            wallets = w_resp.json().get("wallets", []) if w_resp.status_code == 200 else []
            wallets = filter_records_by_user(wallets, userId)
        except Exception as e:
            print(f"Error fetching wallets: {e}")
            wallets = []

        return render_template(
            "fiat.html",
            transactions=transactions,
            userId=userId,
            wallets=wallets,
            base_currency=base_currency,
            fx_warning=fx_warning,
        )
    else:
        return render_template("home.html")


@fiat_bp.route("/fiat", methods=["POST"])
def create_fiat_transaction():
    trans_id = str(uuid.uuid4())
    user = session.get("user")
    if user:
        user_id = user.get("username")
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
        print(data)
        try:
            response = requests.post(f"{API_URL}/transaction", json=data, auth=aws_auth)
            print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")
            return redirect(url_for("fiat.fiat_page"))
        except Exception as e:
            print(f"❌ [ERROR] Failed to create transaction: {str(e)}")
            return jsonify({"error": "Internal Server Error"}), 500
    else:
        print("failed")
        fd = "Please login and try again"
        return render_template("fiat.html", fd=fd)


@fiat_bp.route("/updateFiat", methods=["POST"])
def update_fiat_transaction():
    user = session.get("user")
    if not user:
        return redirect(url_for("home.home_page"))
    user_id = user.get("username")

    data = {
        "transId": request.form["transId"],
        # Never trust userId from the client; scope to the logged-in user.
        "userId": user_id,
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
    print(f"🔄 [DEBUG] Updating transaction: {data}")

    try:
        response = requests.patch(f"{API_URL}/transaction", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")
        return redirect(url_for("fiat.fiat_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update transaction: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


@fiat_bp.route("/deleteFiat/<trans_id>/<user_id>", methods=["POST"])
def delete_fiat_transaction(trans_id, user_id):
    user = session.get("user")
    if not user:
        return redirect(url_for("home.home_page"))
    session_user_id = user.get("username")

    data = {
        "transId": trans_id,
        # Never trust userId from the URL; scope to the logged-in user.
        "userId": session_user_id,
    }
    print(f"🗑️ [DEBUG] Deleting transaction: {data}")

    try:
        response = requests.delete(f"{API_URL}/transaction", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")
        return redirect(url_for("fiat.fiat_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete transaction: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
