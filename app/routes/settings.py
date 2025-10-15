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
    
    try:
        response = requests.patch(f"{API_URL}/settings", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("settings.settings_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update settings: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500


    
