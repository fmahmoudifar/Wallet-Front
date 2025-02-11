from flask import Flask, render_template, request, redirect, url_for, session
import boto3
import requests
from requests_aws4auth import AWS4Auth
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)

AWS_ACCESS_KEY = os.getenv("ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("SECRET_KEY")
AWS_REGION = "eu-north-1"
AWS_SERVICE = "execute-api" 

# API Gateway details
API_URL = "https://e31gpskeu0.execute-api.eu-north-1.amazonaws.com/PROD"

# AWS API Gateway Base URL (Replace with your actual URL)
# API_URL = "https://your-api-id.execute-api.region.amazonaws.com/prod"

# Home route - Fetch and display data
# AWS Signature v4 Authentication
aws_auth = AWS4Auth(AWS_ACCESS_KEY, AWS_SECRET_KEY, AWS_REGION, AWS_SERVICE)

# Home route - Fetch and display data
@app.route('/wallets')
def index():
    response = requests.get(f"{API_URL}/wallets", auth=aws_auth)
    items = response.json() if response.status_code == 200 else []
    return render_template("index.html", items=items)

# Create new item
@app.route('/wallet', methods=['POST'])
def create():
    item_name = request.form['name']
    data = {"id": "123", "name": item_name}  # Replace "123" with a dynamic ID
    requests.post(f"{API_URL}/items", json=data)
    return redirect(url_for('index'))

# Update an item
@app.route('/update/<item_id>', methods=['POST'])
def update(item_id):
    item_name = request.form['name']
    data = {"name": item_name}
    requests.put(f"{API_URL}/items/{item_id}", json=data)
    return redirect(url_for('index'))

# Delete an item
@app.route('/delete/<item_id>')
def delete(item_id):
    requests.delete(f"{API_URL}/items/{item_id}")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
