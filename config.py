import os

class Config:
    SECRET_KEY = os.urandom(24)  # Change this to a secure key
    AWS_API_URL = "https://your-api-endpoint.amazonaws.com"