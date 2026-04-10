import requests
import os
from dotenv import load_dotenv

load_dotenv()

user = os.getenv("RACING_API_USER")
key = os.getenv("RACING_API_KEY")

print(f"Username: '{user}'")
print(f"Key: '{key}'")

response = requests.get(
    "https://api.theracingapi.com/v1/racecards/free",
    auth=(user, key)
)

print(f"Status code: {response.status_code}")
print(f"Response: {response.text[:500]}")