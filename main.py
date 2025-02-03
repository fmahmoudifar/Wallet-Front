from flask import Flask, render_template, request, jsonify
import requests
import os
from botocore.awsrequest import AWSRequest
from botocore.auth import SigV4Auth
from botocore.credentials import Credentials
from dotenv import load_dotenv

load_dotenv()


app = Flask(__name__)
API_BASE_URL = "https://your-api-gateway-url"  # Replace with actual API Gateway URL

# AWS Credentials
ACCESS_KEY = os.getenv("ACCESS_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
REGION = "eu-north-1"

# API Gateway details
INVOKE_URL = "https://e31gpskeu0.execute-api.eu-north-1.amazonaws.com/PROD"
API_ROOT = "/health"

url = "https://e31gpskeu0.execute-api.eu-north-1.amazonaws.com/PROD"

@app.route('/')
def home():
    return render_template('index.html')

def make_aws_request():
    """Signs and sends a request to AWS API Gateway."""
    url = INVOKE_URL + API_ROOT
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



@app.route('/wallet', methods=['GET'])
def get_wallet():
    wallet_name = request.args.get('walletName')
    username = request.args.get('username')
    if not wallet_name or not username:
        return jsonify({"Message": "walletName and username are required"}), 400
    
    response = requests.get(f"{API_BASE_URL}/wallet", params={'walletName': wallet_name, 'username': username})
    return jsonify(response.json())

@app.route('/wallets', methods=['GET'])
def get_wallets():
    response = requests.get(f"{API_BASE_URL}/wallets")
    return jsonify(response.json())

@app.route('/wallet', methods=['POST'])
def create_wallet():
    data = request.json
    response = requests.post(f"{API_BASE_URL}/wallet", json=data)
    return jsonify(response.json())

@app.route('/wallet', methods=['PATCH'])
def update_wallet():
    data = request.json
    response = requests.patch(f"{API_BASE_URL}/wallet", json=data)
    return jsonify(response.json())

@app.route('/wallet', methods=['DELETE'])
def delete_wallet():
    data = request.json
    response = requests.delete(f"{API_BASE_URL}/wallet", json=data)
    return jsonify(response.json())

if __name__ == '__main__':
    app.run(debug=True)
