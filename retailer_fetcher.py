"""Search public retailer search pages; never bypass access controls."""
import re
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

RETAILERS = {
    "Ryans": {
        "domain": "ryans.com",
        "search_url": "https://www.ryans.com/search?q=",
    },
    "Star Tech": {
        "domain": "startech.com.bd",
        "search_url": "https://www.startech.com.bd/search?search=",
    },
    "Techland": {
        "domain": "techlandbd.com",
        "search_url": "https://www.techlandbd.com/index.php?route=product/search&search=",
    },
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = (10, 22)


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def extract_price(value):
    text = str(value or "")
    for pattern in (r"(?:৳|Tk\.?|BDT)\s*([\d,]+)", r"([\d,]+)\s*(?:৳|Tk\.?|BDT)"):
        match = re.search(pattern, text, re.I)
        if match:
            amount = int(match.group(1).replace(",", ""))
            if 5000 <= amount <= 2_000_000:
                return amount
    if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", text):
        amount = int(float(text))
        if 5000 <= amount <= 2_000_000:
            return amount
    return None


def extract_sku(text):
    patterns = [
        r"\b([A-Z]{2}\.[A-Z0-9]{2,12}\.[A-Z0-9]{2,12})\b",
        r"(?:Part\s*(?:No|Number)|SKU|Product\s*Code)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9._-]{4,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            return match.group(1).strip().upper()
    return None


def fetch_soup(url, session):
    response = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def is_product_url(url, domain):
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    return (
        host == domain
        and path not in ("", "/")
        and not any(token in path for token in (
            "/search", "/category", "/brand", "/blog", "/news",
            "/account", "/checkout", "/compare", "/wishlist",
        ))
    )


def search_result_links(search_url, domain, session):
    soup = fetch_soup(search_url, session)
    links = []
    seen = set()
    # Prioritize links inside product result cards.
    selectors = [
        ".product-thumb", ".product-layout", ".product-item",
        ".product-card", ".product-grid .item", "[class*='product-item']",
    ]
    anchors = []
    for selector in selectors:
        for card in soup.select(selector):
            anchors.extend(card.select("a[href]"))
    if not anchors:
        anchors = soup.select("a[href]")

    for anchor in anchors:
        url = urljoin(search_url, anchor.get("href", ""))
        if not is_product_url(url, domain) or url in seen:
            continue
        title = clean(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
        if len(title) < 4:
            continue
        seen.add(url)
        links.append({"name": title, "url": url})
    return links[:12]


def parse_product_page(url, session):
    soup = fetch_soup(url, session)
    heading = soup.find("h1")
    name = clean(heading.get_text(" ", strip=True) if heading else "")
    if not name and soup.title:
        name = clean(soup.title.get_text(" ", strip=True))
    text = clean(soup.get_text(" ", strip=True))
    price = None
    for tag in soup.select(
        '[itemprop="price"], meta[property="product:price:amount"], '
        'meta[property="og:price:amount"], .price, .price-new, .product-price'
    ):
        price = extract_price(tag.get("content") or tag.get_text(" ", strip=True))
        if price:
            break
    sku = extract_sku(text)
    return {
        "retailer": urlparse(url).netloc,
        "name": name,
        "sku": sku,
        "price": price,
        "url": url,
        "page_text": text,
    }


def search_one(product, retailer_name):
    config = RETAILERS[retailer_name]
    session = requests.Session()
    search_terms = []
    if product.get("sku"):
        search_terms.append(("SKU", product["sku"]))
    if product.get("model"):
        search_terms.append(("MODEL", product["model"]))
    # Use a short, recognizable model/title only as last fallback.
    if product.get("name"):
        search_terms.append(("NAME", product["name"][:75]))

    collected, visited, errors = [], set(), []
    for search_type, term in search_terms:
        search_url = config["search_url"] + quote(term)
        try:
            links = search_result_links(search_url, config["domain"], session)
            candidates = []
            for link in links:
                if link["url"] in visited:
                    continue
                visited.add(link["url"])
                try:
                    item = parse_product_page(link["url"], session)
                    item["search_type"] = search_type
                    candidates.append(item)
                except requests.RequestException as exc:
                    errors.append(str(exc)[:140])

            if search_type == "SKU" and product.get("sku"):
                exact_sku = [
                    item for item in candidates
                    if item.get("sku") and normalize(item["sku"]) == normalize(product["sku"])
                ]
                if exact_sku:
                    return {"status": "OK", "results": exact_sku, "errors": []}
            collected.extend(candidates)
            if candidates and search_type != "SKU":
                break
        except requests.RequestException as exc:
            errors.append(str(exc)[:140])

    unique = {item["url"]: item for item in collected}
    if unique:
        status = "OK"
    elif errors:
        status = "REQUEST ERROR / POSSIBLY BLOCKED"
    else:
        status = "NO RESULTS"
    return {
        "status": status,
        "results": list(unique.values())[:8],
        "errors": errors[:3],
    }


def search_all_retailers(product):
    return {name: search_one(product, name) for name in RETAILERS}
