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

SETTINGS_SHEET = "Settings"
SOURCES_SHEET = "Sources"
RESULTS_SHEET = "Results"

RESULT_HEADERS = ["Rank", "Site", "Price", "Size", "Status", "URL", "Last Check"]

BLOCKED_TERMS = [
    "iq7605-101", "preschool", "(ps)", " ps ", "toddler", "(td)", " td ",
    "infant", "kids", "baby", "junior", "bambino", "bambina",
    "olive", "medium olive", "reverse mocha", "canary", "velvet brown",
    "fragment", "phantom", "air force", "dunk", "air max", "nocta",
    "glide", "flyease", "why not", "hikvision", "ds-7604"
]

NON_PRODUCT_PATHS = [
    "/blog", "/blogs", "/news", "/release", "/releases", "/raffle",
    "/magazine", "/editorial", "/article", "/articles"
]

NON_PRODUCT_DOMAINS = [
    "instagram.com", "facebook.com", "tiktok.com", "youtube.com",
    "reddit.com", "x.com", "twitter.com", "pinterest.com",
    "sneakernews.com", "hypebeast.com", "nicekicks.com",
    "complex.com", "lesitedelasneaker.com"
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


def path_from_url(url):
    try:
        return urlparse(url).path.lower()
    except Exception:
        return ""


def is_non_product_url(url):
    domain = domain_from_url(url)
    path = path_from_url(url)

    if not domain:
        return True

    if any(d in domain for d in NON_PRODUCT_DOMAINS):
        return True

    if any(p in path for p in NON_PRODUCT_PATHS):
        return True

    return False


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

    text = str(value).replace("\xa0", " ").strip()

    if not re.search(r"\d", text):
        return None

    text = text.replace("€", "").replace("$", "").replace("£", "")
    text = text.replace("EUR", "").replace("USD", "").replace("GBP", "")
    text = text.strip()

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")

    match = re.search(r"\d+(?:\.\d+)?", text)

    if not match:
        return None

    try:
        amount = float(match.group(0))
        if 80 <= amount <= 3000:
            return amount
        return None
    except Exception:
        return None


def money(amount, symbol):
    if amount is None:
        return "N/D"
    return f"{symbol}{amount:.2f}"


def title_is_valid(text, sku):
    text = f" {str(text).lower()} "

    if any(term in text for term in BLOCKED_TERMS):
        return False

    has_sku = sku.lower() in text
    has_name = all(word in text for word in ["travis", "scott", "tropical", "pink"])

    return has_sku or has_name


def size_status(text, target_sizes):
    low = str(text).lower().replace(",", ".")

    for size in target_sizes:
        s = size.strip().lower()
        if not s:
            continue

        if s in low:
            return f"{size.strip()} likely"

        if s.endswith("y") and s.replace("y", " y") in low:
            return f"{size.strip()} likely"

    return "To verify"


def serpapi_google(query, max_results):
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

    return response.json().get("organic_results", [])


def find_product_url(domain, sku, search_term, max_results):
    query = f'site:{domain} "{sku}" OR "{search_term}"'

    try:
        results = serpapi_google(query, max_results)
    except Exception:
        return "", "Search error"

    for result in results:
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        link = extract_real_link(result.get("link", ""))

        if is_non_product_url(link):
            continue

        combined = f"{title} {snippet} {link}"

        if title_is_valid(combined, sku):
            return link, title

    return "", "No product page found"


def extract_structured_price(soup):
    prices = []

    for attrs in [
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
        {"name": "twitter:data1"},
    ]:
        tag = soup.find(attrs=attrs)
        if tag:
            value = parse_price(tag.get("content") or tag.get("value"))
            if value:
                prices.append(value)

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            offers = obj.get("offers")

            if isinstance(offers, dict):
                value = parse_price(offers.get("price"))
                if value:
                    prices.append(value)

            elif isinstance(offers, list):
                for offer in offers:
                    if isinstance(offer, dict):
                        value = parse_price(offer.get("price"))
                        if value:
                            prices.append(value)

    if not prices:
        return None

    return min(prices)


def verify_product_page(url, sku, target_sizes):
    if not url:
        return None, "€", "No URL", "To verify"

    if is_non_product_url(url):
        return None, "€", "Rejected - non-product URL", "To verify"

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=25,
        )

        if response.status_code >= 400:
            return None, "€", f"Not verified - HTTP {response.status_code}", "To verify"

        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        if not title_is_valid(text[:6000], sku):
            return None, detect_currency(html), "Rejected - product text not confirmed", "To verify"

        symbol = detect_currency(html)
        price = extract_structured_price(soup)
        size = size_status(text, target_sizes)

        if price is not None:
            return price, symbol, "Price verified", size

        return None, symbol, "Price not found structured", size

    except Exception as error:
        return None, "€", f"Not verified - {str(error)[:60]}", "To verify"


def clear_results(ws):
    ws.clear()
    ws.update(values=[RESULT_HEADERS], range_name="A1:G1")


def write_results(ws, rows):
    if not rows:
        return

    clean_rows = [[str(cell) if cell is not None else "" for cell in row] for row in rows]

    end_row = len(clean_rows) + 1

    if ws.row_count < end_row:
        ws.add_rows(end_row - ws.row_count)

    ws.update(
        values=clean_rows,
        range_name=f"A2:G{end_row}",
        value_input_option="USER_ENTERED",
    )


def main():
    sheet = connect_sheet()

    settings_ws = sheet.worksheet(SETTINGS_SHEET)
    sources_ws = sheet.worksheet(SOURCES_SHEET)
    results_ws = sheet.worksheet(RESULTS_SHEET)

    clear_results(results_ws)

    settings = read_settings(settings_ws)

    sku = settings.get("SKU", "IQ7604-101")
    search_term = settings.get("Search Term", "Travis Scott Tropical Pink")
    target_sizes = [s.strip() for s in settings.get("Target Sizes", "36,36.5,4Y,4.5Y").split(",")]
    max_results = int(float(settings.get("Max Google Results", "10")))
    telegram_top = int(float(settings.get("Telegram Top Results", "5")))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sources = sources_ws.get_all_records()

    raw_results = []
    checked = 0
    verified_count = 0
    product_url_count = 0
    google_found_count = 0

    for source in sources:
        site = normalize(source.get("Site", ""))
        domain = normalize(source.get("Domain", ""))
        product_url = normalize(source.get("Product URL", ""))
        enabled = normalize(source.get("Enabled", "")).upper()

        if enabled not in ["TRUE", "YES", "SI", "SÌ", "1"]:
            continue

        if not domain and product_url:
            domain = domain_from_url(product_url)

        if not domain:
            continue

        checked += 1

        if product_url:
            url = product_url
            title = "Product URL from Sources"
            product_url_count += 1
        else:
            url, title = find_product_url(domain, sku, search_term, max_results)
            if url:
                google_found_count += 1

        if not url:
            raw_results.append({
                "price": None,
                "row": [
                    "",
                    site or domain,
                    "N/D",
                    "To verify",
                    title,
                    f"https://{domain}",
                    now,
                ],
            })
            continue

        price, symbol, status, size = verify_product_page(url, sku, target_sizes)

        if price is not None and status == "Price verified":
            verified_count += 1

        raw_results.append({
            "price": price if price is not None else 999999,
            "row": [
                "",
                site or domain,
                money(price, symbol),
                size,
                status,
                url,
                now,
            ],
        })

    raw_results.sort(
    key=lambda x: x["price"] if x["price"] is not None else 999999
)

    final_rows = []
    rank = 1

    for item in raw_results:
        row = item["row"]

        if isinstance(item["price"], (int, float)) and item["price"] < 999999:
            row[0] = rank
            rank += 1
        else:
            row[0] = ""

        final_rows.append(row)

    write_results(results_ws, final_rows)

    verified_rows = [
    item
    for item in raw_results
    if isinstance(item["price"], (int, float))
    and item["price"] > 0
    and item["price"] < 999999
]

    summary = (
        "📊 Sneaker Tracker V11\n\n"
        f"Siti controllati: {checked}\n"
        f"Product URL usati: {product_url_count}\n"
        f"Trovati via Google: {google_found_count}\n"
        f"Prezzi verificati: {verified_count}\n"
    )

    if verified_rows:
        best = verified_rows[0]["row"]

        details = (
            f"\n🥇 Miglior prezzo verificato\n"
            f"{best[1]} → {best[2]}\n"
            f"Size: {best[3]}\n"
            f"Status: {best[4]}\n"
            f"Link: {best[5]}"
        )

        others = []
        for item in verified_rows[1:telegram_top]:
            row = item["row"]
            others.append(f"{row[1]} → {row[2]}")

        if others:
            details += "\n\n📋 Altri prezzi verificati\n" + "\n".join(others)

        send_telegram(summary + details)
    else:
        send_telegram(summary + "\nNessun prezzo verificato trovato oggi.")


if __name__ == "__main__":
    main()
