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
SETTINGS_SHEET_NAME = "Settings"
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

def read_settings(settings_ws):
    rows = settings_ws.get_all_records()
    settings = {}
    for row in rows:
        key = normalize(row.get("Parameter", ""))
        value = normalize(row.get("Value", ""))
        if key:
            settings[key] = value
    return settings

def main():
    sheet = connect_sheet()
    sources_ws = sheet.worksheet(SOURCE_SHEET_NAME)
    settings_ws = sheet.worksheet(SETTINGS_SHEET_NAME)
    results_ws = sheet.worksheet(RESULTS_SHEET_NAME)

    settings = read_settings(settings_ws)
    rows = sources_ws.get_all_records()

    active_sources = []
    for row in rows:
        site = normalize(row.get("SITO", ""))
        url = normalize(row.get("URL", ""))
        active = normalize(row.get("ATTIVO", "")).upper()
        enabled = normalize(row.get("Enabled", "")).upper()

        if active in ["YES", "SI", "SÌ", "TRUE", "1"] and enabled in ["YES", "SI", "SÌ", "TRUE", "1"] and url:
            active_sources.append(site)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sku = settings.get("SKU", "")
    exclude_sku = settings.get("Exclude SKU", "")
    sizes = [
        settings.get("Size EU 1", ""),
        settings.get("Size EU 2", ""),
        settings.get("Size US GS 1", ""),
        settings.get("Size US GS 2", ""),
        settings.get("Size US Men 1", ""),
        settings.get("Size US Men 2", ""),
    ]
    sizes = [s for s in sizes if s]

    alert_1 = settings.get("Alert 1", "")
    alert_2 = settings.get("Alert 2", "")

    results_ws.append_row([
        now,
        "SYSTEM",
        sku,
        " / ".join(sizes),
        "",
        "",
        "",
        "",
        f"Config OK - {len(active_sources)} active sources",
        "",
        ""
    ])

    message = (
        "✅ Tracker configurato correttamente.\n\n"
        f"Siti attivi: {len(active_sources)}\n"
        f"SKU: {sku}\n"
        f"Escludi: {exclude_sku}\n"
        f"Taglie: {' / '.join(sizes)}\n"
        f"Alert: {alert_1} € / {alert_2} €"
    )

    send_telegram(message)

if __name__ == "__main__":
    main()
