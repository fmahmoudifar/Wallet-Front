import smtplib
from email.message import EmailMessage
from email.utils import formataddr
import os
import random
from pathlib import Path

from flask import Blueprint, render_template, request, session
from dotenv import dotenv_values

from config import (
    CONTACT_TO_EMAIL,
)

contact_bp = Blueprint("contact", __name__)

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_CONTACT_CAPTCHA_ANSWER_KEY = "_contact_captcha_answer"
_CONTACT_CAPTCHA_QUESTION_KEY = "_contact_captcha_question"


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


def _new_contact_captcha() -> str:
    left = random.randint(1, 9)
    right = random.randint(1, 9)
    question = f"What is {left} + {right}?"
    session[_CONTACT_CAPTCHA_QUESTION_KEY] = question
    session[_CONTACT_CAPTCHA_ANSWER_KEY] = str(left + right)
    return question


def _current_contact_captcha() -> str:
    question = str(session.get(_CONTACT_CAPTCHA_QUESTION_KEY) or "").strip()
    answer = str(session.get(_CONTACT_CAPTCHA_ANSWER_KEY) or "").strip()
    if not question or not answer:
        return _new_contact_captcha()
    return question


def _verify_contact_captcha(user_input: str) -> bool:
    expected = str(session.get(_CONTACT_CAPTCHA_ANSWER_KEY) or "").strip()
    provided = str(user_input or "").strip()
    if not expected:
        return False
    ok = provided == expected
    if ok:
        session.pop(_CONTACT_CAPTCHA_ANSWER_KEY, None)
        session.pop(_CONTACT_CAPTCHA_QUESTION_KEY, None)
    return ok


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
    captcha_required = _captcha_required()
    captcha_question = _current_contact_captcha() if captcha_required else ""
    status = None
    error = None
    form_data = {
        "name": "",
        "email": user_email_default,
        "topic": "Issue",
        "subject": "",
        "message": "",
        "captcha": "",
    }

    if request.method == "POST":
        form_data["name"] = (request.form.get("name") or "").strip()
        form_data["email"] = (request.form.get("email") or "").strip()
        form_data["topic"] = (request.form.get("topic") or "Issue").strip() or "Issue"
        form_data["subject"] = (request.form.get("subject") or "").strip()
        form_data["message"] = (request.form.get("message") or "").strip()
        form_data["captcha"] = (request.form.get("captcha_answer") or "").strip()

        if not form_data["name"]:
            error = "Please enter your name."
        elif not form_data["email"] or "@" not in form_data["email"]:
            error = "Please enter a valid email address."
        elif not form_data["subject"]:
            error = "Please enter a subject."
        elif not form_data["message"]:
            error = "Please enter your message."
        elif captcha_required and not _verify_contact_captcha(form_data["captcha"]):
            error = "Captcha validation failed. Please try again."
            form_data["captcha"] = ""
            captcha_question = _new_contact_captcha()
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
                form_data["captcha"] = ""
                if captcha_required:
                    captcha_question = _new_contact_captcha()
            except Exception as exc:
                error = f"Failed to send message: {exc}"

    return render_template(
        "contact.html",
        status=status,
        error=error,
        form_data=form_data,
        captcha_required=captcha_required,
        captcha_question=captcha_question,
    )
