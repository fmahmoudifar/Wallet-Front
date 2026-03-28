import os

from flask import Flask, redirect, request, session, url_for

from app.routes.admin_tools import admin_tools_bp
from app.routes.auth import auth_bp
from app.routes.crypto import crypto_bp
from app.routes.data_io import data_io_bp
from app.routes.fiat import fiat_bp
from app.routes.home import home_bp
from app.routes.loans import loans_bp
from app.routes.settings import settings_bp
from app.routes.stock import stock_bp
from app.routes.wallet import wallet_bp
from app.routes.dev_auth import dev_auth_bp
from app.services.authz import is_admin_user


def create_app():
    app = Flask(__name__)

    def _strip_quotes(val: str) -> str:
        s = (val or "").strip()
        if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
            return s[1:-1].strip()
        return s

    def _truthy_env(val: str | None) -> bool:
        s = _strip_quotes(str(val or "")).strip().lower()
        return s in {"1", "true", "yes", "y", "on"}

    def _is_codespaces_env() -> bool:
        if _truthy_env(os.getenv("CODESPACES")):
            return True
        if os.getenv("CODESPACE_NAME"):
            return True
        if os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"):
            return True
        # Also treat local development as a dev environment
        if _truthy_env(os.getenv("LOCAL_DEV") or ""):
            return True
        return False

    def _dev_login_creds_present_env() -> bool:
        username = os.getenv("DEV_LOGIN_USERNAME") or os.getenv("DUMMY_USERNAME") or os.getenv("dummy_username")
        password = os.getenv("DEV_LOGIN_PASSWORD") or os.getenv("DUMMY_PASSWORD") or os.getenv("dummy_password")
        return bool(_strip_quotes(username or "").strip() and _strip_quotes(password or "").strip())

    # Use a stable secret key when provided (prevents session/nonce loss during OIDC redirects).
    # In Codespaces dev-login mode, default to an ephemeral secret so each server start forces a fresh login.
    # This avoids "sticky" sessions that bypass the dev login page when the app restarts.
    force_dev_login_on_start = _truthy_env(os.getenv("DEV_FORCE_LOGIN_ON_START") or "1")
    if force_dev_login_on_start and _is_codespaces_env() and _dev_login_creds_present_env():
        app.config["SECRET_KEY"] = os.urandom(24)
    else:
        env_secret = os.getenv("FLASK_SECRET_KEY") or os.getenv("APP_SECRET_KEY")
        app.config["SECRET_KEY"] = env_secret if env_secret else os.urandom(24)

    def _is_codespaces() -> bool:
        # GitHub Codespaces typically sets one or more of these env vars.
        if (os.getenv("CODESPACES") or "").strip().lower() not in {"", "0", "false", "no", "off"}:
            return True
        if os.getenv("CODESPACE_NAME"):
            return True
        if os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"):
            return True
        if _truthy_env(os.getenv("LOCAL_DEV") or ""):
            return True

        try:
            host = (request.host or "").lower().split(":")[0]
            # Codespaces hostname or local development
            if host.endswith(".app.github.dev") or host.endswith(".github.dev"):
                return True
            if host in ("localhost", "127.0.0.1", "::1"):
                return True
        except Exception:
            pass

        return False

    @app.before_request
    def _require_dev_login_in_codespaces_when_configured():
        def _dev_login_creds_present() -> bool:
            # Prefer DEV_LOGIN_*; allow fallback to dummy_* since many env files use that.
            username = os.getenv("DEV_LOGIN_USERNAME") or os.getenv("DUMMY_USERNAME") or os.getenv("dummy_username")
            password = os.getenv("DEV_LOGIN_PASSWORD") or os.getenv("DUMMY_PASSWORD") or os.getenv("dummy_password")
            return bool(_strip_quotes(username or "").strip() and _strip_quotes(password or "").strip())

        # Only enforce dev-login in Codespaces, and only when credentials exist.
        if not _is_codespaces() or not _dev_login_creds_present():
            return None

        if request.endpoint == "static":
            return None

        # Avoid redirect loops.
        if (request.path or "") == "/dev-login":
            return None

        # Let logout clear session first.
        if request.endpoint == "auth.logout":
            return None

        # If the user is already logged in (Cognito or dev-login), don't override.
        if isinstance(session.get("user"), dict) and session.get("user"):
            return None

        next_path = request.full_path if request.query_string else request.path
        return redirect(url_for("dev_auth.dev_login", next=next_path))

    @app.context_processor
    def _inject_admin_tools_config():
        return {
            "is_admin": is_admin_user(),
        }

    app.register_blueprint(home_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(fiat_bp)
    app.register_blueprint(crypto_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(loans_bp)
    app.register_blueprint(data_io_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dev_auth_bp)
    app.register_blueprint(admin_tools_bp)
    return app
