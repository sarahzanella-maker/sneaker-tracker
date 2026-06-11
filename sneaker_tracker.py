import os
import json
import re
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

RESULT_HEADERS = ["Rank", "Site", "Trust", "Price", "Size", "Status", "URL", "Last Check"]

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

TRUST_MAP = {
    "stockx": "A",
    "goat": "A",
    "klekt": "A",
    "novelship": "A",
    "stadium goods": "A",
    "stadiumgoods": "A",
    "wethenew": "A",
    "flight club": "A",
    "flightclub": "A",

    "hypeboost": "B",
    "overkicks": "B",
    "over kicks": "B",
    "kis": "B",
    "menta": "B",
    "select": "B",
    "outgem": "B",

    "whiteturin": "C",
    "white turin": "C",
    "request": "C",
    "mrreseller": "C",
    "mr reseller": "C",
    "sutore": "C",
    "zneakerz": "C",
    "lab19": "C",
    "laced": "C",

    "subito": "D",
}

DEFAULT_TRUST = "?"


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


def clean_url(url):
    if not url:
        return ""

    url = str(url).strip()
    url = url.replace("https://https://", "https://")
    url = url.replace("http://http://", "http://")
    url = url.replace("http://https://", "https://")
    url = url.replace("https://http://", "https://")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    return url


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
        return urlparse(clean_url(url)).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def path_from_url(url):
    try:
        return urlparse(clean_url(url)).path.lower()
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
        return clean_url(link)

    params = parse_qs(urlparse(link).query)

    for key in ["url", "q"]:
        if key in params and params[key]:
            candidate = unquote(params[key][0])
            if candidate.startswith("http") and "google.com" not in candidate:
                return clean_url(candidate)

    return clean_url(link)


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


def get_trust(site):
    value = str(site).lower().strip()

    for key, trust in TRUST_MAP.items():
        if key in value:
            return trust

    return DEFAULT_TRUST


def title_is_valid(text, sku):
    text = f" {str(text).lower()} "

    if any(term in text for term in BLOCKED_TERMS):
        return False

    has_sku = sku.lower() in text
    has_name = all(word in text for word in ["travis", "scott", "tropical", "pink"])

    return has_sku or has_name


def size_status(text, target_sizes):
    low = str(text).lower().replace(",", ".")

    found = []

    for size in target_sizes:
        s = size.strip().lower()
        if not s:
            continue

        variants = [s]

        if s.endswith("y"):
            variants.append(s.replace("y", " y"))

        if s == "36.5":
            variants.extend(["36,5", "eu 36.5", "eu 36,5"])
        elif s == "36":
            variants.extend(["eu 36", " 36 "])
        elif s == "4y":
            variants.extend(["4 y", "us 4y", "us 4 y"])
        elif s == "4.5y":
            variants.extend(["4.5 y", "us 4.5y", "us 4.5 y"])

        if any(v in low for v in variants):
            found.append(size.strip())

    if found:
        return " / ".join(found) + " likely"

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


def find_product_url(domain_url, sku, search_term, max_results):
    domain = domain_from_url(domain_url)

    if not domain:
        return "", "Invalid domain"

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


def _parse_visible_amount(value):
    return parse_price(value)


def _price_matches_with_positions(text):
    patterns = [
        r"€\s?(\d{2,5}(?:[.,]\d{2})?)",
        r"EUR\s?(\d{2,5}(?:[.,]\d{2})?)",
        r"(\d{2,5}(?:[.,]\d{2})?)\s?€",
        r"\$\s?(\d{2,5}(?:[.,]\d{2})?)",
        r"USD\s?(\d{2,5}(?:[.,]\d{2})?)",
        r"£\s?(\d{2,5}(?:[.,]\d{2})?)",
        r"GBP\s?(\d{2,5}(?:[.,]\d{2})?)",
    ]

    prices = []

    for pattern in patterns:
        for match in re.finditer(pattern, str(text), flags=re.IGNORECASE):
            raw_value = match.group(1)
            value = _parse_visible_amount(raw_value)
            if value is not None and 350 <= value <= 3000:
                blocked_context = [
        "klarna",
        "rate",
        "rata",
        "rate da",
        "installment",
        "installments",
        "paypal 3 rate",
    ]

context = raw_text[max(0, match.start()-50):match.end()+50].lower()

if any(word in context for word in blocked_context):
    continue
                prices.append({
                    "value": value,
                    "start": match.start(),
                    "end": match.end(),
                    "raw": match.group(0),
                })

    return prices


def _size_variants(target_sizes):
    variants = []

    for size in target_sizes:
        s = str(size).strip().lower()
        if not s:
            continue

        clean = s.replace(",", ".")
        variants.append(clean)

        if clean == "36.5":
            variants.extend(["36,5", "eu 36.5", "eu 36,5", "taglia 36.5", "taglia 36,5"])
        elif clean == "36":
            variants.extend(["eu 36", "taglia 36"])
        elif clean == "4y":
            variants.extend(["4 y", "us 4y", "us 4 y"])
        elif clean == "4.5y":
            variants.extend(["4.5 y", "us 4.5y", "us 4.5 y", "4,5y", "4,5 y"])

    # Prefer longer variants first so 36.5 wins over 36 when both overlap.
    unique = sorted(set(variants), key=len, reverse=True)
    return unique


def extract_visible_price_for_sizes(text, target_sizes):
    """
    Fallback for pages where prices are visible but not exposed as structured data.
    It only accepts prices close to target sizes, avoiding the old mistake of taking
    the cheapest price from another size on the same product page.
    """
    raw_text = str(text)
    normalized = raw_text.lower().replace(",", ".")

    prices = _price_matches_with_positions(raw_text)
    if not prices:
        return None

    size_hits = []
    for variant in _size_variants(target_sizes):
        v = variant.lower().replace(",", ".")
        if not v:
            continue

        # Avoid matching 36 inside 36.5 or 136 by using loose numeric boundaries.
        if re.fullmatch(r"\d+(?:\.\d+)?", v):
            pattern = rf"(?<![\d.]){re.escape(v)}(?![\d.])"
        else:
            pattern = re.escape(v)

        for match in re.finditer(pattern, normalized):
            size_hits.append({
                "variant": variant,
                "start": match.start(),
                "end": match.end(),
            })

    if not size_hits:
        return None

    candidates = []

    for size_hit in size_hits:
        for price in prices:
            distance_after = price["start"] - size_hit["end"]
            distance_before = size_hit["start"] - price["end"]

            # Most shop grids show: SIZE then PRICE. Prioritize prices shortly after the size.
            if 0 <= distance_after <= 120:
                candidates.append((0, distance_after, price["value"]))
            # Some layouts show: PRICE then SIZE. Accept only if very close.
            elif 0 <= distance_before <= 60:
                candidates.append((1, distance_before, price["value"]))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    return candidates[0][2]


def verify_product_page(url, sku, target_sizes, trust_product_url=False):
    if not url:
        return None, "€", "No URL", "To verify"

    url = clean_url(url)

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

        if not trust_product_url and not title_is_valid(text[:6000], sku):
            return None, detect_currency(html), "Rejected - product text not confirmed", "To verify"

        symbol = detect_currency(html)
        price = extract_structured_price(soup)
        price_source = "structured"

        if price is None:
            price = extract_visible_price_for_sizes(text, target_sizes)
            price_source = "visible-size"

        size = size_status(text, target_sizes)

        if price is not None:
            return price, symbol, f"Price verified ({price_source})", size

        return None, symbol, "Price not found", size

    except Exception as error:
        return None, "€", f"Not verified - {str(error)[:60]}", "To verify"


def clear_results(ws):
    ws.clear()
    ws.update(values=[RESULT_HEADERS], range_name="A1:H1")


def write_results(ws, rows):
    if not rows:
        return

    clean_rows = [[str(cell) if cell is not None else "" for cell in row] for row in rows]

    end_row = len(clean_rows) + 1

    if ws.row_count < end_row:
        ws.add_rows(end_row - ws.row_count)

    ws.update(
        values=clean_rows,
        range_name=f"A2:H{end_row}",
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
    target_sizes = [
        s.strip()
        for s in settings.get("Target Sizes", "36,36.5,4Y,4.5Y").split(",")
    ]
    max_results = int(float(settings.get("Max Google Results", "10")))
    telegram_top = int(float(settings.get("Telegram Top Results", "5")))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sources = sources_ws.get_all_records()

    raw_results = []
    checked = 0
    verified_count = 0
    product_url_count = 0
    google_found_count = 0
    manual_check = []

    for source in sources:
        site = normalize(source.get("Site", ""))
        domain_raw = normalize(source.get("Domain", ""))
        product_url_raw = normalize(source.get("Product URL", ""))
        enabled = normalize(source.get("Enabled", "")).upper()

        if enabled not in ["TRUE", "YES", "SI", "SÌ", "1"]:
            continue

        domain_url = clean_url(domain_raw)
        product_url = clean_url(product_url_raw) if product_url_raw else ""

        if not domain_url and product_url:
            domain_url = clean_url(domain_from_url(product_url))

        if not domain_url:
            continue

        checked += 1

        if product_url:
            url = product_url
            title = "Product URL from Sources"
            product_url_count += 1
            trust_product_url = True
        else:
            url, title = find_product_url(domain_url, sku, search_term, max_results)
            trust_product_url = False
            if url:
                google_found_count += 1

        if not url:
            status = title
            row = [
                "",
                site or domain_from_url(domain_url),
                get_trust(site or domain_from_url(domain_url)),
                "N/D",
                "To verify",
                status,
                domain_url,
                now,
            ]
            raw_results.append({"price": None, "row": row})
            manual_check.append(site or domain_from_url(domain_url))
            continue

        price, symbol, status, size = verify_product_page(
            url,
            sku,
            target_sizes,
            trust_product_url=trust_product_url,
        )

        if price is not None and status.startswith("Price verified"):
            verified_count += 1
        else:
            manual_check.append(site or domain_from_url(domain_url))

        raw_results.append({
            "price": price if price is not None else None,
            "row": [
                "",
                site or domain_from_url(domain_url),
                get_trust(site or domain_from_url(domain_url)),
                money(price, symbol),
                size,
                status,
                url,
                now,
            ],
        })

    raw_results.sort(
        key=lambda x: x["price"] if isinstance(x["price"], (int, float)) else 999999
    )

    final_rows = []
    rank = 1

    for item in raw_results:
        row = item["row"]

        if isinstance(item["price"], (int, float)) and item["price"] > 0:
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
    ]

    summary = (
        "📊 Sneaker Tracker V11.4\n\n"
        f"Siti controllati: {checked}\n"
        f"Product URL usati: {product_url_count}\n"
        f"Trovati via Google: {google_found_count}\n"
        f"Prezzi verificati: {verified_count}\n"
    )

    if verified_rows:
        best = verified_rows[0]["row"]

        details = (
            f"\n🥇 Miglior prezzo verificato\n"
            f"{best[1]} [{best[2]}] → {best[3]}\n"
            f"Size: {best[4]}\n"
            f"Status: {best[5]}\n"
            f"Link: {best[6]}"
        )

        others = []
        for item in verified_rows[1:telegram_top]:
            row = item["row"]
            others.append(f"{row[1]} [{row[2]}] → {row[3]}")

        if others:
            details += "\n\n📋 Altri prezzi verificati\n" + "\n".join(others)

        if manual_check:
            unique_manual = []
            seen = set()
            for site in manual_check:
                if site and site not in seen:
                    seen.add(site)
                    unique_manual.append(site)

            if unique_manual:
                details += (
                    "\n\n⚠️ Da controllare manualmente\n"
                    + "\n".join(unique_manual[:10])
                )

        send_telegram(summary + details)
    else:
        send_telegram(summary + "\nNessun prezzo verificato trovato oggi.")


if __name__ == "__main__":
    main()
