from flask import Flask, jsonify
import requests
from botocore.awsrequest import AWSRequest
from botocore.auth import SigV4Auth
from botocore.credentials import Credentials
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

app = Flask(__name__)

# AWS Credentials
ACCESS_KEY = os.getenv("ACCESS_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
REGION = "eu-north-1"

# API Gateway details
INVOKE_URL = "https://e31gpskeu0.execute-api.eu-north-1.amazonaws.com/PROD"
API_ROOT = "/health"


def make_aws_request(API_ROOT):
    """Signs and sends a request to AWS API Gateway."""
    url = INVOKE_URL + API_ROOT
    print(url)
    request = AWSRequest(method="GET", url=url, data="")  # Empty payload for GET
    credentials = Credentials(ACCESS_KEY, SECRET_KEY)
    SigV4Auth(credentials, "execute-api", REGION).add_auth(request)

    # Send the signed request
    response = requests.request(
        method=request.method,
        url=request.url,
        headers=dict(request.headers),
        data=request.body,
    )
    return response


@app.route("/health", methods=["GET"])
def health_check():
    """Flask route to trigger AWS API request."""
    response = make_aws_request()
    return jsonify({
        "status_code": response.status_code,
        "response": response.text
    }), response.status_code

@app.route('/wallets', methods=['GET'])
def get_wallets():
    response = make_aws_request('/wallets')
    return jsonify({
        "status_code": response.status_code,
        "response": response.text
    }), response.status_code

if __name__ == "__main__":
    app.run(debug=True)
