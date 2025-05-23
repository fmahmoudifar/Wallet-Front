# from flask import Flask, session
# from config import Config

# def create_app():
#     app = Flask(__name__)
#     app.config.from_object("config.Config")
    
#     from .routes.wallet import wallet_bp
#     from .routes.transactions import transactions_bp
#     from .routes.users import users_bp
#     from .routes.auth import auth_bp
    
#     app.register_blueprint(wallet_bp)
#     app.register_blueprint(transactions_bp)
#     app.register_blueprint(users_bp)
#     app.register_blueprint(auth_bp)
    
#     app.secret_key = Config.SECRET_KEY
    
#     return app


from flask import Flask
import os
from app.routes.home import home_bp
from app.routes.wallet import wallet_bp
from app.routes.transaction import transaction_bp
from app.routes.crypto import crypto_bp
from app.routes.stock import stock_bp
from app.routes.account import account_bp
from app.routes.auth import auth_bp
# from app.routes.layout import layout_bp

FLASK_SECRET_KEY = os.urandom(24)

def create_app():
    app = Flask(__name__)
    # app.config['SECRET_KEY'] = os.getenv('CLIENT_SECRET', 'your_default_secret_key_here')
    app.config['SECRET_KEY'] = os.urandom(24)
    app.register_blueprint(home_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(transaction_bp)
    app.register_blueprint(crypto_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(auth_bp)
    # app.register_blueprint(layout_bp)
    return app