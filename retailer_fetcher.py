"""Retailer search and product-page parser for UCC laptop price comparison."""
import re
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from match_engine import compare_products

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = (8, 15)
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
    return re.sub(r"\\s+", " ", str(value or "")).strip()


def normalize(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def extract_price(value):
    text = str(value or "")
    for pattern in (r"(?:৳|Tk\\.?|BDT)\\s*([\\d,]+)", r"([\\d,]+)\\s*(?:৳|Tk\\.?|BDT)"):
        match = re.search(pattern, text, re.I)
        if match:
            amount = int(match.group(1).replace(",", ""))
            if 5000 <= amount <= 2_000_000:
                return amount
    # Product price meta tags commonly contain a plain numeric value.
    if re.fullmatch(r"\\s*\\d{4,7}(?:\\.\\d+)?\\s*", text):
        amount = int(float(text))
        if 5000 <= amount <= 2_000_000:
            return amount
    return None


def first_match(patterns, text):
    for pattern in patterns:
        found = re.search(pattern, text or "", re.I)
        if found:
            return clean(found.group(1))
    return None


def extract_specs(name, text):
    combined = clean(name + " " + text)
    sku = first_match([
        r"(?:SKU|Part\\s*(?:No|Number)|MPN|Product\\s*Code)\\s*[:#\\-]?\\s*([A-Z0-9][A-Z0-9._\\-]{4,})",
        r"\\b([A-Z]{2}\\.[A-Z0-9]{2,12}\\.[A-Z0-9]{2,12})\\b",
    ], text)
    model = first_match([
        r"\\b((?:AS|AN|TL|TMP|PHN|PH|A\\d{3})\\s?[A-Z0-9]{1,10}(?:-[A-Z0-9]{1,10}){0,2})\\b",
        r"\\b([A-Z]\\d[A-Z]{2,7}(?:-[A-Z0-9]{2,8})?)\\b",
    ], combined)
    cpu = first_match([
        r"\\b(Ryzen\\s+AI\\s+[579]\\s+\\d{3,4}[A-Z]{0,3})\\b",
        r"\\b(Ryzen\\s+[3579]\\s+\\d{4,5}[A-Z]{0,3})\\b",
        r"\\b(Core\\s+Ultra\\s+[3579]\\s+\\d{3}[A-Z]{0,3})\\b",
        r"\\b(Core\\s+i[3579]\\s*[- ]?\\s*\\d{4,5}[A-Z]{0,3})\\b",
        r"\\b(Core\\s+[3579]\\s+\\d{3,5}[A-Z]{0,3})\\b",
        r"\\b(Processor\\s+N\\d{3})\\b",
    ], combined)
    ram = first_match([
        r"\\bRAM\\s*[:\\-]?\\s*(\\d{1,2})\\s*GB\\b",
        r"\\bMemory\\s*[:\\-]?\\s*(\\d{1,2})\\s*GB\\b",
        r"\\b(\\d{1,2})\\s*GB\\s*(?:LPDDR[345X]\\s*)?(?:RAM|Memory)\\b",
        r"\\b(\\d{1,2})\\s*GB\\s*(?:LPDDR[345X]|DDR[345X])\\b",
    ], combined)
    ddr = first_match([r"\\b((?:LPDDR[345X]|DDR[345X]))\\b"], combined)
    ssd = first_match([
        r"\\b(\\d+(?:\\.\\d+)?\\s*TB)\\b(?=.{0,60}\\bSSD\\b)",
        r"\\bSSD\\)?\\s*[:|=-]?\\s*(\\d+(?:\\.\\d+)?\\s*(?:TB|GB))\\b",
        r"\\b(?:Storage(?:\\s+Capacity)?|Solid-State\\s+Drive)\\s*[:|=-]?\\s*(\\d{3,4}\\s*GB)\\b",
    ], combined)
    return {
        "sku": sku.upper() if sku else None,
        "model": re.sub(r"\\s+", "", model).upper() if model else None,
        "processor": cpu,
        "ram": f"{ram}GB" if ram else None,
        "ddr": ddr.upper() if ddr else None,
        "ssd": re.sub(r"\\s+", "", ssd.upper()) if ssd else None,
    }


def parse_price(soup):
    # Prefer structured current product-price fields, not EMI or crossed-out price.
    selectors = [
        '[itemprop="price"]',
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        'meta[name="twitter:data1"]',
        ".price-new",
        ".special-price",
        ".product-price",
        ".product__price",
        ".price-current",
    ]
    for selector in selectors:
        for tag in soup.select(selector):
            raw = tag.get("content") or tag.get_text(" ", strip=True)
            amount = extract_price(raw)
            if amount:
                return amount

    # Retailer product-page main content fallback.
    main = soup.select_one("#content, main, .product-info, .product-details, .product-summary")
    if main:
        for selector in (".price", "[class*='price-current']", "[class*='price-new']"):
            for tag in main.select(selector):
                amount = extract_price(tag.get_text(" ", strip=True))
                if amount:
                    return amount
    return None


def parse_product_page(retailer, url):
    domain = RETAILER_DOMAINS[retailer]
    parsed = urlparse(url)
    if parsed.netloc.lower().removeprefix("www.") != domain:
        raise ValueError("Candidate URL is not on the retailer domain.")
    if any(part in parsed.path.lower() for part in (
        "/information/", "/category/", "/brand/", "/search", "/account",
        "/checkout", "/cart", "/compare", "/wishlist",
    )):
        raise ValueError("Rejected non-product URL.")

    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    final = urlparse(response.url)
    if final.netloc.lower().removeprefix("www.") != domain:
        raise ValueError("Unexpected redirect outside retailer domain.")
    if any(part in final.path.lower() for part in ("/information/", "/search", "/category/")):
        raise ValueError("Search redirected to a non-product page.")

    soup = BeautifulSoup(response.text, "html.parser")
    heading = soup.find("h1")
    name = clean(heading.get_text(" ", strip=True) if heading else "")
    if not name:
        raise ValueError("Product page has no H1 title.")
    page_text = clean(soup.get_text(" ", strip=True))

    stock_match = re.search(
        r"\\b(?:Stock|Availability|Status)\\s*[:：]?\\s*(In\\s+Stock|Out\\s+Of\\s+Stock|Available|Unavailable|Discontinued)\\b",
        page_text, re.I
    )
    stock_status = stock_match.group(1).upper() if stock_match else "NOT CONFIRMED"
    explicitly_unavailable = bool(stock_match and re.search(
        r"out\\s+of\\s+stock|unavailable|discontinued",
        stock_match.group(1), re.I
    ))
    price = None if explicitly_unavailable else parse_price(soup)

    item = {
        "retailer": retailer,
        "name": name,
        "price": price,
        "stock_status": stock_status,
        "url": response.url,
        "page_text": page_text,
    }
    item.update(extract_specs(name, page_text))
    if price is None:
        item["price_note"] = "Current product price could not be read reliably."
    return item


def search_endpoint(retailer, query):
    # Known native search routes. Avoid selecting arbitrary homepage forms:
    # Star Tech has unrelated forms that can lead to /information/offer.
    if retailer == "Ryans":
        return "https://www.ryans.com/search", {"keyword": query}
    if retailer == "Star Tech":
        return "https://www.startech.com.bd/index.php", {
            "route": "product/search", "search": query
        }
    return "https://www.techlandbd.com/search", {"search": query}


def collect_product_links(retailer, response, query):
    domain = RETAILER_DOMAINS[retailer]
    soup = BeautifulSoup(response.text, "html.parser")
    cards = []
    for selector in (
        ".product-thumb", ".product-layout", ".product-item",
        ".product-card", ".product-box", ".product-grid .item",
    ):
        cards.extend(soup.select(selector))

    anchors = []
    if cards:
        for card in cards:
            anchors.extend(card.select("a[href]"))
    else:
        # Search results may not use standard cards. Only accept links whose
        # path looks like a product slug and whose visible text is relevant.
        anchors = soup.select("a[href]")

    query_norm = normalize(query)
    query_words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 2]
    ranked = {}
    for anchor in anchors:
        href = urljoin(response.url, anchor.get("href", ""))
        parsed = urlparse(href)
        if parsed.netloc.lower().removeprefix("www.") != domain:
            continue
        path = parsed.path.lower().strip("/")
        if not path or any(term in path for term in (
            "information", "offer", "search", "category", "brand",
            "account", "checkout", "cart", "compare", "wishlist",
            "blog", "news", "contact", "about",
        )):
            continue
        if path.count("/") > 1 or path.count("-") < 2:
            continue
        card = anchor.find_parent(class_=re.compile(r"product|item|card", re.I))
        context = clean((card or anchor.parent).get_text(" ", strip=True))
        title = clean(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
        evidence = normalize(" ".join((title, context, path)))
        score = 0
        if query_norm and query_norm in evidence:
            score += 100
        score += sum(3 for word in query_words if word in evidence.lower())
        # For SKU lookup, require SKU text in the card/search result before
        # accepting a candidate. Model search can use title/spec token overlap.
        if re.fullmatch(r"[A-Z0-9._-]{5,}", query, re.I) and "." in query:
            if normalize(query) not in evidence:
                continue
        if score <= 0:
            continue
        clean_url = f"https://{domain}/{path}"
        ranked[clean_url] = max(score, ranked.get(clean_url, 0))

    return [url for url, _ in sorted(ranked.items(), key=lambda item: item[1], reverse=True)[:5]]


def search_result_urls(retailer, query):
    endpoint, params = search_endpoint(retailer, query)
    response = requests.get(endpoint, params=params, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return collect_product_links(retailer, response, query)


def search_one(product, retailer):
    domain = RETAILER_DOMAINS[retailer]
    sku = clean(product.get("sku")).upper()
    direct = DIRECT_PRODUCT_URLS.get(sku, {}).get(retailer)
    queries = []
    if sku:
        queries.append(sku)
    fallback = " ".join(str(product.get(k) or "") for k in ("model", "processor"))
    if not fallback.strip():
        fallback = product.get("name", "")
    if fallback.strip():
        queries.append(clean(fallback))

    results, errors = [], []
    candidate_urls = [direct] if direct else []
    for query in queries:
        if not candidate_urls:
            try:
                candidate_urls = search_result_urls(retailer, query)
            except requests.RequestException as exc:
                errors.append(f"Search {query}: {type(exc).__name__}: {str(exc)[:100]}")
                candidate_urls = []
        for url in candidate_urls:
            if any(x.get("url") == url for x in results):
                continue
            try:
                item = parse_product_page(retailer, url)
                item["match"] = compare_products(product, item)
                results.append(item)
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:110]}")
        verified = [x for x in results if x["match"]["price_comparison_allowed"]]
        if verified:
            break
        candidate_urls = []

    results.sort(key=lambda x: (
        not x["match"]["sku_match"],
        not x["match"]["price_comparison_allowed"],
        x.get("price") is None,
    ))
    if results:
        return {"status": "PRODUCT PAGE FOUND", "results": results[:3], "errors": errors[:3]}
    return {
        "status": "NO VERIFIED PRODUCT FOUND",
        "results": [],
        "errors": errors[:3] or [f"No matching product page found for {sku or product.get('name')}."],
    }


def search_all_retailers(product):
    return {retailer: search_one(product, retailer) for retailer in RETAILER_DOMAINS}
