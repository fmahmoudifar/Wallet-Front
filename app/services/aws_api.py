# import requests
# from config import Config

# def call_lambda(endpoint, payload={}):
#     url = f"{Config.AWS_API_URL}/{endpoint}"
#     response = requests.post(url, json=payload)
#     return response.json()
