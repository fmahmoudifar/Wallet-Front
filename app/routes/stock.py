from flask import Blueprint, render_template

stock_bp = Blueprint("stock", __name__, url_prefix="/stock")

@stock_bp.route("/")
def users():
    return render_template("stock.html")