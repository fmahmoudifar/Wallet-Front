from flask import Blueprint, redirect, url_for, session, request
from authlib.integrations.flask_client import OAuth
from  config import AUTHORITY, SERVER_METADATA_URL, URL
import urllib.parse
import os

auth_bp = Blueprint('auth', __name__)

oauth = OAuth()
oauth.register(
    name='oidc',
    client_id=os.getenv('CLIENT_ID'),
    client_secret=os.getenv('CLIENT_SECRET'),
    # server_metadata_url='https://eu-north-1vgqk3w4tz.auth.eu-north-1.amazoncognito.com/.well-known/openid-configuration',
    server_metadata_url="https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_vGqk3w4TZ/.well-known/openid-configuration",
    client_kwargs={'scope': 'email openid phone'}
)

# === Routes ===
@auth_bp.record_once
def on_load(state):
    """ Initializes the OAuth app when the blueprint is loaded. """
    oauth.init_app(state.app)


@auth_bp.route('/login')
def login():
    # Alternate option to redirect to /authorize
    redirect_uri = URL.rstrip('/') + url_for('auth.auth_callback', _external=False)
    # redirect_uri = 'http://localhost:5000/callback'
    print(f'Redirect URI: {redirect_uri}')
    return oauth.oidc.authorize_redirect(redirect_uri)
    #return oauth.oidc.authorize_redirect(URL)
 

@auth_bp.route('/callback')
def auth_callback():
    token = oauth.oidc.authorize_access_token()

    user_info = oauth.oidc.userinfo()  # Secure and nonce-free

    session['user'] = user_info
    return redirect(url_for('home.users'))

import urllib.parse
from config import URL  

@auth_bp.route('/logout')
def logout():
    session.clear()

    return_to = URL.rstrip('/')
    logout_uri_param = urllib.parse.quote_plus(return_to)

    client_id = os.getenv('CLIENT_ID')
    cognito_domain = "eu-north-1vgqk3w4tz.auth.eu-north-1.amazoncognito.com"
    cognito_logout = f"https://{cognito_domain}/logout?client_id={client_id}&logout_uri={logout_uri_param}"

    print(f"Cognito logout URL: {cognito_logout}")

    return redirect(cognito_logout)
