import os
import secrets
import uuid

from flask import Blueprint, abort, redirect, render_template, request, session, url_for


dev_auth_bp = Blueprint("dev_auth", __name__)


def _strip_quotes(val: str) -> str:
    s = (val or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1].strip()
    return s


def _truthy(val: str | None) -> bool:
    s = _strip_quotes(str(val or "")).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _is_codespaces() -> bool:
    if _truthy(os.getenv("CODESPACES")):
        return True
    if os.getenv("CODESPACE_NAME"):
        return True
    if os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"):
        return True
    if _truthy(os.getenv("LOCAL_DEV") or ""):
        return True

    try:
        host = (request.host or "").lower().split(":")[0]
        if host.endswith(".app.github.dev") or host.endswith(".github.dev"):
            return True
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
    except Exception:
        pass

    return False


def _dev_login_creds() -> tuple[str, str]:
    username = (
        os.getenv("DEV_LOGIN_USERNAME")
        or os.getenv("DUMMY_USERNAME")
        or os.getenv("dummy_username")
        or os.getenv("DEV_DUMMY_USERNAME")
        or ""
    )
    password = (
        os.getenv("DEV_LOGIN_PASSWORD")
        or os.getenv("DUMMY_PASSWORD")
        or os.getenv("dummy_password")
        or os.getenv("DEV_DUMMY_PASSWORD")
        or ""
    )
    return _strip_quotes(username), _strip_quotes(password)


def _dev_user_id(fallback_username: str) -> str:
    # Allow either explicit DEV_USER_ID or a simpler user_id key in .env.
    raw = (
        os.getenv("DEV_USER_ID")
        or os.getenv("DEV_DUMMY_USER_ID")
        or os.getenv("USER_ID")
        or os.getenv("user_id")
        or ""
    )
    user_id = _strip_quotes(raw).strip()
    return user_id or (fallback_username or "").strip()


def _dev_login_enabled() -> bool:
    if not _is_codespaces():
        return False
    if _truthy(os.getenv("DEV_LOGIN_DISABLED")):
        return False
    u, p = _dev_login_creds()
    return bool(u and p)


@dev_auth_bp.route("/dev-login", methods=["GET", "POST"])
def dev_login():
    if not _dev_login_enabled():
        abort(404)

    # Already logged in? Send them through.
    if isinstance(session.get("user"), dict) and session.get("user"):
        return redirect(url_for("home.users"))

    if request.method == "GET":
        return render_template("dev_login.html", error=None, username="")

    expected_username, expected_password = _dev_login_creds()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    ok = secrets.compare_digest(username, expected_username) and secrets.compare_digest(password, expected_password)
    if not ok:
        return render_template(
            "dev_login.html",
            error="Invalid username or password.",
            username=username,
        )

    # Use a stable app-level userId for dev (so saved data is consistent).
    effective_user_id = _dev_user_id(username)
    dummy_email = _strip_quotes(os.getenv("DEV_DUMMY_EMAIL") or f"{username}@dev.local")
    dummy_sub = _strip_quotes(os.getenv("DEV_DUMMY_SUB") or f"dev-{uuid.uuid4()}")

    raw_groups = _strip_quotes(os.getenv("DEV_DUMMY_GROUPS") or "")
    groups = [g.strip() for g in raw_groups.split(",") if g.strip()] if raw_groups else []

    session["user"] = {
        "username": effective_user_id,
        "email": dummy_email,
        "sub": dummy_sub,
        "dev_login": username,
    }
    session["id_token_claims"] = {
        "sub": dummy_sub,
        "cognito:groups": groups,
    }
    session["cognito_groups"] = groups

    next_url = (request.args.get("next") or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)

    return redirect(url_for("home.users"))
