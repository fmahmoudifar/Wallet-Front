import os

from dotenv import load_dotenv
from requests_aws4auth import AWS4Auth

load_dotenv()

AWS_ACCESS_KEY = os.getenv("ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("SECRET_KEY")
AWS_REGION = "eu-north-1"
AWS_SERVICE = "execute-api"
API_URL = "https://e31gpskeu0.execute-api.eu-north-1.amazonaws.com/PROD"
aws_auth = AWS4Auth(AWS_ACCESS_KEY, AWS_SECRET_KEY, AWS_REGION, AWS_SERVICE)

# Alpha Vantage (stocks)
# Preferred env var: ALPHA_VANTAGE_API_KEY
# Backward compatible env var: AV_API_KEY
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY") or os.getenv("AV_API_KEY")

# CoinMarketCap API (crypto)
CMC_API_KEY = os.getenv("CMC_API_KEY")

# Stock data provider:
# - yahoo (default): Yahoo Finance only
# - auto: try Alpha Vantage, fall back to Yahoo Finance
# - alphavantage: Alpha Vantage only
# - yahoo: Yahoo Finance only
STOCK_DATA_PROVIDER = (os.getenv("STOCK_DATA_PROVIDER") or "yahoo").strip().lower()

AUTHORITY = "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_dBBGtdFWv"
SERVER_METADATA_URL = (
    "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_dBBGtdFWv/.well-known/openid-configuration"
)
URL = (
    os.getenv("PUBLIC_URL")
    or os.getenv("APP_URL")
    or os.getenv("BASE_URL")
    or "https://www.walletsportfolio.com"
).strip()

# Contact / support email settings
CONTACT_TO_EMAIL = (os.getenv("CONTACT_TO_EMAIL") or "info@walletsportfolio.com").strip()
SMTP_HOST = (os.getenv("SMTP_HOST") or "").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT") or "587").strip() or "587")
SMTP_USERNAME = (os.getenv("SMTP_USERNAME") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
SMTP_USE_TLS = (os.getenv("SMTP_USE_TLS") or "1").strip().lower() in {"1", "true", "yes", "y", "on"}
SMTP_USE_SSL = (os.getenv("SMTP_USE_SSL") or "0").strip().lower() in {"1", "true", "yes", "y", "on"}
MAIL_FROM_EMAIL = (os.getenv("MAIL_FROM_EMAIL") or SMTP_USERNAME or "").strip()
