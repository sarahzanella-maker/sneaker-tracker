import os
import json
from datetime import datetime
import requests
import gspread
from google.oauth2.service_account import Credentials

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})

def connect_sheet():
    credentials_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
    client = gspread.authorize(credentials)
    return client.open_by_key(GOOGLE_SHEET_ID)

def main():
    sheet = connect_sheet()
    results = sheet.worksheet("Results")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results.append_row([
        now,
        "TEST",
        "IQ7604-101",
        "EU 36.5 / US 4.5Y",
        "GS/Adult",
        "",
        "",
        "",
        "Google Sheets connection OK",
        "",
        ""
    ])

    send_telegram("✅ Google Sheets collegato correttamente.\nHo scritto una riga di test nel foglio Results.")

if __name__ == "__main__":
    main()
