
from flask import Flask
import os
from app.services.authz import is_admin_user
from app.routes.home import home_bp
from app.routes.wallet import wallet_bp
from app.routes.fiat import fiat_bp
from app.routes.crypto import crypto_bp
from app.routes.stock import stock_bp
from app.routes.settings import settings_bp
from app.routes.loans import loans_bp
from app.routes.auth import auth_bp
from app.routes.admin_tools import admin_tools_bp

def create_app():
    app = Flask(__name__)

    # Use a stable secret key when provided (prevents session/nonce loss during OIDC redirects).
    env_secret = (
        os.getenv('FLASK_SECRET_KEY')
        or os.getenv('APP_SECRET_KEY')
    )
    app.config['SECRET_KEY'] = env_secret if env_secret else os.urandom(24)

    @app.context_processor
    def _inject_admin_tools_config():
        return {
            'is_admin': is_admin_user(),
        }

    app.register_blueprint(home_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(fiat_bp)
    app.register_blueprint(crypto_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(loans_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_tools_bp)
    return app