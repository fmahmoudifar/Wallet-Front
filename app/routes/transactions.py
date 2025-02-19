from flask import Blueprint, render_template

transactions_bp = Blueprint("transactions", __name__, url_prefix="/transactions")

@transactions_bp.route("/")
def transactions():
    return render_template("transactions.html")