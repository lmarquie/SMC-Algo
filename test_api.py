import requests
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv('HYPERLIQUID_API_KEY')
print(f"API Key: {api_key[:10]}..." if api_key else "No API key found")

# Test basic API call
url = "https://api.hyperliquid.xyz/info"
payload = {"type": "allMids"}

try:
    response = requests.post(url, json=payload, timeout=10)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
except Exception as e:
    print(f"Error: {e}")