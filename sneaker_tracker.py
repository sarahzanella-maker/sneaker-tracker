import os, json, re
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote

import requests
import gspread
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

SOURCE_SHEET_NAME = "Sneaker Sources"
SETTINGS_SHEET_NAME = "Settings"
RESULTS_SHEET_NAME = "Results"
HISTORY_SHEET_NAME = "History"

HEADERS = [
    "Date", "Site", "SKU", "Size", "Type", "Price", "Shipping", "Total",
    "Availability", "Stock", "URL", "Source Type", "Notes"
]

BLOCKED = [
    "iq7605-101", "preschool", "(ps)", " ps ", "toddler", "(td)", " td ",
    "infant", "kids", "baby", "bambino", "bambina", "junior",
    "olive", "medium olive", "black olive", "reverse mocha", "canary",
    "velvet brown", "fragment", "phantom", "air force", "dunk", "air max",
    "nocta", "glide", "flyease", "why not", "zer0.4", "hikvision",
    "registratore", "nvr", "camera", "ds-7604"
]

NON_PRODUCT_DOMAINS = [
    "instagram.com", "facebook.com", "tiktok.com", "youtube.com",
    "lesitedelasneaker.com", "sneakernews.com", "soleretriever.com",
    "hypebeast.com", "nicekicks.com", "complex.com"
]


def send_telegram(message):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=30,
    )


def connect_sheet():
    creds = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(creds, scopes=scopes)
    return gspread.authorize(credentials).open_by_key(GOOGLE_SHEET_ID)


def normalize(v):
    return str(v).strip()


def read_settings(ws):
    settings = {}
    for row in ws.get_all_records():
        k = normalize(row.get("Parameter", ""))
        v = normalize(row.get("Value", ""))
        if k:
            settings[k] = v
    return settings


def domain_from_url(url):
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def extract_real_link(link):
    if not link:
        return ""
    link = str(link)
    if "google.com" not in link:
        return link

    params = parse_qs(urlparse(link).query)
    for key in ["url", "q"]:
        if key in params and params[key]:
            candidate = unquote(params[key][0])
            if candidate.startswith("http") and "google.com" not in candidate:
                return candidate
    return link


def detect_currency(text):
    text = str(text)
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
    text = text.replace("\xa0", " ")

    matches = re.findall(r"[€$£]?\s?\d{2,5}(?:[.,]\d{2})?", text)
    candidates = []

    for m in matches:
        cleaned = m.replace("€", "").replace("$", "").replace("£", "").strip()
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")
        try:
            val = float(cleaned)
            if val >= 50:
                candidates.append(val)
        except Exception:
            pass

    if not candidates:
        return None

    return max(candidates)


def money(amount, symbol):
    if amount is None:
        return "N/D"
    return f"{symbol}{amount:.2f}"


def title_is_valid(text, sku):
    t = f" {str(text).lower()} "
    if any(b in t for b in BLOCKED):
        return False
    has_sku = sku.lower() in t
    has_name = all(x in t for x in ["travis", "scott", "tropical", "pink"])
    return has_sku or has_name


def serpapi_google(query, max_results=10):
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERPAPI_KEY,
        "gl": "it",
        "hl": "it",
        "num": max_results,
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("organic_results", [])


def verify_page(url, sku):
    if not url or "google.com" in url:
        return None, "€", "Price not verified - Google link", ""

    domain = domain_from_url(url)
    if any(d in domain for d in NON_PRODUCT_DOMAINS):
        return None, "€", "Price not verified - non-product page", ""

    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code >= 400:
            return None, "€", f"Price not verified - HTTP {r.status_code}", ""

        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        visible_text = soup.get_text(" ", strip=True)
        low = visible_text.lower()

        if not title_is_valid(visible_text[:5000], sku):
            return None, detect_currency(html), "Rejected - product text not confirmed", ""

        if "36.5" in low or "36,5" in low or "4.5y" in low or "4.5 y" in low:
            size_note = "Size likely present"
            size_value = "36.5 / 4.5Y likely"
        elif "36" in low or "4y" in low or "4 y" in low:
            size_note = "Size 36/4Y likely present"
            size_value = "36 / 4Y likely"
        else:
            size_note = "Size not verified"
            size_value = "To verify"

        symbol = detect_currency(html)
        price_candidates = []

        for attrs in [
            {"property": "product:price:amount"},
            {"property": "og:price:amount"},
            {"itemprop": "price"},
        ]:
            tag = soup.find(attrs=attrs)
            if tag:
                val = parse_price(tag.get("content") or tag.get("value"))
                if val:
                    price_candidates.append(val)

        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                objs = data if isinstance(data, list) else [data]
                for obj in objs:
                    if not isinstance(obj, dict):
                        continue
                    offers = obj.get("offers")
                    if isinstance(offers, dict):
                        val = parse_price(offers.get("price"))
                        if val:
                            price_candidates.append(val)
                    elif isinstance(offers, list):
                        for offer in offers:
                            val = parse_price(offer.get("price"))
                            if val:
                                price_candidates.append(val)
            except Exception:
                pass

        # fallback prezzi visibili
        fallback = parse_price(html)
        if fallback:
            price_candidates.append(fallback)

        return None, symbol, f"Price not found - {size_note}", size_value

        valid_prices = [
            p for p in price_candidates
            if 80 <= p <= 3000
]

        if valid_prices:
            return min(valid_prices), symbol, f"Price verified - {size_note}", size_value

    except Exception as e:
        return None, "€", f"Price not verified - {str(e)[:60]}", ""


def ensure_headers(ws):
    values = ws.get_all_values()
    if not values:
        ws.update(values=[HEADERS], range_name="A1:M1")
    else:
        ws.update(values=[HEADERS], range_name="A1:M1")


def clear_results(ws):
    ws.clear()
    ws.update(values=[HEADERS], range_name="A1:M1")


def write_rows(ws, rows):
    if not rows:
        return
    clean = [[str(c) if c is not None else "" for c in row] for row in rows]
    start = len(ws.get_all_values()) + 1
    end = start + len(clean) - 1
    ws.update(values=clean, range_name=f"A{start}:M{end}", value_input_option="USER_ENTERED")


def main():
    sheet = connect_sheet()
    sources_ws = sheet.worksheet(SOURCE_SHEET_NAME)
    settings_ws = sheet.worksheet(SETTINGS_SHEET_NAME)
    results_ws = sheet.worksheet(RESULTS_SHEET_NAME)
    history_ws = sheet.worksheet(HISTORY_SHEET_NAME)

    ensure_headers(history_ws)
    clear_results(results_ws)

    settings = read_settings(settings_ws)
    sku = settings.get("SKU", "IQ7604-101")
    search_term = settings.get("Search Term", "Travis Scott Tropical Pink")
    alert_2 = float(settings.get("Alert 2", "400"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    source_rows = sources_ws.get_all_records()
    rows = []
    alerts = []
    checked = 0
    no_result = 0

    for source in source_rows:
        active = normalize(source.get("ATTIVO", "")).upper()
        enabled = normalize(source.get("Enabled", "")).upper()
        site_name = normalize(source.get("SITO", ""))
        site_url = normalize(source.get("URL", ""))

        if active not in ["YES", "SI", "SÌ", "TRUE", "1"]:
            continue
        if enabled not in ["YES", "SI", "SÌ", "TRUE", "1"]:
            continue

        domain = domain_from_url(site_url)
        if not domain:
            continue

        checked += 1

        query = f'site:{domain} "{sku}" OR "{search_term}"'
        try:
            results = serpapi_google(query, 5)
        except Exception as e:
            rows.append([
                now, site_name or domain, sku, "", "", "N/D", "N/D", "N/D",
                f"Search error: {e}", "", site_url, "V9 site search", query
            ])
            continue

        best_link = ""
        best_title = ""

        for result in results:
            title = result.get("title", "")
            link = extract_real_link(result.get("link", ""))
            snippet = result.get("snippet", "")
            text = f"{title} {snippet} {link}"

            if title_is_valid(text, sku):
                best_link = link
                best_title = title
                break

        if not best_link:
            no_result += 1
            rows.append([
                now, site_name or domain, sku, "To verify", "To verify",
                "N/D", "N/D", "N/D", "No product page found",
                "", site_url, "V9 site search", query
            ])
            continue

        price, symbol, status, size_value = verify_page(best_link, sku)

        total = price
        row = [
            now,
            site_name or domain,
            sku,
            size_value or "To verify",
            "GS/Adult",
            money(price, symbol),
            "N/D",
            money(total, symbol),
            status,
            "",
            best_link,
            "V9 site-by-site",
            best_title,
        ]

        rows.append(row)

        if price is not None and price <= alert_2 and status.startswith("Price verified"):
            alerts.append(row)

    def sort_key(row):
        val = parse_price(row[7])
        return val if val is not None else 999999

    rows.sort(key=sort_key)

    write_rows(results_ws, rows)
    write_rows(history_ws, rows)

    summary = (
        "🔍 Sneaker Tracker V9\n\n"
        f"Siti controllati: {checked}\n"
        f"Senza pagina prodotto: {no_result}\n"
        f"Righe salvate: {len(rows)}\n"
        f"Alert ≤ {alert_2} €: {len(alerts)}"
    )

    if alerts:
        details = "\n\n".join([
            f"🚨 {r[1]}\n"
            f"Price: {r[5]}\n"
            f"Total: {r[7]}\n"
            f"Size: {r[3]}\n"
            f"Status: {r[8]}\n"
            f"Link: {r[10]}"
            for r in alerts[:5]
        ])
        send_telegram(summary + "\n\n" + details)
    else:
        send_telegram(summary + "\n\nNessun alert reale sotto soglia trovato.")


if __name__ == "__main__":
    main()
