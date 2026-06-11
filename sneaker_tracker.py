import os
import json
import re
from datetime import datetime
from urllib.parse import urlparse

import requests
import gspread
from google.oauth2.service_account import Credentials

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

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


def domain_from_url(url):
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def parse_price(value):
    if value is None:
        return None
    text = str(value)
    match = re.search(r"(\d+[.,]?\d*)", text.replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def is_excluded(text, exclude_sku):
    text = text.lower()
    blocked = [
        exclude_sku.lower(),
        " preschool",
        " ps ",
        " toddler",
        " td ",
        " infant",
    ]
    return any(b in text for b in blocked if b.strip())


def serpapi_shopping_search(query, max_results):
    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": SERPAPI_KEY,
        "gl": "it",
        "hl": "it",
        "num": max_results,
    }
    response = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("shopping_results", [])


def serpapi_google_search(query, max_results):
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERPAPI_KEY,
        "gl": "it",
        "hl": "it",
        "num": max_results,
    }
    response = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("organic_results", [])


def main():
    sheet = connect_sheet()
    sources_ws = sheet.worksheet(SOURCE_SHEET_NAME)
    settings_ws = sheet.worksheet(SETTINGS_SHEET_NAME)
    results_ws = sheet.worksheet(RESULTS_SHEET_NAME)

    settings = read_settings(settings_ws)
    source_rows = sources_ws.get_all_records()

    sku = settings.get("SKU", "IQ7604-101")
    exclude_sku = settings.get("Exclude SKU", "IQ7605-101")
    search_term = settings.get("Search Term", "Travis Scott Tropical Pink")
    alert_2 = float(settings.get("Alert 2", "400"))
    max_results = int(float(settings.get("Max Results per Site", "20")))

    allowed_domains = []
    for row in source_rows:
        active = normalize(row.get("ATTIVO", "")).upper()
        enabled = normalize(row.get("Enabled", "")).upper()
        url = normalize(row.get("URL", ""))

        if active in ["YES", "SI", "SÌ", "TRUE", "1"] and enabled in ["YES", "SI", "SÌ", "TRUE", "1"]:
            domain = domain_from_url(url)
            if domain:
                allowed_domains.append(domain)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    queries = [
        f'"{sku}"',
        f'"{search_term}" "4Y" OR "4.5Y" OR "36" OR "36.5"',
    ]

    found_rows = []
    alert_rows = []

    for query in queries:
        try:
            shopping_results = serpapi_shopping_search(query, max_results)
        except Exception as e:
            shopping_results = []
            results_ws.append_row([
                now, "SYSTEM", sku, "", "", "", "", "",
                f"Shopping search error: {e}", "", "", "SerpAPI", query
            ])

        for item in shopping_results:
            title = item.get("title", "")
            source = item.get("source", "")
            link = item.get("link") or item.get("product_link") or ""
            price_text = item.get("price", "")
            price = item.get("extracted_price") or parse_price(price_text)

            full_text = f"{title} {source} {link}"

            if is_excluded(full_text, exclude_sku):
                continue

            if sku.lower() not in full_text.lower() and search_term.lower() not in full_text.lower():
                continue

            site = source or domain_from_url(link) or "Google Shopping"
            availability = "Possible match"

            row = [
                now,
                site,
                sku,
                "36 / 36.5 / 4Y / 4.5Y / 4 / 4.5",
                "GS/Adult",
                price if price is not None else "",
                "",
                price if price is not None else "",
                availability,
                "",
                link,
                "Google Shopping",
                title,
            ]

            found_rows.append(row)

            if price is not None and price <= alert_2:
                alert_rows.append(row)

    try:
        organic_results = serpapi_google_search(f'"{sku}" OR "{search_term}"', max_results)
    except Exception as e:
        organic_results = []
        results_ws.append_row([
            now, "SYSTEM", sku, "", "", "", "", "",
            f"Google search error: {e}", "", "", "SerpAPI", ""
        ])

        for item in organic_results:
        title = item.get("title", "")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        domain = domain_from_url(link)
        full_text = f"{title} {snippet} {link}"

        if is_excluded(full_text, exclude_sku):
            continue

        if allowed_domains and domain not in allowed_domains:
            continue

        row = [
            now,
            domain,
            sku,
            "To verify",
            "To verify",
            "",
            "",
            "",
            "Found page - price to verify",
            "",
            link,
            "Google Search",
            title,
        ]

        found_rows.append(row)

    if found_rows:
        print(f"FOUND_ROWS = {len(found_rows)}")
        print("WRITING TO GOOGLE SHEETS")

        clean_rows = []
        for row in found_rows:
            clean_rows.append([str(cell) if cell is not None else "" for cell in row])

        results_ws.append_rows(
            clean_rows,
            value_input_option="USER_ENTERED"
        )

    summary = (
        "🔍 Sneaker Tracker V5\n\n"
        f"Risultati trovati: {len(found_rows)}\n"
        f"Possibili alert ≤ {alert_2} €: {len(alert_rows)}"
    )

    if alert_rows:
        first_alerts = alert_rows[:5]
        details = "\n\n".join([
            f"🚨 {r[1]}\nPrezzo: {r[5]} €\nLink: {r[10]}"
            for r in first_alerts
        ])
        send_telegram(summary + "\n\n" + details)
    else:
        send_telegram(summary + "\n\nNessun prezzo sotto soglia trovato per ora.")


if __name__ == "__main__":
    main()
