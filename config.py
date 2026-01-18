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

# Stock data provider:
# - yahoo (default): Yahoo Finance only
# - auto: try Alpha Vantage, fall back to Yahoo Finance
# - alphavantage: Alpha Vantage only
# - yahoo: Yahoo Finance only
STOCK_DATA_PROVIDER = (os.getenv("STOCK_DATA_PROVIDER") or "yahoo").strip().lower()

AUTHORITY  = "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_vGqk3w4TZ"
SERVER_METADATA_URL = "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_vGqk3w4TZ/.well-known/openid-configuration"
URL = "https://jubilant-meme-jgxqwvqxpjh9wr-5000.app.github.dev"
