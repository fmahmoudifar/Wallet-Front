import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
import json
import os
import re
from pathlib import Path
from urllib import parse as urlparse
from urllib import request as urlrequest

from flask import Blueprint, render_template, request, session
from dotenv import dotenv_values

from config import (
    CONTACT_TO_EMAIL,
)

contact_bp = Blueprint("contact", __name__)

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA"
_TURNSTILE_TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"


def _session_user_email() -> str:
    user = session.get("user") or {}
    if isinstance(user, dict):
        email = (user.get("email") or "").strip()
        if email:
            return email
    return str(session.get("email") or "").strip()


def _is_logged_in() -> bool:
    return bool(isinstance(session.get("user"), dict) and session.get("user"))


def _captcha_required() -> bool:
    return not _is_logged_in()


def _verify_turnstile(token: str, secret_key: str, remote_ip: str | None = None) -> bool:
    payload = {
        "secret": str(secret_key or "").strip(),
        "response": str(token or "").strip(),
    }
    if remote_ip:
        payload["remoteip"] = str(remote_ip).strip()

    body = urlparse.urlencode(payload).encode("utf-8")
    req = urlrequest.Request(_TURNSTILE_VERIFY_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
    except Exception:
        return False

    return bool(data.get("success") is True)


def _is_valid_email(value: str) -> bool:
    email = str(value or "").strip()
    if not email or len(email) > 254:
        return False

    # parseaddr strips display names and malformed wrappers.
    _, parsed = parseaddr(email)
    if parsed != email:
        return False

    pattern = r"(?i)^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+$"
    return re.fullmatch(pattern, email) is not None


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _runtime_mail_settings() -> dict:
    # Read .env at runtime so updates work without process restart.
    file_values = {}
    try:
        file_values = dotenv_values(str(_ENV_FILE)) if _ENV_FILE.exists() else {}
    except Exception:
        file_values = {}

    def _get(name: str, default: str = "") -> str:
        val = os.getenv(name)
        if val is None or str(val).strip() == "":
            val = file_values.get(name)
        return str(val if val is not None else default).strip()

    smtp_host = _get("SMTP_HOST")
    smtp_port_raw = _get("SMTP_PORT", "587")
    try:
        smtp_port = int(smtp_port_raw or "587")
    except Exception:
        smtp_port = 587

    smtp_username = _get("SMTP_USERNAME")
    smtp_password = _get("SMTP_PASSWORD")
    mail_from_email = _get("MAIL_FROM_EMAIL") or smtp_username
    contact_to_email = _get("CONTACT_TO_EMAIL", CONTACT_TO_EMAIL)
    smtp_use_tls = _is_truthy(_get("SMTP_USE_TLS", "1"))
    smtp_use_ssl = _is_truthy(_get("SMTP_USE_SSL", "0"))
    turnstile_site_key = _get("TURNSTILE_SITE_KEY")
    turnstile_secret_key = _get("TURNSTILE_SECRET_KEY")

    # In local/dev environments, default to Cloudflare Turnstile test keys
    # so captcha can be exercised without provisioning real keys first.
    is_dev_env = _is_truthy(_get("LOCAL_DEV")) or _is_truthy(_get("CODESPACES"))
    if is_dev_env and (not turnstile_site_key or not turnstile_secret_key):
        turnstile_site_key = _TURNSTILE_TEST_SITE_KEY
        turnstile_secret_key = _TURNSTILE_TEST_SECRET_KEY

    # Gmail app passwords are often copied as 4-char groups with spaces.
    # Normalize only for Gmail SMTP to avoid accidental auth failures.
    if "gmail.com" in smtp_host.lower() and smtp_password:
        smtp_password = smtp_password.replace(" ", "")

    return {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password,
        "mail_from_email": mail_from_email,
        "contact_to_email": contact_to_email,
        "smtp_use_tls": smtp_use_tls,
        "smtp_use_ssl": smtp_use_ssl,
        "turnstile_site_key": turnstile_site_key,
        "turnstile_secret_key": turnstile_secret_key,
    }


def _send_contact_email(name: str, user_email: str, subject: str, topic: str, message: str) -> None:
    settings = _runtime_mail_settings()
    smtp_host = settings["smtp_host"]
    smtp_port = settings["smtp_port"]
    smtp_username = settings["smtp_username"]
    smtp_password = settings["smtp_password"]
    mail_from_email = settings["mail_from_email"]
    contact_to_email = settings["contact_to_email"]
    smtp_use_tls = settings["smtp_use_tls"]
    smtp_use_ssl = settings["smtp_use_ssl"]

    missing = []
    if not smtp_host:
        missing.append("SMTP_HOST")
    if not smtp_username:
        missing.append("SMTP_USERNAME")
    if not smtp_password:
        missing.append("SMTP_PASSWORD")
    if not mail_from_email:
        missing.append("MAIL_FROM_EMAIL")

    if missing:
        raise RuntimeError(
            "SMTP is not configured. Missing: " + ", ".join(missing)
        )

    final_subject = f"{subject}"
    body = (
        f"Name: {name}\n"
        f"Email: {user_email}\n"
        f"Topic: {topic}\n\n"
        f"Message:\n{message}\n"
    )

    msg = EmailMessage()
    msg["To"] = CONTACT_TO_EMAIL
    msg["Subject"] = final_subject

    # For Gmail SMTP and most providers, From must be the authenticated mailbox.
    from_email = (mail_from_email or smtp_username or contact_to_email).strip()
    display_name = f"{name}"
    msg["From"] = formataddr((display_name, from_email))
    if user_email:
        msg["Reply-To"] = user_email

    msg.set_content(body)

    if smtp_use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
            if smtp_username:
                server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_username:
            server.login(smtp_username, smtp_password)
        server.send_message(msg)


@contact_bp.route("/contact", methods=["GET", "POST"])
def contact_page():
    user_email_default = _session_user_email()
    settings = _runtime_mail_settings()
    captcha_required = _captcha_required()
    turnstile_site_key = settings["turnstile_site_key"]
    turnstile_secret_key = settings["turnstile_secret_key"]
    status = None
    error = None
    form_data = {
        "name": "",
        "email": user_email_default,
        "topic": "Issue",
        "subject": "",
        "message": "",
    }

    if request.method == "POST":
        form_data["name"] = (request.form.get("name") or "").strip()
        form_data["email"] = (request.form.get("email") or "").strip()
        form_data["topic"] = (request.form.get("topic") or "Issue").strip() or "Issue"
        form_data["subject"] = (request.form.get("subject") or "").strip()
        form_data["message"] = (request.form.get("message") or "").strip()
        turnstile_token = (request.form.get("cf-turnstile-response") or "").strip()

        if not form_data["name"]:
            error = "Please enter your name."
        elif not _is_valid_email(form_data["email"]):
            error = "Please enter a valid email address."
        elif not form_data["subject"]:
            error = "Please enter a subject."
        elif not form_data["message"]:
            error = "Please enter your message."
        elif captcha_required and (not turnstile_site_key or not turnstile_secret_key):
            error = "Captcha is not configured. Please contact support."
        elif captcha_required and not _verify_turnstile(turnstile_token, turnstile_secret_key, request.remote_addr):
            error = "Captcha validation failed. Please try again."
        else:
            try:
                _send_contact_email(
                    name=form_data["name"],
                    user_email=form_data["email"],
                    subject=form_data["subject"],
                    topic=form_data["topic"],
                    message=form_data["message"],
                )
                status = "Your message has been sent. Thank you for your feedback."
                form_data["subject"] = ""
                form_data["message"] = ""
            except Exception as exc:
                error = f"Failed to send message: {exc}"

    return render_template(
        "contact.html",
        status=status,
        error=error,
        form_data=form_data,
        captcha_required=captcha_required,
        turnstile_site_key=turnstile_site_key,
    )
