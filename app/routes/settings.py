import json
import os
import requests
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.services.user_scope import filter_records_by_user
from config import API_URL, aws_auth

_SETTINGS_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "settings_defaults.json")
with open(_SETTINGS_DEFAULTS_PATH, "r") as _f:
    _SETTING_DEFAULTS = json.load(_f)

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings", methods=["GET"])
def settings_page():
    user = session.get("user")
    if user:
        userId = user.get("username")

        # --- Fetch settings ---
        try:
            response = requests.get(f"{API_URL}/settings", params={"userId": userId}, auth=aws_auth)
            settings = response.json().get("settings", []) if response.status_code == 200 else []
            settings = filter_records_by_user(settings, userId)
            print(settings)
        except Exception as e:
            print(f"Error fetching settings: {e}")
            settings = []

        first = (settings[0] if settings and isinstance(settings, list) else None) or {}

        # Sync all settings fields to session
        for key in ("theme", "currency", "dashboardColors", "incomeCategories", "expenseCategories"):
            val = first.get(key)
            if val:
                session[key] = val

        income_categories = first.get("incomeCategories") or _SETTING_DEFAULTS["incomeCategories"]
        expense_categories = first.get("expenseCategories") or _SETTING_DEFAULTS["expenseCategories"]
        dashboard_colors = first.get("dashboardColors") or _SETTING_DEFAULTS["dashboardColors"]

        return render_template(
            "settings.html",
            settings=settings,
            userId=userId,
            income_categories=income_categories,
            expense_categories=expense_categories,
            dashboard_colors=dashboard_colors,
            default_dashboard_colors=_SETTING_DEFAULTS.get("dashboardColors", {}),
        )
    else:
        return render_template("home.html")


@settings_bp.route("/updateSettings", methods=["POST"])
def update_settings():
    user = session.get("user")
    user_id = user.get("username")
    data = {"userId": user_id, "currency": request.form["currency"], "theme": request.form["theme"]}
    print(f"🔄 [DEBUG] Updating settings: {data}")

    session["theme"] = data.get("theme")
    session["currency"] = data.get("currency")

    try:
        response = requests.patch(f"{API_URL}/settings", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")
        return redirect(url_for("settings.settings_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update settings: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


@settings_bp.route("/updateCategorySettings", methods=["POST"])
def update_category_settings():
    user = session.get("user")
    if not user:
        return jsonify({"error": "Not authenticated"}), 401
    user_id = user.get("username")

    body = request.get_json(silent=True) or {}
    payload = {"userId": user_id}

    if "dashboardColors" in body:
        payload["dashboardColors"] = body["dashboardColors"]
        session["dashboardColors"] = body["dashboardColors"]
    if "incomeCategories" in body:
        payload["incomeCategories"] = body["incomeCategories"]
        session["incomeCategories"] = body["incomeCategories"]
    if "expenseCategories" in body:
        payload["expenseCategories"] = body["expenseCategories"]
        session["expenseCategories"] = body["expenseCategories"]

    try:
        response = requests.patch(f"{API_URL}/settings", json=payload, auth=aws_auth)
        print(f"✅ [DEBUG] Category settings update: {response.status_code}")
        return jsonify({"ok": True})
    except Exception as e:
        print(f"❌ [ERROR] Failed to update category settings: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
