from flask import Flask, jsonify, request
import requests
from botocore.awsrequest import AWSRequest
from botocore.auth import SigV4Auth
from botocore.credentials import Credentials
from dotenv import load_dotenv
import os
import json

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
data = {
    "walletName": "Cash",
    "username": "fmahmoudifar@gmail.com",
    "walletType": "cash",
    "accountNumber" : "",
    "currency": "EUR",
    "note" : ""
    }


# def make_aws_request(API_ROOT):
#     """Signs and sends a request to AWS API Gateway."""
#     url = INVOKE_URL + API_ROOT
#     print(url)
#     request = AWSRequest(method="GET", url=url, data="")  # Empty payload for GET
#     credentials = Credentials(ACCESS_KEY, SECRET_KEY)
#     SigV4Auth(credentials, "execute-api", REGION).add_auth(request)

#     # Send the signed request
#     response = requests.request(
#         method=request.method,
#         url=request.url,
#         headers=dict(request.headers),
#         data=request.body,
#     )
#     return response

def make_aws_request(API_ROOT, method="GET", data=None):
    """Signs and sends a request to AWS API Gateway."""
    url = INVOKE_URL + API_ROOT
    print(f"Request URL: {url}, Method: {method}")

    request_data = json.dumps(data) if data else ""


    request = AWSRequest(method=method, url=url, data=data if data else "")
    credentials = Credentials(ACCESS_KEY, SECRET_KEY)
    SigV4Auth(credentials, "execute-api", REGION).add_auth(request)

    # Send the signed request
    response = requests.request(
        method=request.method,
        url=request.url,
        headers=dict(request.headers),
        json=data if data else None,  # Use JSON payload for POST
    )
    return response


@app.route("/health", methods=["GET"])
def health_check():
    response = make_aws_request("/health")
    return jsonify({
        "status_code": response.status_code,
        "response": response.text
    }), response.status_code

# @app.route('/wallets', methods=['GET'])
# def get_wallets():
#     response = make_aws_request('/wallets')
#     return jsonify({
#         "status_code": response.status_code,
#         "response": response.text
#     }), response.status_code


@app.route('/wallets', methods=['GET'])
def get_wallets():
    """Fetch wallet data from AWS API."""
    response = make_aws_request('/wallets')

    try:
        # Convert response text to actual JSON
        response_json = response.json()  
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON response from AWS"}), 500

    return jsonify({
        "status_code": response.status_code,
        "response": response_json  # Now it's proper JSON, not a string
    }), response.status_code


# @app.route('/wallet', methods=['POST'])
# def create_wallet():
#     data = request.json  # Get JSON payload from the request
#     response = make_aws_request('/wallet', method="POST", data=data)  # Pass method and data
#     return jsonify({
#         "status_code": response.status_code,
#         "response": response.json()
#     }), response.status_code

# @app.route('/wallet', methods=['POST'])
# def create_wallet():
#     """Flask route to create a wallet using a POST request."""
#     data = request.json  # Get JSON payload from the request

#     if not data:  # Validate input
#         return jsonify({"error": "Request body is missing"}), 400

#     return jsonify({
#         "message": "Wallet created successfully",
#         "data": data  # Echo back received data
#     }), 201

@app.route('/wallet', methods=['GET','POST'])
def create_wallet():
    """Flask route to create a wallet using a POST request."""

    # Hardcoded JSON request body
    data = {
        "walletName": "Cash1",
        "username": "fmahmoudifar@gmail.com",
        "walletType": "cash",
        "accountNumber": "",
        "currency": "EUR",
        "note": ""
    }
    response = requests.post(f"{INVOKE_URL}/wallet", json=data)
    return jsonify(response.json())

    # return jsonify({
    #     "message": "Wallet created successfully",
    #     "data": data  # Echo back the hardcoded data
    # }), 201

@app.route('/wallet', methods=['GET','DELETE'])
def delete_wallet_route():
    """Delete a wallet by walletName and username."""
    # data = request.json  # Expecting JSON input

    data = {
        "walletName": "Cash",
        "username": "fmahmoudifar@gmail.com"}


    # Validate required fields
    wallet_name = data.get("walletName")
    username = data.get("username")

    if not wallet_name or not username:
        return jsonify({"error": "Missing walletName or username"}), 400

    # Call the delete_wallet function
    # response = delete_wallet(wallet_name, username)

    return jsonify(response), response["statusCode"]

if __name__ == "__main__":
    app.run(debug=True)
