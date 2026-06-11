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

RESULT_HEADERS = [
    "Rank", "Date", "Site", "SKU", "Size", "Type", "Price", "Shipping", "Total",
    "Availability", "Stock", "URL", "Source Type", "Notes"
]

REQUIRED_SOURCE_COLUMNS = [
    "SITO", "URL", "ATTIVO", "Reliability", "Notes", "Search Query",
    "Search Mode", "Search Type", "Search Priority", "Enabled",
    "Source Origin", "First Seen"
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
    "hypebeast.com", "nicekicks.com", "complex.com", "google.com"
]

NON_PRODUCT_PATHS = [
    "/blog", "/blogs", "/news", "/release", "/releases",
    "/raffle", "/magazine", "/editorial", "/article", "/articles"
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


def col_letter(n):
    result = ""
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


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


def path_from_url(url):
    try:
        return urlparse(url).path.lower()
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
        value = float(match.group(0))
        if 80 <= value <= 3000:
            return value
        return None
    except Exception:
        return None


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


def extract_structured_price(soup):
    price_candidates = []

    for attrs in [
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
        {"name": "twitter:data1"},
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
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            offers = obj.get("offers")

            if isinstance(offers, dict):
                val = parse_price(offers.get("price"))
                if val:
                    price_candidates.append(val)

            elif isinstance(offers, list):
                for offer in offers:
                    if isinstance(offer, dict):
                        val = parse_price(offer.get("price"))
                        if val:
                            price_candidates.append(val)

    if not price_candidates:
        return None

    return min(price_candidates)


def verify_page(url, sku):
    if not url or "google.com" in url:
        return None, "€", "Price not verified - Google link", ""

    if is_non_product_url(url):
        return None, "€", "Price not verified - non-product page", ""

    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )

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
        price = extract_structured_price(soup)

        if price is not None:
            return price, symbol, f"Price verified structured - {size_note}", size_value

        return None, symbol, f"Price not found structured - {size_note}", size_value

    except Exception as e:
        return None, "€", f"Price not verified - {str(e)[:60]}", ""


def clear_results(ws):
    ws.clear()
    ws.update(values=[RESULT_HEADERS], range_name="A1:N1")


def write_rows(ws, rows):
    if not rows:
        return

    clean = [[str(c) if c is not None else "" for c in row] for row in rows]

    start = len(ws.get_all_values()) + 1
    end = start + len(clean) - 1

    if ws.row_count < end:
        ws.add_rows(end - ws.row_count)

    ws.update(
        values=clean,
        range_name=f"A{start}:N{end}",
        value_input_option="USER_ENTERED",
    )


def ensure_source_columns(ws):
    values = ws.get_all_values()
    headers = values[0] if values else []

    changed = False
    for col in REQUIRED_SOURCE_COLUMNS:
        if col not in headers:
            headers.append(col)
            changed = True

    if changed or not values:
        end_col = col_letter(len(headers))
        ws.update(values=[headers], range_name=f"A1:{end_col}1")

    return headers


def append_discovered_source(ws, headers, domain, first_seen):
    site_name = domain.split(".")[0].replace("-", " ").title()
    base_url = f"https://{domain}"

    row_data = {
        "SITO": site_name,
        "URL": base_url,
        "ATTIVO": "NO",
        "Reliability": "",
        "Notes": "Auto-discovered by tracker. Review manually before enabling.",
        "Search Query": "IQ7604-101",
        "Search Mode": "GOOGLE_SITE_SEARCH",
        "Search Type": "SEARCH",
        "Search Priority": "LOW",
        "Enabled": "FALSE",
        "Source Origin": "Auto-discovered",
        "First Seen": first_seen,
    }

    row = [row_data.get(h, "") for h in headers]
    ws.append_row(row, value_input_option="USER_ENTERED")


def discover_new_sources(sources_ws, headers, existing_domains, sku, search_term, today):
    discovered = []

    queries = [
        f'"{sku}"',
        f'"{search_term}" "IQ7604-101"',
        f'"Travis Scott" "Tropical Pink" "36.5"',
    ]

    for query in queries:
        try:
            results = serpapi_google(query, 10)
        except Exception:
            continue

        for result in results:
            title = result.get("title", "")
            link = extract_real_link(result.get("link", ""))
            snippet = result.get("snippet", "")

            if is_non_product_url(link):
                continue

            text = f"{title} {snippet} {link}"

            if not title_is_valid(text, sku):
                continue

            domain = domain_from_url(link)

            if not domain:
                continue

            if domain in existing_domains:
                continue

            existing_domains.add(domain)
            append_discovered_source(sources_ws, headers, domain, today)

            discovered.append({
                "domain": domain,
                "url": f"https://{domain}",
                "title": title,
            })

    return discovered


def main():
    sheet = connect_sheet()

    sources_ws = sheet.worksheet(SOURCE_SHEET_NAME)
    settings_ws = sheet.worksheet(SETTINGS_SHEET_NAME)
    results_ws = sheet.worksheet(RESULTS_SHEET_NAME)

    source_headers = ensure_source_columns(sources_ws)
    clear_results(results_ws)

    settings = read_settings(settings_ws)

    sku = settings.get("SKU", "IQ7604-101")
    search_term = settings.get("Search Term", "Travis Scott Tropical Pink")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    source_rows = sources_ws.get_all_records()

    raw_rows = []
    checked = 0
    no_result = 0
    verified_count = 0
    existing_domains = set()

    for source in source_rows:
        site_url = normalize(source.get("URL", ""))
        domain = domain_from_url(site_url)
        if domain:
            existing_domains.add(domain)

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
            raw_rows.append({
                "sort": 999999,
                "verified": False,
                "row": [
                    "", now, site_name or domain, sku, "", "", "N/D", "N/D", "N/D",
                    f"Search error: {e}", "", site_url, "V10.1 site search", query,
                ],
            })
            continue

        best_link = ""
        best_title = ""

        for result in results:
            title = result.get("title", "")
            link = extract_real_link(result.get("link", ""))
            snippet = result.get("snippet", "")
            text = f"{title} {snippet} {link}"

            if is_non_product_url(link):
                continue

            if title_is_valid(text, sku):
                best_link = link
                best_title = title
                break

        if not best_link:
            no_result += 1
            raw_rows.append({
                "sort": 999999,
                "verified": False,
                "row": [
                    "", now, site_name or domain, sku, "To verify", "To verify",
                    "N/D", "N/D", "N/D", "No product page found",
                    "", site_url, "V10.1 site search", query,
                ],
            })
            continue

        price, symbol, status, size_value = verify_page(best_link, sku)
        total = price

        verified = price is not None and status.startswith("Price verified structured")
        if verified:
            verified_count += 1

        raw_rows.append({
            "sort": total if total is not None else 999999,
            "verified": verified,
            "row": [
                "", now, site_name or domain, sku, size_value or "To verify", "GS/Adult",
                money(price, symbol), "N/D", money(total, symbol),
                status, "", best_link, "V10.1 site-by-site", best_title,
            ],
        })

    raw_rows.sort(key=lambda x: x["sort"])

    ranked_rows = []
    rank = 1

    for item in raw_rows:
        row = item["row"]

        if item["verified"]:
            row[0] = rank
            rank += 1
        else:
            row[0] = ""

        ranked_rows.append(row)

    write_rows(results_ws, ranked_rows)

    verified_rows = [i for i in raw_rows if i["verified"]]

    new_sources = discover_new_sources(
        sources_ws=sources_ws,
        headers=source_headers,
        existing_domains=existing_domains,
        sku=sku,
        search_term=search_term,
        today=today,
    )

    summary = (
        "📊 Sneaker Tracker V10.1\n\n"
        f"Siti controllati: {checked}\n"
        f"Senza pagina prodotto: {no_result}\n"
        f"Prezzi verificati: {verified_count}\n"
        f"Nuovi siti trovati: {len(new_sources)}\n"
    )

    if verified_rows:
        best = verified_rows[0]["row"]

        other_lines = []
        for item in verified_rows[1:6]:
            r = item["row"]
            other_lines.append(f"{r[2]} → {r[8]}")

        details = (
            f"\n🥇 Miglior prezzo verificato\n"
            f"{best[2]} → {best[8]}\n"
            f"Price: {best[6]}\n"
            f"Size: {best[4]}\n"
            f"Status: {best[9]}\n"
            f"Link: {best[11]}"
        )

        if other_lines:
            details += "\n\n📋 Altri prezzi verificati\n" + "\n".join(other_lines)

    else:
        details = "\nNessun prezzo verificato trovato oggi."

    if new_sources:
        new_lines = []
        for s in new_sources[:10]:
            new_lines.append(f"🆕 {s['domain']}\n{s['url']}")

        details += (
            "\n\n🆕 Nuovi siti aggiunti a Sneaker Sources "
            "(ATTIVO=NO, Enabled=FALSE)\n\n"
            + "\n\n".join(new_lines)
        )

    send_telegram(summary + details)


if __name__ == "__main__":
    main()
