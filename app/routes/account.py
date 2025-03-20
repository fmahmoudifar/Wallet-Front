from flask import Blueprint, render_template

account_bp = Blueprint("account", __name__, url_prefix="/account")

@account_bp.route("/")
def users():
    return render_template("account.html")