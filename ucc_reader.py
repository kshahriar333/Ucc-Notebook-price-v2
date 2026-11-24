"""Read Acer laptop listings and specifications from UCC public pages."""
import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

CATEGORY_URL = "https://www.ucc.com.bd/acer-brand-laptops"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = (10, 25)


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def get_soup(url, session=None):
    client = session or requests.Session()
    response = client.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def first_match(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            return clean(match.group(1))
    return None


def extract_price(value):
    text = str(value or "")
    for pattern in (r"(?:৳|Tk\.?|BDT)\s*([\d,]+)", r"([\d,]+)\s*(?:৳|Tk\.?|BDT)"):
        match = re.search(pattern, text, re.I)
        if match:
            amount = int(match.group(1).replace(",", ""))
            if 5000 <= amount <= 2_000_000:
                return amount
    # JSON-LD often stores a plain numeric price.
    if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", text):
        amount = int(float(text))
        if 5000 <= amount <= 2_000_000:
            return amount
    return None


def extract_page_price(soup):
    for tag in soup.select(
        '[itemprop="price"], meta[property="product:price:amount"], '
        'meta[property="og:price:amount"], .price, .price-new, .product-price'
    ):
        amount = extract_price(tag.get("content") or tag.get_text(" ", strip=True))
        if amount:
            return amount

    def walk(obj):
        if isinstance(obj, list):
            for item in obj:
                result = walk(item)
                if result:
                    return result
        elif isinstance(obj, dict):
            offers = obj.get("offers")
            if offers:
                result = walk(offers)
                if result:
                    return result
            result = extract_price(obj.get("price"))
            if result:
                return result
            if obj.get("@graph"):
                return walk(obj["@graph"])
        return None

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            result = walk(json.loads(script.string or script.get_text()))
            if result:
                return result
        except (ValueError, TypeError):
            continue
    return None


def extract_specs(name, page_text):
    text = clean(f"{name} {page_text}")
    # Prefer the value explicitly attached to the SKU/Part No label.
    # Product descriptions can mention older/different part numbers earlier.
    sku = first_match([
        r"(?:SKU|Part\s*(?:No|Number)|MPN|Product\s*Code)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9._-]{4,})",
        r"\b([A-Z]{2}\.[A-Z0-9]{2,12}\.[A-Z0-9]{2,12})\b",
    ], text)

    cpu = first_match([
        r"\b(Ryzen\s+[3579]\s+\d{4,5}[A-Z]{0,3})\b",
        r"\b(Core\s+Ultra\s+[3579]\s+\d{3}[A-Z]{0,3})\b",
        r"\b(Core\s+i[3579]\s*[- ]?\s*\d{4,5}[A-Z]{0,3})\b",
        r"\b(Core\s+[3579]\s+\d{3,5}[A-Z]{0,3})\b",
        r"\b(Processor\s+N\d{3})\b",
    ], text)

    model = first_match([
        r"\b((?:AS|AN|A\d{3}|TL|TMP|SF|PH)\s?[A-Z0-9]{1,8}-[A-Z0-9]{1,8})\b",
    ], text)
    if model:
        model = re.sub(r"\s+", "", model).upper()

    # RAM must be tied to RAM/memory wording or an explicit DDR memory phrase.
    ram = first_match([
        r"\bRAM\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\bMemory\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\b(\d{1,2})\s*GB\s*(?:DDR[345X]\s*)?(?:RAM|Memory)\b",
        r"\b(\d{1,2})\s*GB\s*DDR[345X]\b",
    ], text)
    ddr = first_match([r"\b(DDR[345X])\b"], text)

    ssd = first_match([
        r"\b(\d+(?:\.\d+)?\s*(?:TB|GB))(?:\s+\w+){0,4}\s+SSD\b",
        r"\bSSD\)?\s*[:\-]?\s*(\d+(?:\.\d+)?\s*(?:TB|GB))\b",
        r"\b(?:Storage(?:\s+Capacity)?|Solid-State\s+Drive)\s*[:\-]?\s*(\d+(?:\.\d+)?\s*(?:TB|GB))\b",
    ], text)
    if ssd:
        ssd = re.sub(r"\s+", "", ssd.upper())

    return {
        "sku": sku.upper() if sku else None,
        "model": model,
        "processor": cpu,
        "ram": f"{ram}GB" if ram else None,
        "ddr": ddr.upper() if ddr else None,
        "ssd": ssd,
    }


def read_product(url, session=None):
    soup = get_soup(url, session)
    heading = soup.find("h1")
    name = clean(heading.get_text(" ", strip=True) if heading else "")
    if not name and soup.title:
        name = clean(soup.title.get_text(" ", strip=True))
    page_text = clean(soup.get_text(" ", strip=True))
    product = {
        "retailer": "UCC",
        "name": name,
        "url": url,
        "price": extract_page_price(soup),
        "page_text": page_text,
        "status": "OK",
    }
    product.update(extract_specs(name, page_text))
    return product


def discover_product_urls(category_url=CATEGORY_URL, session=None):
    soup = get_soup(category_url, session)
    urls = set()
    # Restrict extraction to likely product-card containers first.
    card_selectors = [
        ".product-thumb", ".product-layout", ".product-item",
        ".product-card", ".product-grid .item", ".product-list .item",
        "[class*='product-item']", "[class*='product-thumb']",
    ]
    anchors = []
    for selector in card_selectors:
        for card in soup.select(selector):
            anchors.extend(card.select("a[href]"))
    if not anchors:
        anchors = soup.select("a[href]")

    for anchor in anchors:
        href = urljoin(category_url, anchor.get("href", ""))
        parsed = urlparse(href)
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.lower().strip("/")
        if host != "ucc.com.bd" or not path:
            continue
        if any(part in path for part in (
            "acer-brand-laptops", "category", "brand", "search", "blog",
            "news", "account", "checkout", "compare", "wishlist",
        )):
            continue
        label = clean(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
        if label and not any(word in label.lower() for word in (
            "acer", "aspire", "nitro", "predator", "travel", "swift",
        )):
            # A product-card image anchor can have no title; retain it only if path hints Acer.
            if not any(word in path for word in ("acer", "aspire", "nitro", "predator", "travel", "swift")):
                continue
        urls.add(href.split("#")[0])
    return sorted(urls)


def get_acer_products(category_url=CATEGORY_URL):
    session = requests.Session()
    urls = discover_product_urls(category_url, session)
    products, errors = [], []
    for url in urls:
        try:
            item = read_product(url, session)
            combined = (item.get("name", "") + " " + url).lower()
            if any(word in combined for word in ("acer", "aspire", "nitro", "predator", "travel", "swift")):
                products.append(item)
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
            print("UCC product fetch failed:", url, str(exc)[:160])
    # Keep errors visible to the caller/report rather than silently claiming complete coverage.
    if not products:
        raise RuntimeError(
            f"No Acer product pages could be parsed from {category_url}. "
            f"Discovered URLs={len(urls)}; errors={len(errors)}. "
            "The category page may be blocked or its HTML layout may have changed."
        )
    return products
