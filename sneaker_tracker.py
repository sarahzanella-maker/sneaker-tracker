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


BLOCKED_KEYWORDS = [
    "iq7605-101",
    "preschool",
    "(ps)",
    " ps)",
    "(ps ",
    " ps ",
    "toddler",
    " td ",
    "(td)",
    "infant",
    "kids",
    "baby",
    "bambino",
    "bambina",
    "junior",
    "olive",
    "medium olive",
    "black olive",
    "reverse mocha",
    "canary",
    "velvet brown",
    "fragment",
    "phantom",
    "air force",
    "dunk",
    "air max",
    "nocta",
    "glide",
    "flyease",
    "why not",
    "zer0.4",
    "hikvision",
    "registratore",
    "nvr",
    "camera",
    "dispositivo",
    "protezione ip",
    "ds-7604",
]


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=30,
    )


def connect_sheet():
    credentials_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=scopes,
    )

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


def detect_currency_symbol(price_text):
    text = str(price_text)

    if "€" in text or "EUR" in text.upper():
        return "€"

    if "$" in text or "USD" in text.upper():
        return "$"

    if "£" in text or "GBP" in text.upper():
        return "£"

    return "€"


def parse_price(value):
    if value is None:
        return None

    text = str(value)
    text = text.replace(",", ".")

    match = re.search(r"(\d+[.]?\d*)", text)

    if not match:
        return None

    try:
        return float(match.group(1))
    except Exception:
        return None


def format_money(amount, symbol):
    if amount is None:
        return "N/D"

    return f"{symbol}{amount:.2f}"


def title_is_valid(title, sku):
    title_text = f" {str(title).lower()} "

    if any(blocked in title_text for blocked in BLOCKED_KEYWORDS):
        return False

    has_full_sku = sku.lower() in title_text

    has_exact_name = (
        "travis" in title_text
        and "scott" in title_text
        and "tropical" in title_text
        and "pink" in title_text
    )

    return has_full_sku or has_exact_name


def get_shipping_text(item):
    shipping_fields = [
        item.get("shipping"),
        item.get("delivery"),
        item.get("extracted_shipping"),
    ]

    for field in shipping_fields:
        if field:
            return str(field)

    return "N/D"


def parse_shipping(shipping_text):
    if not shipping_text or shipping_text == "N/D":
        return None

    text = str(shipping_text).lower()

    if "free" in text or "gratis" in text or "gratuita" in text:
        return 0.0

    return parse_price(shipping_text)


def serpapi_shopping_search(query, max_results):
    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": SERPAPI_KEY,
        "gl": "it",
        "hl": "it",
        "num": max_results,
    }

    response = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=30,
    )

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

    response = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    return data.get("organic_results", [])


def write_rows_to_results(results_ws, rows):
    if not rows:
        return

    clean_rows = []

    for row in rows:
        clean_rows.append([str(cell) if cell is not None else "" for cell in row])

    existing_rows = len(results_ws.get_all_values())
    start_row = existing_rows + 1
    end_row = start_row + len(clean_rows) - 1

    results_ws.update(
        f"A{start_row}:M{end_row}",
        clean_rows,
        value_input_option="USER_ENTERED",
    )


def main():
    sheet = connect_sheet()

    sources_ws = sheet.worksheet(SOURCE_SHEET_NAME)
    settings_ws = sheet.worksheet(SETTINGS_SHEET_NAME)
    results_ws = sheet.worksheet(RESULTS_SHEET_NAME)

    settings = read_settings(settings_ws)
    source_rows = sources_ws.get_all_records()

    sku = settings.get("SKU", "IQ7604-101")
    search_term = settings.get("Search Term", "Travis Scott Tropical Pink")
    alert_2 = float(settings.get("Alert 2", "400"))
    max_results = int(float(settings.get("Max Results per Site", "20")))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    allowed_domains = []

    for row in source_rows:
        active = normalize(row.get("ATTIVO", "")).upper()
        enabled = normalize(row.get("Enabled", "")).upper()
        url = normalize(row.get("URL", ""))

        if active in ["YES", "SI", "SÌ", "TRUE", "1"] and enabled in ["YES", "SI", "SÌ", "TRUE", "1"]:
            domain = domain_from_url(url)
            if domain:
                allowed_domains.append(domain)

    queries = [
        f'"{sku}" "Jordan"',
        f'"{search_term}"',
        f'"Travis Scott" "Tropical Pink" "Jordan"',
    ]

    found_items = []
    filtered_out = 0

    for query in queries:
        try:
            shopping_results = serpapi_shopping_search(query, max_results)
        except Exception as error:
            shopping_results = []
            found_items.append({
                "sort_total": 999999,
                "row": [
                    now,
                    "SYSTEM",
                    sku,
                    "",
                    "",
                    "",
                    "",
                    "",
                    f"Shopping search error: {error}",
                    "",
                    "",
                    "SerpAPI",
                    query,
                ],
            })

        for item in shopping_results:
            title = item.get("title", "")
            source = item.get("source", "")
            link = item.get("link") or item.get("product_link") or ""
            price_text = item.get("price", "")
            price = item.get("extracted_price") or parse_price(price_text)

            if not title_is_valid(title, sku):
                filtered_out += 1
                continue

            symbol = detect_currency_symbol(price_text)

            shipping_text = get_shipping_text(item)
            shipping_value = parse_shipping(shipping_text)

            total_value = price
            if price is not None and shipping_value is not None:
                total_value = price + shipping_value

            site = source or domain_from_url(link) or "Google Shopping"

            price_display = format_money(price, symbol)
            shipping_display = format_money(shipping_value, symbol) if shipping_value is not None else "N/D"
            total_display = format_money(total_value, symbol)

            row = [
                now,
                site,
                sku,
                "36 / 36.5 / 4Y / 4.5Y / 4 / 4.5",
                "GS/Adult",
                price_display,
                shipping_display,
                total_display,
                "Possible match - title filtered",
                "",
                link,
                "Google Shopping",
                title,
            ]

            found_items.append({
                "sort_total": total_value if total_value is not None else 999999,
                "row": row,
                "alert": total_value is not None and total_value <= alert_2,
            })

    try:
        organic_results = serpapi_google_search(
            f'"{sku}" OR "{search_term}"',
            max_results,
        )
    except Exception as error:
        organic_results = []
        found_items.append({
            "sort_total": 999999,
            "row": [
                now,
                "SYSTEM",
                sku,
                "",
                "",
                "",
                "",
                "",
                f"Google search error: {error}",
                "",
                "",
                "SerpAPI",
                "",
            ],
        })

    for item in organic_results:
        title = item.get("title", "")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        domain = domain_from_url(link)

        if allowed_domains and domain not in allowed_domains:
            continue

        title_and_snippet = f"{title} {snippet}"

        if not title_is_valid(title_and_snippet, sku):
            filtered_out += 1
            continue

        row = [
            now,
            domain,
            sku,
            "To verify",
            "To verify",
            "N/D",
            "N/D",
            "N/D",
            "Found page - title filtered",
            "",
            link,
            "Google Search",
            title,
        ]

        found_items.append({
            "sort_total": 999999,
            "row": row,
            "alert": False,
        })

    unique_items = []
    seen = set()

    for item in found_items:
        row = item["row"]
        key = (
            str(row[1]).lower().strip(),
            str(row[5]).lower().strip(),
            str(row[12]).lower().strip(),
        )

        if key in seen:
            continue

        seen.add(key)
        unique_items.append(item)

    unique_items.sort(key=lambda x: x["sort_total"])

    found_rows = [item["row"] for item in unique_items]
    alert_items = [item for item in unique_items if item.get("alert")]

    print(f"FOUND_ROWS = {len(found_rows)}")
    print(f"FILTERED_OUT = {filtered_out}")
    print("WRITING TO GOOGLE SHEETS")

    write_rows_to_results(results_ws, found_rows)

    summary = (
        "🔍 Sneaker Tracker V6.3\n\n"
        f"Risultati validi: {len(found_rows)}\n"
        f"Scartati dal filtro: {filtered_out}\n"
        f"Possibili alert ≤ {alert_2} €: {len(alert_items)}"
    )

    if alert_items:
        first_alerts = alert_items[:5]

        details = "\n\n".join([
            f"🚨 {item['row'][1]}\n"
            f"Price: {item['row'][5]}\n"
            f"Shipping: {item['row'][6]}\n"
            f"Total: {item['row'][7]}\n"
            f"Link: {item['row'][10]}\n"
            f"Titolo: {item['row'][12]}"
            for item in first_alerts
        ])

        send_telegram(summary + "\n\n" + details)
    else:
        send_telegram(summary + "\n\nNessun risultato sotto soglia trovato per ora.")


if __name__ == "__main__":
    main()
