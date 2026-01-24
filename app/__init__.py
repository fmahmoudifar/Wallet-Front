
from flask import Flask
import os
from app.routes.home import home_bp
from app.routes.wallet import wallet_bp
from app.routes.fiat import fiat_bp
from app.routes.crypto import crypto_bp
from app.routes.stock import stock_bp
from app.routes.settings import settings_bp
from app.routes.loans import loans_bp
from app.routes.auth import auth_bp

FLASK_SECRET_KEY = os.urandom(24)

def create_app():
    app = Flask(__name__)
    # app.config['SECRET_KEY'] = os.getenv('CLIENT_SECRET', 'your_default_secret_key_here')
    app.config['SECRET_KEY'] = os.urandom(24)
    app.register_blueprint(home_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(fiat_bp)
    app.register_blueprint(crypto_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(loans_bp)
    app.register_blueprint(auth_bp)
    return app