"""Direct-URL retailer price reader for UCC Acer notebook monitoring.

This version deliberately uses verified product-detail URLs instead of generic
search pages. Add a retailer URL to DIRECT_PRODUCT_URLS for each new UCC SKU.
It never treats search/category/navigation pages as product pages and never
compares a price when required specifications are missing or different.
"""
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = (12, 25)

# UCC page's visible SKU is UN.35MSI.02B. All three supplied pages show
# the same part number/configuration (Ryans and Techland SKU; Star Tech MPN).
DIRECT_PRODUCT_URLS = {
    "UN.35MSI.02B": {
        "Ryans": (
            "https://www.ryans.com/"
            "acer-aspire-15-as15-42-amd-ryzen-5-7430u-15.6-inch-fhd-laptop"
        ),
        "Star Tech": (
            "https://www.startech.com.bd/"
            "acer-aspire-15-as15-42-r5-ryzen-5-15-inch-laptop"
        ),
        "Techland": (
            "https://www.techlandbd.com/"
            "acer-aspire-15-as15-42-ryzen-5-7430u-156-inch-fhd-ips-laptop"
        ),
    }
}

RETAILER_DOMAINS = {
    "Ryans": "ryans.com",
    "Star Tech": "startech.com.bd",
    "Techland": "techlandbd.com",
}


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def extract_price(text):
    text = str(text or "")
    patterns = (
        r"(?:৳|Tk\.?|BDT)\s*([\d,]+)",
        r"([\d,]+)\s*(?:৳|Tk\.?|BDT)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            amount = int(match.group(1).replace(",", ""))
            if 5000 <= amount <= 2_000_000:
                return amount
    return None


def extract_first(patterns, text, flags=re.I):
    for pattern in patterns:
        match = re.search(pattern, text or "", flags)
        if match:
            return clean(match.group(1))
    return None


def extract_sku(text):
    # Labelled part numbers take priority over incidental codes in descriptions.
    return extract_first([
        r"(?:Part\s*(?:No|Number)|MPN|SKU)\s*[:#\-]?\s*"
        r"([A-Z0-9][A-Z0-9._-]{4,})",
        r"\b([A-Z]{2}\.[A-Z0-9]{2,12}\.[A-Z0-9]{2,12})\b",
    ], text)


def extract_specs(name, text):
    combined = clean(f"{name} {text}")
    model = extract_first([
        r"\b((?:AS|AN|A\d{3}|TL|TMP|SF|PH)\s?[A-Z0-9]{1,8}-[A-Z0-9]{1,8})\b"
    ], combined)
    cpu = extract_first([
        r"\b(Ryzen\s+[3579]\s+\d{4,5}[A-Z]{0,3})\b",
        r"\b(Core\s+Ultra\s+[3579]\s+\d{3}[A-Z]{0,3})\b",
        r"\b(Core\s+i[3579]\s*[- ]?\s*\d{4,5}[A-Z]{0,3})\b",
    ], combined)
    ram = extract_first([
        r"\bRAM\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\bMemory\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\b(\d{1,2})\s*GB\s*(?:DDR[345X]\s*)?(?:RAM|Memory)\b",
        r"\b(\d{1,2})\s*GB\s*DDR[345X]\b",
    ], combined)
    ddr = extract_first([r"\b(DDR[345X])\b"], combined)
    ssd = extract_first([
        r"\b(\d+(?:\.\d+)?\s*(?:TB|GB))(?:\s+\w+){0,4}\s+SSD\b",
        r"\bSSD\)?\s*[|:=-]?\s*(\d+(?:\.\d+)?\s*(?:TB|GB))\b",
        r"\b(?:Storage(?:\s+Capacity)?|Solid-State\s+Drive)\s*[|:=-]?\s*(\d+(?:\.\d+)?\s*(?:TB|GB))\b",
        r"\b(\d{3,4})\s*GB\b(?=.{0,60}\bSSD\b)",
        r"\bSSD\b.{0,40}?\b(\d{3,4})\s*GB\b",
    ], combined)
    if model:
        model = re.sub(r"\s+", "", model).upper()
    return {
        "sku": extract_sku(text),
        "model": model,
        "processor": cpu,
        "ram": f"{ram}GB" if ram else None,
        "ddr": ddr.upper() if ddr else None,
        "ssd": re.sub(r"\s+", "", ssd.upper()) if ssd else None,
    }


def parse_price_from_soup(soup, page_text, allow_fallback=True):
    # Prefer the labelled current selling price before generic price classes.
    labelled_patterns = (
        r"(?:Discount|Special)\s*Price\s*(?:৳|Tk\.?|BDT)\s*[\d,]+",
    )
    for pattern in labelled_patterns:
        match = re.search(pattern, page_text or "", re.I)
        if match:
            price = extract_price(match.group(0))
            if price:
                return price

    selectors = [
        '[itemprop="price"]',
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        ".price-new",
        ".product-price",
        ".special-price",
    ]
    for selector in selectors:
        for tag in soup.select(selector):
            raw = tag.get("content") or tag.get_text(" ", strip=True)
            price = extract_price(raw)
            if price:
                return price

    # Generic .price elements can be "Save ৳7,500", so avoid them.
    if allow_fallback:
        main = soup.select_one(
            "#content, main, .product-info, .product-details, "
            ".product-summary, .product-page"
        )
        if main:
            return extract_price(main.get_text(" ", strip=True))
    return None

def parse_product_page(retailer, url):
    expected_domain = RETAILER_DOMAINS[retailer]
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()

    final_host = response.url.split("/")[2].lower().removeprefix("www.")
    if final_host != expected_domain:
        raise ValueError(f"Unexpected redirect host: {final_host}")

    soup = BeautifulSoup(response.text, "html.parser")
    heading = soup.find("h1")
    name = clean(heading.get_text(" ", strip=True) if heading else "")
    if not name:
        raise ValueError("Product page has no H1 title.")

    page_text = clean(soup.get_text(" ", strip=True))
    unavailable = bool(re.search(
        r"\b(out\s+of\s+stock|discontinued|not\s+available)\b",
        page_text,
        re.I,
    ))
    stock_status = "OUT OF STOCK / DISCONTINUED" if unavailable else "CHECK PAGE"
    if retailer == "Star Tech":
        status_match = re.search(
            r"\bStatus\s*[:|]?\s*(In\s+Stock|Out\s+Of\s+Stock|Discontinued)\b",
            page_text,
            re.I,
        )
        if not status_match or status_match.group(1).lower() != "in stock":
            unavailable = True
            stock_status = "OUT OF STOCK / STATUS NOT CONFIRMED"
        else:
            unavailable = False
            stock_status = "IN STOCK"
    price = None if unavailable else parse_price_from_soup(
        soup,
        page_text,
        allow_fallback=True,
    )

    bad_titles = ("login", "cart", "offer", "pc builder", "category", "search")
    if any(term in name.lower() for term in bad_titles):
        raise ValueError(f"Not a product-detail page: {name}")

    item = {
        "retailer": retailer,
        "name": name,
        "price": price,
        "stock_status": stock_status,
        "url": url,
        "page_text": page_text,
    }
    item.update(extract_specs(name, page_text))
    if price is None and not unavailable:
        item["price_note"] = "No reliable current selling price found on the page."
    elif price is None and unavailable:
        item["price_note"] = "Page is marked unavailable; FAQ or EMI amounts are not treated as selling price."
    return item

def search_one(product, retailer_name):
    sku = clean(product.get("sku")).upper()
    retailer_urls = DIRECT_PRODUCT_URLS.get(sku, {})
    url = retailer_urls.get(retailer_name)

    if not url:
        return {
            "status": "NO VERIFIED DIRECT URL CONFIGURED",
            "results": [],
            "errors": [
                f"No verified {retailer_name} product URL is configured for SKU {sku or 'UNKNOWN'}."
            ],
        }

    try:
        item = parse_product_page(retailer_name, url)
        return {
            "status": "OK - VERIFIED DIRECT PRODUCT URL",
            "results": [item],
            "errors": [],
        }
    except Exception as exc:
        return {
            "status": "DIRECT PRODUCT PAGE ERROR",
            "results": [],
            "errors": [f"{type(exc).__name__}: {str(exc)[:220]}"],
        }


def search_all_retailers(product):
    return {
        retailer: search_one(product, retailer)
        for retailer in RETAILER_DOMAINS
    }
