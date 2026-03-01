from flask import Blueprint, redirect, url_for, session, request
from authlib.integrations.flask_client import OAuth
from config import SERVER_METADATA_URL, URL
import urllib.parse
import os
import secrets

auth_bp = Blueprint('auth', __name__)

# oauth = OAuth()
# oauth.register(
#     name='oidc',
#     client_id=os.getenv('CLIENT_ID'),
#     client_secret=os.getenv('CLIENT_SECRET'),
#     server_metadata_url="https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_vGqk3w4TZ/.well-known/openid-configuration",
#     client_kwargs={'scope': 'email openid phone'}
# )

oauth = OAuth()
oauth.register(
    name='oidc',
    client_id=os.getenv('CLIENT_ID'),
    client_secret=os.getenv('CLIENT_SECRET'),
    server_metadata_url=SERVER_METADATA_URL,
    client_kwargs={'scope': 'email openid phone'}
)

@auth_bp.record_once
def on_load(state):
    """ Initializes the OAuth app when the blueprint is loaded. """
    oauth.init_app(state.app)

@auth_bp.route('/login')
def login():
    redirect_uri = URL.rstrip('/') + url_for('auth.auth_callback', _external=False)
    # redirect_uri = 'http://localhost:5000/callback'
    print(f'Redirect URI: {redirect_uri}')

    # Authlib requires an explicit nonce for parse_id_token() in newer versions.
    nonce = secrets.token_urlsafe(24)
    session['oidc_nonce'] = nonce
    return oauth.oidc.authorize_redirect(redirect_uri, nonce=nonce)

@auth_bp.route('/callback')
def auth_callback():
    token = oauth.oidc.authorize_access_token()

    try:
        print(f"[auth] token keys={list(token.keys())} has_id_token={'id_token' in token}")
    except Exception:
        pass

    user_info = oauth.oidc.userinfo()
    session['user'] = user_info

    # Persist ID token claims so Cognito group membership (cognito:groups) is available.
    # The UserInfo endpoint typically does NOT include groups.
    try:
        nonce = session.get('oidc_nonce')
        claims = oauth.oidc.parse_id_token(token, nonce=nonce)
        session.pop('oidc_nonce', None)
        if isinstance(claims, dict):
            session['id_token_claims'] = claims
            session['cognito_groups'] = claims.get('cognito:groups')
            # Convenience: surface groups on session['user'] as well.
            if isinstance(session.get('user'), dict) and 'cognito:groups' in claims:
                session['user']['cognito:groups'] = claims.get('cognito:groups')

            try:
                grp = claims.get('cognito:groups')
                sub = claims.get('sub')
                print(f"[auth] sub={sub} cognito:groups={grp}")
            except Exception:
                pass
    except Exception as e:
        # If parsing fails for any reason, keep login working; admin gating will simply be false.
        try:
            print(f"[auth] parse_id_token failed: {type(e).__name__}: {e}")
        except Exception:
            pass
        session.pop('id_token_claims', None)
        session.pop('cognito_groups', None)
        session.pop('oidc_nonce', None)

    return redirect(url_for('home.users'))

@auth_bp.route('/logout')
def logout():
    session.clear()

    return_to = URL.rstrip('/')
    logout_uri_param = urllib.parse.quote_plus(return_to)

    client_id = os.getenv('CLIENT_ID')
    # cognito_domain = "eu-north-1vgqk3w4tz.auth.eu-north-1.amazoncognito.com"
    cognito_domain = "eu-north-1dbbgtdfwv.auth.eu-north-1.amazoncognito.com"
    cognito_logout = f"https://{cognito_domain}/logout?client_id={client_id}&logout_uri={logout_uri_param}"

    print(f"Cognito logout URL: {cognito_logout}")

    return redirect(cognito_logout)
