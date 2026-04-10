import requests
import os
from dotenv import load_dotenv

load_dotenv()

user = os.getenv("RACING_API_USER")
key = os.getenv("RACING_API_KEY")

endpoints = [
    "/racecards/free",
    "/racecards/basic",
    "/racecards",
    "/races/free",
    "/races",
    "/horses",
    "/results",
    "/results/free",
    "/courses",
    "/jockeys",
    "/trainers",
]

for ep in endpoints:
    r = requests.get(f"https://api.theracingapi.com/v1{ep}", auth=(user, key))
    print(f"{ep}: {r.status_code} — {r.text[:100]}")