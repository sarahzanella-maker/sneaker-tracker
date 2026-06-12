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

MIN_VISIBLE_PRICE = 350
MAX_PRICE = 3000

# Approximate FX rates to EUR. Used only to make non-EUR sites comparable.
# Keep conservative; actual checkout totals may differ.
FX_TO_EUR = {
    "EUR": 1.00,
    "GBP": 1.18,
    "USD": 0.92,
    "INR": 0.0107,
}


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
    "resellpiacenza": "C",

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
    if "₹" in text or "INR" in text.upper():
        return "₹"

    return "€"


def parse_numeric_amount(value):
    """Parse a numeric price without applying min/max filters."""
    if value is None:
        return None

    text = str(value).replace("\xa0", " ").strip()
    if not re.search(r"\d", text):
        return None

    text = re.sub(r"(?i)\b(EUR|USD|GBP|INR)\b", "", text)
    text = text.replace("€", "").replace("$", "").replace("£", "").replace("₹", "")
    text = text.strip()

    # Keep only digits and separators.
    match = re.search(r"\d[\d.,]*", text)
    if not match:
        return None

    num = match.group(0)

    if "," in num and "." in num:
        # Last separator is decimal; the other is thousands.
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        parts = num.split(",")
        if len(parts[-1]) == 2:
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    else:
        # Dot may be decimal or thousands. If exactly three digits after it and no decimals, treat as thousands.
        parts = num.split(".")
        if len(parts) > 1 and len(parts[-1]) == 3:
            num = num.replace(".", "")

    try:
        return float(num)
    except Exception:
        return None


def convert_to_eur(amount, currency):
    if amount is None:
        return None

    cur = str(currency or "EUR").upper().strip()
    rate = FX_TO_EUR.get(cur)
    if rate is None:
        return None

    value = float(amount) * rate
    if MIN_VISIBLE_PRICE <= value <= MAX_PRICE:
        return round(value, 2)
    return None


def price_value_in_eur(value, currency="EUR", min_price=80):
    amount = parse_numeric_amount(value)
    if amount is None:
        return None

    cur = str(currency or "EUR").upper().strip()
    if cur == "EUR":
        if min_price <= amount <= MAX_PRICE:
            return amount
        return None

    return convert_to_eur(amount, cur)


def parse_price(value, min_price=80):
    amount = parse_numeric_amount(value)
    if amount is None:
        return None

    if min_price <= amount <= MAX_PRICE:
        return amount
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


def _contains_blocked_term(text):
    text = f" {str(text).lower()} "

    for term in BLOCKED_TERMS:
        t = term.strip().lower()
        if not t:
            continue

        # Avoid rejecting marketplace names like SnkrDunk when only the shoe model "Dunk" should be blocked.
        if t == "dunk":
            if re.search(r"\bdunk\b", text):
                return True
            continue

        if " " in t or "-" in t or t.startswith("iq"):
            if t in text:
                return True
        else:
            if re.search(rf"\b{re.escape(t)}\b", text):
                return True

    return False


def product_score(text, sku):
    text = f" {str(text).lower()} "
    score = 0

    if sku.lower() in text:
        score += 5
    if "travis" in text:
        score += 1
    if "scott" in text:
        score += 1
    if "tropical" in text:
        score += 1
    if "pink" in text:
        score += 1
    if "jordan" in text:
        score += 1
    if "air jordan" in text:
        score += 1
    if "sail" in text:
        score += 1

    return score


def title_is_valid(text, sku):
    text = str(text)

    if _contains_blocked_term(text):
        return False

    return product_score(text, sku) >= 4


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

    queries = [
        f'site:{domain} "{sku}"',
        f'site:{domain} "{search_term}"',
        f'site:{domain} "Travis Scott" "Tropical Pink"',
    ]

    for query in queries:
        try:
            results = serpapi_google(query, max_results)
        except Exception:
            continue

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
            value = parse_price(tag.get("content") or tag.get("value"), min_price=80)
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
                value = parse_price(offers.get("price"), min_price=80)
                if value:
                    prices.append(value)

            elif isinstance(offers, list):
                for offer in offers:
                    if isinstance(offer, dict):
                        value = parse_price(offer.get("price"), min_price=80)
                        if value:
                            prices.append(value)

    if not prices:
        return None

    return min(prices)

def iter_json_objects(data):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from iter_json_objects(value)
    elif isinstance(data, list):
        for item in data:
            yield from iter_json_objects(item)


def load_jsonld_objects(soup):
    objects = []

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects.extend(list(iter_json_objects(data)))

    return objects


def target_size_patterns(target_sizes):
    patterns = []

    for size in target_sizes:
        s = str(size).strip().lower().replace(",", ".")
        if not s:
            continue

        if s == "36":
            patterns.extend([r"\beu\s*36\b", r"\b36\b", r"\buk\s*3\.5\b", r"\bus\s*4\b", r"\b3\.5\b"])
        elif s == "36.5":
            patterns.extend([r"\beu\s*36\.5\b", r"\b36\.5\b", r"\buk\s*4\b", r"\bus\s*4\.5\b", r"\b4\.5y\b"])
        elif s == "4y":
            patterns.extend([r"\bus\s*4y\b", r"\b4y\b", r"\bus\s*4\b"])
        elif s == "4.5y":
            patterns.extend([r"\bus\s*4\.5y\b", r"\b4\.5y\b", r"\bus\s*4\.5\b"])
        else:
            patterns.append(rf"\b{re.escape(s)}\b")

    # Prefer explicit EU patterns over loose numeric ones.
    return list(dict.fromkeys(patterns))


def text_matches_target_size(text, target_sizes):
    normalized = str(text or "").lower().replace(",", ".")
    for pattern in target_size_patterns(target_sizes):
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return True
    return False


def extract_jsonld_size_price(soup, target_sizes):
    """Read JSON-LD variants and return a EUR price only for target sizes."""
    candidates = []

    for obj in load_jsonld_objects(soup):
        if not isinstance(obj, dict):
            continue

        size_text = " ".join([
            str(obj.get("size", "")),
            str(obj.get("name", "")),
            str(obj.get("sku", "")),
        ])

        if not text_matches_target_size(size_text, target_sizes):
            continue

        offers = obj.get("offers")
        offer_list = offers if isinstance(offers, list) else [offers]

        for offer in offer_list:
            if not isinstance(offer, dict):
                continue

        currency = (offer.get("priceCurrency") or "EUR").upper()
        availability = str(offer.get("availability", "")).lower()

        if availability and "instock" not in availability and "in stock" not in availability:
            continue

        possible_prices = [
            offer.get("lowPrice"),
            offer.get("price"),
        ]

        for price in possible_prices:
            value = price_value_in_eur(price, currency, min_price=MIN_VISIBLE_PRICE)

            if value is not None:
                candidates.append(value)

    if not candidates:
        return None

    return min(candidates)


def extract_cdcrew_price(text):
    m = re.search(
        r'€\s?(\d{2,5}[.,]\d{2})\s?EUR',
        str(text),
        re.IGNORECASE
    )

    if not m:
        return None

    return parse_price(m.group(1), min_price=350)


def extract_laced_price(text):
    matches = re.findall(
        r'(\d{3,5})\s?€',
        str(text),
        re.IGNORECASE
    )

    prices = []

    for p in matches:
        try:
            value = float(p)

            if 350 <= value <= 3000:
                prices.append(value)

        except Exception:
            pass

    if not prices:
        return None

    return max(prices)

def _parse_visible_amount(value):
    return parse_price(value, min_price=MIN_VISIBLE_PRICE)


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
    raw_text = str(text)

    blocked_context = [
        "klarna", "rate", "rata", "rate da", "installment", "installments",
        "paypal", "scalapay", "afterpay", "split in", "pay in 3", "3 rate",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, raw_text, flags=re.IGNORECASE):
            raw_value = match.group(1)
            value = _parse_visible_amount(raw_value)

            if value is None:
                continue
            if not (MIN_VISIBLE_PRICE <= value <= MAX_PRICE):
                continue

            context = raw_text[
                max(0, match.start() - 80):
                min(len(raw_text), match.end() + 80)
            ].lower()

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

    return sorted(set(variants), key=len, reverse=True)


def extract_visible_price_for_sizes(text, target_sizes):
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

            if 0 <= distance_after <= 180:
                candidates.append((0, distance_after, price["value"]))
            elif 0 <= distance_before <= 80:
                candidates.append((1, distance_before, price["value"]))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    return candidates[0][2]


def try_shopify_product_json(url):
    """
    Best-effort Shopify endpoint.
    Many Shopify product pages expose /products/handle.js with variants/prices.
    """
    try:
        parsed = urlparse(clean_url(url))
        path = parsed.path

        if "/products/" not in path:
            return None

        handle = path.split("/products/", 1)[1].split("/")[0]
        if not handle:
            return None

        json_url = f"{parsed.scheme}://{parsed.netloc}/products/{handle}.js"
        response = requests.get(json_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)

        if response.status_code >= 400:
            return None

        data = response.json()
        variants = data.get("variants", [])
        prices = []

        for variant in variants:
            price = variant.get("price")
            if price is None:
                continue

            # Shopify often returns cents.
            try:
                price_float = float(price)
                if price_float > 3000:
                    price_float = price_float / 100
                if MIN_VISIBLE_PRICE <= price_float <= MAX_PRICE:
                    prices.append(price_float)
            except Exception:
                pass

        if not prices:
            return None

        return min(prices)

    except Exception:
        return None


def _normalize_embedded_price(value):
    parsed = parse_price(value)
    if parsed is not None and MIN_VISIBLE_PRICE <= parsed <= MAX_PRICE:
        return parsed

    try:
        raw = str(value).strip()
        if re.fullmatch(r"\d{5,6}", raw):
            cents = float(raw) / 100
            if MIN_VISIBLE_PRICE <= cents <= MAX_PRICE:
                return cents
    except Exception:
        pass

    return None


def extract_embedded_price(html):
    """Fallback for JS state objects such as __NEXT_DATA__, initial state, or data-price fields."""
    raw = str(html)
    patterns = [
        r"[\"'](?:price|currentPrice|salePrice|regularPrice|amount|priceAmount|finalPrice)[\"']\s*:\s*[\"']?(\d{2,6}(?:[.,]\d{2})?)[\"']?",
        r"[\"'](?:value|centAmount)[\"']\s*:\s*[\"']?(\d{4,6})[\"']?",
        r"(?:data-price|data-product-price|variant-price)[=:\s\"']+(\d{2,6}(?:[.,]\d{2})?)",
    ]

    blocked_context = [
        "klarna", "rate", "rata", "installment", "installments", "paypal", "scalapay",
    ]

    candidates = []
    for pattern in patterns:
        for match in re.finditer(pattern, raw, flags=re.IGNORECASE):
            value = _normalize_embedded_price(match.group(1))
            if value is None:
                continue

            context = raw[max(0, match.start() - 80):min(len(raw), match.end() + 80)].lower()
            if any(word in context for word in blocked_context):
                continue

            candidates.append(value)

    if not candidates:
        return None

    return min(candidates)


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

        if "novelship" in url.lower():
            print("\n===== NOVELSHIP HTML DEBUG =====")
            print(html[:30000])

        if "klekt" in url.lower():
            print("\n===== KLEKT HTML DEBUG =====")
            print(html[:30000])

        if "crepdogcrew" in url.lower():
            print("\n===== CDCREW HTML DEBUG =====")
            print(html[:30000])

        if "snkrdunk" in url.lower():
            print("\n===== SNKRDUNK HTML DEBUG =====")
            print(html[:30000])

        if not trust_product_url and not title_is_valid(text[:6000], sku):
            return None, detect_currency(html), "Rejected - product text not confirmed", "To verify"

        symbol = "€"

        price = extract_jsonld_size_price(soup, target_sizes)
        price_source = "jsonld-size"

        if price is None:
            price = extract_structured_price(soup)
            price_source = "structured"

        if price is None:
            shopify_price = try_shopify_product_json(url)

            if shopify_price is not None:
                price = shopify_price
                price_source = "shopify-json"

        # Domain-specific fallback for Crepdog Crew visible INR price.
        # Kept after JSON-LD because JSON-LD is size-specific when available.
        if price is None and "crepdogcrew" in url.lower():
            cd_price = extract_cdcrew_price(text)

            if cd_price is not None:
                price = cd_price
                price_source = "cdcrew"

        # General visible fallback: only accepts prices close to target sizes.
        if price is None:
            visible_price = extract_visible_price_for_sizes(text, target_sizes)

            if visible_price is not None:
                price = visible_price
                price_source = "visible-size"

        EMBEDDED_ALLOWED = [
            "zneakerz",
        ]

        if price is None and any(site in url.lower() for site in EMBEDDED_ALLOWED):
            embedded_price = extract_embedded_price(html)

            if embedded_price is not None:
                price = embedded_price
                price_source = "embedded"

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
        "📊 Sneaker Tracker V13\n\n"
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
