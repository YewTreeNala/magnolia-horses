import requests, os
from dotenv import load_dotenv
load_dotenv()

r = requests.get(
    "https://api.theracingapi.com/v1/entries",
    auth=(os.getenv("RACING_API_USER"), os.getenv("RACING_API_KEY"))
)
print(r.status_code)
print(r.text[:300])