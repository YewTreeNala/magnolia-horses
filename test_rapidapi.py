import requests

headers = {
    "X-RapidAPI-Key":  "YOUR_RAPIDAPI_KEY",
    "X-RapidAPI-Host": "horse-racing.p.rapidapi.com"
}

base = "https://horse-racing.p.rapidapi.com"

r = requests.get(base + "/racecards", headers=headers)
print("Status:", r.status_code)
print("Type:", type(r.json()))
print("Raw:", r.text[:500])