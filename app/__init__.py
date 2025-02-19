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
from app.routes.wallet import wallet_bp

def create_app():
    app = Flask(__name__)
    app.register_blueprint(wallet_bp)
    return app