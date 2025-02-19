from flask import Blueprint, render_template

wallet_bp = Blueprint("wallet", __name__, url_prefix="/wallet")

@wallet_bp.route("/")
def wallet():
    return render_template("wallet.html")