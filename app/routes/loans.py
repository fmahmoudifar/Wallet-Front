from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
import uuid
from decimal import Decimal

from config import API_URL, aws_auth

loans_bp = Blueprint('loans', __name__)


def _to_decimal(val) -> Decimal:
    try:
        if val is None:
            return Decimal(0)
        s = str(val).strip()
        if s == '':
            return Decimal(0)
        return Decimal(s)
    except Exception:
        return Decimal(0)


@loans_bp.route('/loans', methods=['GET'])
def loans_page():
    user = session.get('user')
    if not user:
        return render_template('home.html')

    user_id = user.get('username')

    # --- Fetch loans ---
    try:
        resp = requests.get(f"{API_URL}/loans", params={"userId": user_id}, auth=aws_auth, timeout=12)
        loans = resp.json().get('loans', []) if resp.status_code == 200 else []
    except Exception as e:
        print(f"Error fetching loans: {e}")
        loans = []

    # --- Fetch wallets (for dropdowns + id->name mapping) ---
    try:
        w_resp = requests.get(f"{API_URL}/wallets", params={"userId": user_id}, auth=aws_auth, timeout=12)
        wallets = w_resp.json().get('wallets', []) if w_resp.status_code == 200 else []
    except Exception as e:
        print(f"Error fetching wallets: {e}")
        wallets = []

    # --- Overview totals (simple, based on loan type) ---
    receive_total = Decimal(0)
    owe_total = Decimal(0)
    base_currency = (session.get('currency') or 'EUR').strip().upper() or 'EUR'
    try:
        for row in (loans or []):
            t = str(row.get('type') or '').strip().lower()
            status = str(row.get('status') or '').strip().lower()
            # Count only open items by default for overview.
            if status and status not in ('open', 'pending'):
                continue
            amt = _to_decimal(row.get('amount'))
            if t == 'lend':
                receive_total += amt
            elif t == 'borrow':
                owe_total += amt
    except Exception:
        receive_total = Decimal(0)
        owe_total = Decimal(0)

    return render_template(
        "loans.html",
        loans=loans,
        wallets=wallets,
        receive_total=float(receive_total),
        owe_total=float(owe_total),
        base_currency=base_currency,
    )


@loans_bp.route('/loans', methods=['POST'])
def create_loan_transaction():
    user = session.get('user')
    if not user:
        return redirect(url_for('home.home_page'))

    user_id = user.get('username')
    loan_id = str(uuid.uuid4())

    data = {
        "loanId": loan_id,
        "userId": user_id,
        "type": request.form.get('type', ''),
        "status": request.form.get('status', ''),
        "counterparty": request.form.get('counterparty', ''),
        "amount": request.form.get('amount', ''),
        "currency": request.form.get('currency', ''),
        "walletId": request.form.get('walletId', ''),
        "tdate": request.form.get('tdate', ''),
        "dueDate": request.form.get('dueDate', ''),
        "note": request.form.get('note', ''),
    }

    try:
        resp = requests.post(f"{API_URL}/loan", json=data, auth=aws_auth, timeout=12)
        if resp.status_code in (200, 201):
            return redirect(url_for('loans.loans_page'))

        # Bubble up backend message for debugging
        try:
            detail = resp.json()
        except Exception:
            detail = {"text": resp.text}
        print(f"[Loans] Create failed: {resp.status_code} {detail}")
        return jsonify({"error": "Failed to create loan", "detail": detail}), 500
    except Exception as e:
        print(f"[Loans] Create exception: {e}")
        return jsonify({"error": "Internal Server Error"}), 500


@loans_bp.route('/updateLoan', methods=['POST'])
def update_loan_transaction():
    user = session.get('user')
    if not user:
        return redirect(url_for('home.home_page'))

    user_id = user.get('username')

    data = {
        "loanId": request.form.get('loanId', ''),
        "userId": user_id,
        "type": request.form.get('type', ''),
        "status": request.form.get('status', ''),
        "counterparty": request.form.get('counterparty', ''),
        "amount": request.form.get('amount', ''),
        "currency": request.form.get('currency', ''),
        "walletId": request.form.get('walletId', ''),
        "tdate": request.form.get('tdate', ''),
        "dueDate": request.form.get('dueDate', ''),
        "note": request.form.get('note', ''),
    }

    try:
        response = requests.patch(f"{API_URL}/loan", json=data, auth=aws_auth, timeout=12)
        try:
            print(f"✅ [Loans] Update Response: {response.status_code}, JSON: {response.json()}")
        except Exception:
            print(f"✅ [Loans] Update Response: {response.status_code}, Text: {response.text}")
        return redirect(url_for('loans.loans_page'))
    except Exception as e:
        print(f"❌ [Loans] Failed to update loan: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


@loans_bp.route('/deleteloan/<loan_id>', methods=['POST'])
def delete_loan_transaction(loan_id):
    user = session.get('user')
    if not user:
        return redirect(url_for('home.home_page'))

    user_id = user.get('username')
    data = {
        "loanId": loan_id,
        "userId": user_id,
    }

    try:
        response = requests.delete(f"{API_URL}/loan", json=data, auth=aws_auth, timeout=12)
        try:
            print(f"✅ [Loans] Delete Response: {response.status_code}, JSON: {response.json()}")
        except Exception:
            print(f"✅ [Loans] Delete Response: {response.status_code}, Text: {response.text}")
        return redirect(url_for('loans.loans_page'))
    except Exception as e:
        print(f"❌ [Loans] Failed to delete loan: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
