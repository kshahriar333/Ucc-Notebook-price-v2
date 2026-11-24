"""SKU-first retailer discovery and conservative product-page extraction."""
import re
import threading
from urllib.parse import urlparse, parse_qs, unquote, urljoin

import requests
from bs4 import BeautifulSoup

from match_engine import compare_products

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = (10, 20)
RETAILER_DOMAINS = {
    "Ryans": "ryans.com",
    "Star Tech": "startech.com.bd",
    "Techland": "techlandbd.com",
}
DIRECT_PRODUCT_URLS = {
    "UN.35MSI.02B": {
        "Ryans": "https://www.ryans.com/acer-aspire-15-as15-42-amd-ryzen-5-7430u-15.6-inch-fhd-laptop",
        "Star Tech": "https://www.startech.com.bd/acer-aspire-15-as15-42-r5-ryzen-5-15-inch-laptop",
        "Techland": "https://www.techlandbd.com/acer-aspire-15-as15-42-ryzen-5-7430u-156-inch-fhd-ips-laptop",
    }
}


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def extract_price(text):
    for pattern in (
        r"(?:৳|Tk\.?|BDT)\s*([\d,]+)",
        r"([\d,]+)\s*(?:৳|Tk\.?|BDT)",
    ):
        match = re.search(pattern, str(text or ""), re.I)
        if match:
            amount = int(match.group(1).replace(",", ""))
            if 5000 <= amount <= 2_000_000:
                return amount
    return None


def extract_first(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            return clean(match.group(1))
    return None


def extract_sku(text):
    return extract_first([
        r"(?:Part\s*(?:No|Number)|MPN|SKU)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9._-]{3,})",
        r"\b([A-Z]{2}\.[A-Z0-9]{2,12}\.[A-Z0-9]{2,12})\b",
    ], text)


def extract_specs(name, text):
    combined = clean(f"{name} {text}")
    model = extract_first([
        r"\b((?:AS|AN|TL|TMP|PHN|PH|A\d{3})\s?[A-Z0-9]{1,10}(?:-[A-Z0-9]{1,10}){0,2})\b",
        r"\b([A-Z]\d[A-Z]{2,7}(?:-[A-Z0-9]{2,8})?)\b",
    ], combined)
    cpu = extract_first([
        r"\b(Ryzen\s+AI\s+[579]\s+\d{3,4}[A-Z]{0,3})\b",
        r"\b(Ryzen\s+[3579]\s+\d{4,5}[A-Z]{0,3})\b",
        r"\b(Core\s+Ultra\s+[3579]\s+\d{3}[A-Z]{0,3})\b",
        r"\b(Core\s+i[3579]\s*[- ]?\s*\d{4,5}[A-Z]{0,3})\b",
        r"\b(Core\s+[3579]\s+\d{3,5}[A-Z]{0,3})\b",
        r"\b(Processor\s+N\d{3})\b",
    ], combined)
    ram = extract_first([
        r"\bRAM\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\bMemory\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\b(\d{1,2})\s*GB\s*(?:LPDDR[345X]\s*)?(?:RAM|Memory)\b",
        r"\b(\d{1,2})\s*GB\s*(?:LPDDR[345X]|DDR[345X])\b",
    ], combined)
    ddr = extract_first([r"\b((?:LPDDR[345X]|DDR[345X]))\b"], combined)
    ssd = extract_first([
        r"\b(\d+(?:\.\d+)?\s*TB)\b(?=.{0,60}\bSSD\b)",
        r"\b(\d{3,4})\s*GB\b(?=.{0,60}\bSSD\b)(?![\s\S]{0,35}\b(?:RAM|LPDDR[345X]|DDR[345X])\b)",
        r"\bSSD\)?\s*[|:=-]?\s*(\d+(?:\.\d+)?\s*(?:TB|GB))\b",
        r"\b(?:Storage(?:\s+Capacity)?|Solid-State\s+Drive)\s*[|:=-]?\s*(\d+(?:\.\d+)?\s*(?:TB|GB))\b",
        r"\bSSD\b.{0,40}?\b(\d{3,4})\s*GB\b",
    ], combined)
    return {
        "sku": extract_sku(text),
        "model": re.sub(r"\s+", "", model).upper() if model else None,
        "processor": cpu,
        "ram": f"{ram}GB" if ram else None,
        "ddr": ddr.upper() if ddr else None,
        "ssd": re.sub(r"\s+", "", ssd.upper()) if ssd else None,
    }


def parse_price_from_soup(soup, page_text, retailer):
    # Do not accept savings, EMI or FAQ prices as current selling prices.
    for selector in (
        '[itemprop="price"]',
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        ".price-new",
        ".product-price",
        ".special-price",
    ):
        for tag in soup.select(selector):
            raw = tag.get("content") or tag.get_text(" ", strip=True)
            price = extract_price(raw)
            if price:
                return price
    main = soup.select_one(
        "#content, main, .product-info, .product-details, "
        ".product-summary, .product-page"
    )
    if main:
        text = main.get_text(" ", strip=True)
        # Strip common savings labels before considering a fallback amount.
        text = re.sub(r"(?:Save|You Save)\s*(?:৳|Tk\.?|BDT)?\s*[\d,]+", "", text, flags=re.I)
        return extract_price(text)
    return None


def parse_product_page(retailer, url):
    expected_domain = RETAILER_DOMAINS[retailer]
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    final_host = urlparse(response.url).netloc.lower().removeprefix("www.")
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
        page_text, re.I
    ))
    stock_status = "OUT OF STOCK / DISCONTINUED" if unavailable else "CHECK PAGE"

    if retailer == "Star Tech":
        status_match = re.search(
            r"\bStatus\s*[:|]?\s*(In\s+Stock|Out\s+Of\s+Stock|Discontinued)\b",
            page_text, re.I
        )
        if not status_match or status_match.group(1).lower() != "in stock":
            unavailable = True
            stock_status = "OUT OF STOCK / STATUS NOT CONFIRMED"
        else:
            unavailable = False
            stock_status = "IN STOCK"
    elif unavailable:
        stock_status = "OUT OF STOCK / DISCONTINUED"
    else:
        stock_status = "STOCK NOT CONFIRMED"

    price = None if unavailable else parse_price_from_soup(soup, page_text, retailer)
    item = {
        "retailer": retailer,
        "name": name,
        "price": price,
        "stock_status": stock_status,
        "url": url,
        "page_text": page_text,
    }
    item.update(extract_specs(name, page_text))
    if price is None:
        item["price_note"] = "No reliable current selling price or confirmed availability."
    return item


def unwrap_search_url(href):
    parsed = urlparse(href)
    if parsed.path == "/url" and parsed.netloc.endswith("google.com"):
        query = parse_qs(parsed.query)
        return unquote((query.get("q") or query.get("url") or [""])[0])
    return href


SEARCH_TIMEOUT = (8, 12)
_SEARCH_FORM_CACHE = {}
_SEARCH_FORM_LOCK = threading.Lock()


def _get_search_form(domain):
    """Load and cache the retailer's search form once per workflow process."""
    if domain in _SEARCH_FORM_CACHE:
        return _SEARCH_FORM_CACHE[domain]
    with _SEARCH_FORM_LOCK:
        if domain in _SEARCH_FORM_CACHE:
            return _SEARCH_FORM_CACHE[domain]
        base = f"https://{domain}/"
        try:
            response = requests.get(base, headers=HEADERS, timeout=SEARCH_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            ranked = []
            for form in soup.find_all("form"):
                fields, search_field, score = {}, None, 0
                for item in form.select("input[name]"):
                    name = item.get("name", "").strip()
                    if not name:
                        continue
                    field_type = (item.get("type") or "").lower()
                    descriptor = " ".join((
                        name, item.get("placeholder", ""),
                        item.get("aria-label", ""), item.get("title", ""),
                    )).lower()
                    if field_type == "hidden":
                        fields[name] = item.get("value", "")
                    if field_type == "search":
                        search_field = name
                        score += 10
                    if any(word in descriptor for word in ("search", "keyword", "query")):
                        search_field = name
                        score += 8
                action = urljoin(response.url, form.get("action") or response.url)
                if urlparse(action).netloc.lower().removeprefix("www.") != domain:
                    continue
                if "search" in urlparse(action).path.lower():
                    score += 5
                if search_field and score >= 8:
                    ranked.append((score, action, (form.get("method") or "get").lower(), fields, search_field))
            ranked.sort(key=lambda item: item[0], reverse=True)
            result = ranked[0][1:] if ranked else None
        except requests.RequestException as exc:
            print(f"Search form discovery warning for {domain}: {type(exc).__name__}")
            result = None
        _SEARCH_FORM_CACHE[domain] = result
        return result


def search_result_urls(domain, query):
    """Search a retailer directly; product prices are read only from product pages."""
    urls = []
    session = requests.Session()
    session.headers.update(HEADERS)

    def collect_links(response):
        soup = BeautifulSoup(response.text, "html.parser")
        anchors = []
        for selector in (
            ".product-thumb a[href]", ".product-item a[href]",
            ".product-card a[href]", ".product-layout a[href]",
            "[class*='product'] a[href]",
        ):
            anchors.extend(soup.select(selector))
        if not anchors:
            anchors = soup.select("a[href]")
        for anchor in anchors:
            href = urljoin(response.url, anchor.get("href", ""))
            parsed = urlparse(href)
            host = parsed.netloc.lower().removeprefix("www.")
            path = parsed.path.lower().strip("/")
            if host != domain or not path:
                continue
            if any(term in path for term in (
                "search", "category", "brand", "account", "checkout",
                "compare", "wishlist", "login", "cart",
            )):
                continue
            candidate = f"https://{domain}/{path}"
            if candidate not in urls:
                urls.append(candidate)

    form = _get_search_form(domain)
    if form:
        action, method, template_fields, search_field = form
        fields = dict(template_fields)
        fields[search_field] = query
        try:
            if method == "post":
                response = session.post(action, data=fields, timeout=SEARCH_TIMEOUT)
            else:
                response = session.get(action, params=fields, timeout=SEARCH_TIMEOUT)
            response.raise_for_status()
            collect_links(response)
        except requests.RequestException as exc:
            print(f"Native search warning for {domain}: {type(exc).__name__}")

    # One known fallback route per retailer for JavaScript-only search boxes.
    if not urls:
        fallback = {
            "ryans.com": ("/search", {"keyword": query}),
            "startech.com.bd": ("/index.php", {"route": "product/search", "search": query}),
            "techlandbd.com": ("/search", {"search": query}),
        }.get(domain)
        if fallback:
            path, params = fallback
            try:
                response = session.get(urljoin(f"https://{domain}/", path),
                                       params=params, timeout=SEARCH_TIMEOUT)
                response.raise_for_status()
                collect_links(response)
            except requests.RequestException as exc:
                print(f"Search fallback warning for {domain}: {type(exc).__name__}")
    return urls[:2]


def search_one(product, retailer_name):
    sku = clean(product.get("sku")).upper()
    domain = RETAILER_DOMAINS[retailer_name]
    direct_url = DIRECT_PRODUCT_URLS.get(sku, {}).get(retailer_name)
    queries = []
    if sku:
        queries.append(sku)
    # Use model + CPU only if exact SKU search returns no usable candidate.
    fallback = " ".join(str(product.get(key) or "") for key in ("model", "processor"))
    if not fallback.strip():
        fallback = product.get("name", "")
    if fallback.strip():
        queries.append(fallback.strip())

    candidate_urls = []
    if direct_url:
        candidate_urls.append(direct_url)
    results, errors = [], []
    for query in queries:
        found = search_result_urls(domain, query) if not candidate_urls else []
        for url in found:
            if url not in candidate_urls:
                candidate_urls.append(url)
        for url in candidate_urls:
            if any(item.get("url") == url for item in results):
                continue
            try:
                candidate = parse_product_page(retailer_name, url)
                match = compare_products(product, candidate)
                candidate["match"] = match
                results.append(candidate)
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:130]}")
        # Prefer exact SKU + fully matching specs; if SKU not indexed, only
        # a fully verified specification match can be used for price tracking.
        verified = [
            item for item in results
            if item["match"]["price_comparison_allowed"]
        ]
        if verified:
            break
        candidate_urls = []

    results.sort(key=lambda item: (
        not item["match"]["sku_match"],
        not item["match"]["price_comparison_allowed"],
        item.get("price") is None,
    ))
    if results:
        return {
            "status": "PRODUCT PAGE FOUND",
            "results": results[:1],
            "errors": errors[:3],
        }
    return {
        "status": "NO VERIFIED PRODUCT FOUND",
        "results": [],
        "errors": errors[:3] or [f"No indexed product page found for {sku or product.get('name')}."],
    }


def search_all_retailers(product):
    return {
        retailer: search_one(product, retailer)
        for retailer in RETAILER_DOMAINS
    }
