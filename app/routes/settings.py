from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth
from decimal import Decimal
from datetime import datetime

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/settings', methods=['GET'])
def settings_page():
    user = session.get('user')
    if user:
        userId = user.get('username')

        # --- Fetch settings ---
        try:
            response = requests.get(f"{API_URL}/settings", params={"userId": userId}, auth=aws_auth)
            settings = response.json().get("settings", []) if response.status_code == 200 else []
            print(settings)
        except Exception as e:
            print(f"Error fetching settings: {e}")
            settings = []

        # Store theme in session (used by base layout for light/dark mode)
        try:
            if settings and isinstance(settings, list):
                theme = (settings[0] or {}).get('theme')
                if theme:
                    session['theme'] = theme
        except Exception:
            pass

        # Store website currency in session so portfolio pages can calculate/display in base currency
        try:
            if settings and isinstance(settings, list):
                currency = (settings[0] or {}).get('currency')
                if currency:
                    session['currency'] = currency
        except Exception:
            pass

        # Send both to template (include coin list)
        return render_template("settings.html", settings=settings, userId=userId)
    else:
        return render_template("home.html")


@settings_bp.route('/updateSettings', methods=['POST'])
def update_settings():
    user = session.get('user')
    user_id = user.get('username')
    data = {
        "userId": user_id,
        "currency": request.form["currency"],
        "theme": request.form["theme"]
    }
    print(f"🔄 [DEBUG] Updating settings: {data}")

    # Update theme in session immediately
    try:
        session['theme'] = data.get('theme')
    except Exception:
        pass

    # Update currency in session immediately
    try:
        session['currency'] = data.get('currency')
    except Exception:
        pass
    
    try:
        response = requests.patch(f"{API_URL}/settings", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("settings.settings_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update settings: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


    
