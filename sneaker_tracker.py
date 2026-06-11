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

SOURCE_SHEET_NAME = "Sneaker Sources"
RESULTS_SHEET_NAME = "Results"

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

def normalize(value):
    return str(value).strip()

def main():
    sheet = connect_sheet()
    sources_ws = sheet.worksheet(SOURCE_SHEET_NAME)
    results_ws = sheet.worksheet(RESULTS_SHEET_NAME)

    rows = sources_ws.get_all_records()

    active_sources = []
    for row in rows:
        site = normalize(row.get("SITO", ""))
        url = normalize(row.get("URL", ""))
        active = normalize(row.get("ATTIVO", "")).upper()

        if active in ["YES", "SI", "SÌ", "TRUE", "1"] and url:
            active_sources.append({
                "site": site,
                "url": url,
                "reliability": normalize(row.get("Reliability", "")),
                "notes": normalize(row.get("Notes", "")),
            })

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results_ws.append_row([
        now,
        "SYSTEM",
        "IQ7604-101",
        "",
        "",
        "",
        "",
        "",
        f"Found {len(active_sources)} active sources",
        "",
        ""
    ])

    source_list = "\n".join([f"- {s['site']}" for s in active_sources[:20]])

    message = (
        "✅ Sneaker Sources letto correttamente.\n\n"
        f"Siti attivi trovati: {len(active_sources)}\n\n"
        f"{source_list}"
    )

    send_telegram(message)

if __name__ == "__main__":
    main()
