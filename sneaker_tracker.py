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


def normalize(value):
    return str(value).strip()


def read_settings(ws):
    settings = {}
    for row in ws.get_all_records():
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
    text = str(value).replace(".", "").replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def money(amount, symbol):
    if amount is None:
        return "N/D"
    return f"{symbol}{amount:.2f}"


def title_is_valid(title, sku):
    t = f" {str(title).lower()} "
    if any(b in t for b in BLOCKED):
        return False
    has_sku = sku.lower() in t
    has_name = all(x in t for x in ["travis", "scott", "tropical", "pink"])
    return has_sku or has_name


def parse_shipping(text):
    if not text or text == "N/D":
        return None
    low = str(text).lower()
    if "free" in low or "gratis" in low or "gratuita" in low:
        return 0.0
    return parse_price(text)


def serpapi_google(query, max_results, engine="google"):
    params = {
        "engine": engine,
        "q": query,
        "api_key": SERPAPI_KEY,
        "gl": "it",
        "hl": "it",
        "num": max_results,
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if engine == "google_shopping":
        return data.get("shopping_results", [])
    return data.get("organic_results", [])


def find_real_product_link(site, sku):
    query = f'"{site}" "{sku}" "36.5"'
    try:
        results = serpapi_google(query, 10, "google")
    except Exception:
        return ""
    for r in results:
        link = extract_real_link(r.get("link", ""))
        text = f"{r.get('title','')} {r.get('snippet','')} {link}".lower()
        if sku.lower() in text or all(x in text for x in ["travis", "scott", "tropical", "pink"]):
            if "google.com" not in link:
                return link
    return ""


def verify_page_price_and_size(url, sku):
    """
    Best-effort check. Some sites block GitHub requests.
    Returns: verified_price, currency_symbol, availability_note
    """
    if not url or "google.com" in url:
        return None, None, "Price not verified - Google link"

    blocked_price_domains = [
        "instagram.com",
        "facebook.com",
        "tiktok.com",
        "youtube.com",
        "lesitedelasneaker.com",
        "sneakernews.com",
        "soleretriever.com",
        "hypebeast.com",
        "nicekicks.com",
        "complex.com",
    ]

    domain = domain_from_url(url)

    if any(blocked in domain for blocked in blocked_price_domains):
        return None, None, "Price not verified - non-product page"

    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        if r.status_code >= 400:
            return None, None, f"Price not verified - HTTP {r.status_code}"

        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True).lower()

        if "36.5" in text or "36,5" in text or "4.5y" in text or "4.5 y" in text:
            size_note = "Size likely present"
        elif "36" in text or "4y" in text or "4 y" in text:
            size_note = "Size 36/4Y likely present"
        else:
            size_note = "Size not verified"

        symbol = detect_currency(html)

        price_candidates = []

        meta_props = [
            {"property": "product:price:amount"},
            {"property": "og:price:amount"},
            {"itemprop": "price"},
        ]

        for attrs in meta_props:
            tag = soup.find(attrs=attrs)
            if tag:
                content = tag.get("content") or tag.get("value")
                val = parse_price(content)
                if val:
                    price_candidates.append(val)

        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                objects = data if isinstance(data, list) else [data]
                for obj in objects:
                    offers = obj.get("offers") if isinstance(obj, dict) else None
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

        # Fallback: visible prices like €919,00
        for m in re.findall(r"[€$£]\s?\d{2,5}(?:[.,]\d{2})?", html):
            val = parse_price(m)
            if val:
                price_candidates.append(val)

        if price_candidates:
            # avoid tiny unrelated values; use highest plausible product price
            price_candidates = [p for p in price_candidates if p >= 50]
            if price_candidates:
                return max(price_candidates), symbol, f"Price verified - {size_note}"

        return None, symbol, f"Price not found - {size_note}"

    except Exception as e:
        return None, None, f"Price not verified - {str(e)[:60]}"


def ensure_headers(ws):
    values = ws.get_all_values()
    if not values:
        ws.update("A1:M1", [HEADERS])
    else:
        ws.update("A1:M1", [HEADERS])


def clear_results(ws):
    ws.clear()
    ws.update("A1:M1", [HEADERS])


def write_rows(ws, rows):
    if not rows:
        return
    clean = [[str(c) if c is not None else "" for c in row] for row in rows]
    start = len(ws.get_all_values()) + 1
    end = start + len(clean) - 1
    ws.update(f"A{start}:M{end}", clean, value_input_option="USER_ENTERED")


def main():
    sheet = connect_sheet()
    sources_ws = sheet.worksheet(SOURCE_SHEET_NAME)
    settings_ws = sheet.worksheet(SETTINGS_SHEET_NAME)
    results_ws = sheet.worksheet(RESULTS_SHEET_NAME)
    history_ws = sheet.worksheet(HISTORY_SHEET_NAME)

    ensure_headers(history_ws)
    clear_results(results_ws)

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
            d = domain_from_url(url)
            if d:
                allowed_domains.append(d)

    queries = [
        f'"{sku}" "Jordan"',
        f'"{search_term}"',
        f'"Travis Scott" "Tropical Pink" "Jordan"',
    ]

    items = []
    filtered_out = 0

    for query in queries:
        try:
            shopping = serpapi_google(query, max_results, "google_shopping")
        except Exception as e:
            shopping = []
            items.append({
                "sort_total": 999999,
                "alert": False,
                "row": [now, "SYSTEM", sku, "", "", "", "", "", f"Shopping error: {e}", "", "", "SerpAPI", query],
            })

        for item in shopping:
            title = item.get("title", "")
            if not title_is_valid(title, sku):
                filtered_out += 1
                continue

            source = item.get("source", "")
            raw_link = item.get("product_link") or item.get("link") or ""
            link = extract_real_link(raw_link)

            if "google.com" in link:
                found = find_real_product_link(source, sku)
                if found:
                    link = found

            price_text = item.get("price", "")
            google_price = item.get("extracted_price") or parse_price(price_text)
            symbol = detect_currency(price_text)

            shipping_text = item.get("shipping") or item.get("delivery") or "N/D"
            shipping_value = parse_shipping(shipping_text)

            verified_price, verified_symbol, note = verify_page_price_and_size(link, sku)

            final_price = verified_price if verified_price is not None else google_price
            final_symbol = verified_symbol or symbol

            total = final_price
            if final_price is not None and shipping_value is not None:
                total = final_price + shipping_value

            site = source or domain_from_url(link) or "Google Shopping"

            row = [
                now,
                site,
                sku,
                "36 / 36.5 / 4Y / 4.5Y / 4 / 4.5",
                "GS/Adult",
                money(final_price, final_symbol),
                money(shipping_value, final_symbol) if shipping_value is not None else "N/D",
                money(total, final_symbol),
                note,
                "",
                link,
                "V8 verified",
                title,
            ]

            items.append({
                "sort_total": total if total is not None else 999999,
                "alert": total is not None and total <= alert_2,
                "row": row,
            })

    # Organic discovery, price not verified unless page available
    try:
        organic = serpapi_google(f'"{sku}" OR "{search_term}"', max_results, "google")
    except Exception:
        organic = []

    for result in organic:
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        link = extract_real_link(result.get("link", ""))
        domain = domain_from_url(link)

        if allowed_domains and domain not in allowed_domains:
            continue

        if not title_is_valid(f"{title} {snippet}", sku):
            filtered_out += 1
            continue

        price, symbol, note = verify_page_price_and_size(link, sku)

        row = [
            now,
            domain,
            sku,
            "To verify",
            "To verify",
            money(price, symbol or "€"),
            "N/D",
            money(price, symbol or "€"),
            note,
            "",
            link,
            "V8 organic verified",
            title,
        ]

        items.append({
    "sort_total": total if total is not None else 999999,
    "row": row,
    "alert": (
        total is not None
        and total <= alert_2
        and "Price verified" in note
        and "non-product" not in note
    ),
})

    # Deduplicate
    unique = []
    seen = set()
    for item in items:
        r = item["row"]
        key = (str(r[1]).lower(), str(r[10]).lower(), str(r[12]).lower(), str(r[5]).lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    unique.sort(key=lambda x: x["sort_total"])

    rows = [i["row"] for i in unique]
    alerts = [i for i in unique if i.get("alert")]

    write_rows(results_ws, rows)
    write_rows(history_ws, rows)

    summary = (
        "🔍 Sneaker Tracker V8\n\n"
        f"Risultati ultimo check: {len(rows)}\n"
        f"Scartati dal filtro: {filtered_out}\n"
        f"Alert ≤ {alert_2} €: {len(alerts)}"
    )

    if alerts:
        details = "\n\n".join([
            f"🚨 {i['row'][1]}\n"
            f"Price: {i['row'][5]}\n"
            f"Shipping: {i['row'][6]}\n"
            f"Total: {i['row'][7]}\n"
            f"Status: {i['row'][8]}\n"
            f"Link: {i['row'][10]}\n"
            f"Titolo: {i['row'][12]}"
            for i in alerts[:5]
        ])
        send_telegram(summary + "\n\n" + details)
    else:
        send_telegram(summary + "\n\nNessun alert sotto soglia trovato.")


if __name__ == "__main__":
    main()
