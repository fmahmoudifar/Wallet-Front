# from flask import Blueprint, render_template, request, redirect, url_for, session

# auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# @auth_bp.route("/login", methods=["GET", "POST"])
# def login():
#     if request.method == "POST":
#         username = request.form.get("username")
#         password = request.form.get("password")
#         # Here you should validate credentials with AWS Lambda API
#         if username == "admin" and password == "password":  # Replace with actual authentication logic
#             session["user"] = username
#             return redirect(url_for("login.login"))  # Redirect to wallet page after login
#         return "Invalid credentials", 401
#     return render_template("login.html")

# @auth_bp.route("/logout")
# def logout():
#     session.pop("user", None)
#     return redirect(url_for("auth.login"))


# from flask import Blueprint, render_template, request, redirect, url_for, jsonify, session, Flask

# from config import API_URL, aws_auth

# from authlib.integrations.flask_client import OAuth
# import os


# auth_bp = Blueprint('auth', __name__)

# @crypto_bp.route('/auth', methods=['GET'])
# def crypto_page():
    

# app = Flask(__name__)
# app.secret_key = os.urandom(24)  # Use a secure random key in production
# oauth = OAuth(app)

# oauth.register(
#   name='oidc',
#   authority='https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_vGqk3w4TZ',
#   client_id='38f8a1hg3jr1r4rhm8pq2vkg6n',
#   client_secret='<client secret>',
#   server_metadata_url='https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_vGqk3w4TZ/.well-known/openid-configuration',
#   client_kwargs={'scope': 'phone openid email'}
# )

# @app.route('/')
# def index():
#     user = session.get('user')
#     if user:
#         return  f'Hello, {user["email"]}. <a href="/logout">Logout</a>'
#     else:
#         return f'Welcome! Please <a href="/login">Login</a>.'
    

# @app.route('/login')
# def login():
#     # Alternate option to redirect to /authorize
#     # redirect_uri = url_for('authorize', _external=True)
#     # return oauth.oidc.authorize_redirect(redirect_uri)
#     return oauth.oidc.authorize_redirect('https://127.0.0.1:5000')

# @app.route('/authorize')
# def authorize():
#     token = oauth.oidc.authorize_access_token()
#     user = token['userinfo']
#     session['user'] = user
#     return redirect(url_for('index'))

# @app.route('/logout')
# def logout():
#     session.pop('user', None)
#     return redirect(url_for('index'))

# if __name__ == '__main__':
#     app.run(debug=True)
    


from flask import Blueprint, session, redirect, url_for
from authlib.integrations.flask_client import OAuth
import os

# Initialize Blueprint for authentication
auth_bp = Blueprint('auth', __name__)

oauth = OAuth()
oauth.register(
    name='oidc',
    authority='https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_vGqk3w4TZ',
    client_id='38f8a1hg3jr1r4rhm8pq2vkg6n',
    client_secret='<client secret>',
    server_metadata_url='https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_vGqk3w4TZ/.well-known/openid-configuration',
    client_kwargs={'scope': 'phone openid email'}
)

@auth_bp.route('/login')
def login():
    """Redirects user to Cognito for authentication."""
    return oauth.oidc.authorize_redirect(url_for('auth.authorize', _external=True))

@auth_bp.route('/authorize')
def authorize():
    """Handles the callback from Cognito after login."""
    token = oauth.oidc.authorize_access_token()
    user = token.get('userinfo', {})
    session['user'] = user
    session['user_id'] = user.get('sub')  # Store user ID for other operations
    return redirect(url_for('crypto.crypto_page'))  # Redirect to crypto page after login

@auth_bp.route('/logout')
def logout():
    """Logs out the user and clears the session."""
    session.pop('user', None)
    session.pop('user_id', None)
    return redirect(url_for('auth.login'))  # Redirect to login after logout
