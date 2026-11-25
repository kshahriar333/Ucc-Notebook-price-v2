"""Retailer search and product-page parser for UCC laptop price comparison."""
import re
import threading
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
CATALOGUE_URLS = {
    "Ryans": "https://www.ryans.com/category/laptop-all-laptop/",
    "Star Tech": "https://www.startech.com.bd/laptop-notebook?limit=75&page=1&sort=pd.name",
    "Techland": "https://www.techlandbd.com/shop-laptop-computer/",
}
_CATALOGUE_CACHE = {}
_CATALOGUE_LOCKS = {name: threading.Lock() for name in RETAILER_DOMAINS}
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


def extract_price(value):
    text = str(value or "")
    for pattern in (r"(?:৳|Tk\.?|BDT)\s*([\d,]+)", r"([\d,]+)\s*(?:৳|Tk\.?|BDT)"):
        found = re.search(pattern, text, re.I)
        if found:
            amount = int(found.group(1).replace(",", ""))
            if 5000 <= amount <= 2_000_000:
                return amount
    if re.fullmatch(r"\s*\d{4,7}(?:\.\d+)?\s*", text):
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
        r"(?:SKU|Part\s*(?:No|Number)|MPN|Product\s*Code)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9._\-]{4,})",
        r"\b([A-Z]{2}\.[A-Z0-9]{2,12}\.[A-Z0-9]{2,12})\b",
    ], text)
    model = first_match([
        r"\b((?:AS|AN|TL|TMP|PHN|PH|A\d{3})\s?[A-Z0-9]{1,10}(?:-[A-Z0-9]{1,10}){0,2})\b",
        r"\b([A-Z]\d[A-Z]{2,7}(?:-[A-Z0-9]{2,8})?)\b",
    ], combined)
    cpu = first_match([
        r"\b(Ryzen\s+AI\s+[579]\s+\d{3,4}[A-Z]{0,3})\b",
        r"\b(Ryzen\s+[3579]\s+\d{4,5}[A-Z]{0,3})\b",
        r"\b(Core\s+Ultra\s+[3579]\s+\d{3}[A-Z]{0,3})\b",
        r"\b(Core\s+i[3579]\s*[- ]?\s*\d{4,5}[A-Z]{0,3})\b",
        r"\b(Core\s+[3579]\s+\d{3,5}[A-Z]{0,3})\b",
        r"\b(Processor\s+N\d{3})\b",
    ], combined)
    ram = first_match([
        r"\bRAM\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\bMemory\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\b(\d{1,2})\s*GB\s*(?:LPDDR[345X]\s*)?(?:RAM|Memory)\b",
        r"\b(\d{1,2})\s*GB\s*(?:LPDDR[345X]|DDR[345X])\b",
    ], combined)
    ddr = first_match([r"\b((?:LPDDR[345X]|DDR[345X]))\b"], combined)
    ssd = first_match([
        r"\b(\d+(?:\.\d+)?\s*TB)\b(?=.{0,60}\bSSD\b)",
        r"\bSSD\)?\s*[:|=-]?\s*(\d+(?:\.\d+)?\s*(?:TB|GB))\b",
        r"\b(?:Storage(?:\s+Capacity)?|Solid-State\s+Drive)\s*[:|=-]?\s*(\d{3,4}\s*GB)\b",
    ], combined)
    return {
        "sku": sku.upper() if sku else None,
        "model": re.sub(r"\s+", "", model).upper() if model else None,
        "processor": cpu,
        "ram": f"{ram}GB" if ram else None,
        "ddr": ddr.upper() if ddr else None,
        "ssd": re.sub(r"\s+", "", ssd.upper()) if ssd else None,
    }


def parse_price(soup):
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
            amount = extract_price(tag.get("content") or tag.get_text(" ", strip=True))
            if amount:
                return amount
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
        raise ValueError("Candidate URL is not on retailer domain.")
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
        raise ValueError("Search redirected to non-product page.")

    soup = BeautifulSoup(response.text, "html.parser")
    heading = soup.find("h1")
    name = clean(heading.get_text(" ", strip=True) if heading else "")
    if not name:
        raise ValueError("Product page has no H1 title.")
    page_text = clean(soup.get_text(" ", strip=True))
    stock_match = re.search(
        r"\b(?:Stock|Availability|Status)\s*[:：]?\s*(In\s+Stock|Out\s+Of\s+Stock|Available|Unavailable|Discontinued)\b",
        page_text, re.I
    )
    stock_status = stock_match.group(1).upper() if stock_match else "NOT CONFIRMED"
    unavailable = bool(stock_match and re.search(
        r"out\s+of\s+stock|unavailable|discontinued", stock_match.group(1), re.I
    ))
    price = None if unavailable else parse_price(soup)
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
        anchors = soup.select("a[href]")

    query_norm = normalize(query)
    query_words = [word.lower() for word in re.findall(r"[A-Za-z0-9]+", query) if len(word) > 2]
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
        score = 100 if query_norm and query_norm in evidence else 0
        score += sum(3 for word in query_words if word in evidence.lower())
        if re.fullmatch(r"[A-Z0-9._-]{5,}", query, re.I) and "." in query:
            if query_norm not in evidence:
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




def _catalogue_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=1,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(("GET",)))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    return session


def _is_product_url(retailer, url):
    domain = RETAILER_DOMAINS[retailer]
    parsed = urlparse(url)
    if parsed.netloc.lower().removeprefix("www.") != domain:
        return False
    path = parsed.path.lower().strip("/")
    if len(path) < 8 or path.count("/") > 1:
        return False
    rejected = (
        "category", "search", "brand", "information", "offer", "account",
        "checkout", "cart", "compare", "wishlist", "shop-laptop-computer",
        "laptop-notebook", "laptop-all-laptop", "page", "blog", "news",
    )
    return not any(term in path for term in rejected)


def _catalogue_page_url(retailer, page):
    base = CATALOGUE_URLS[retailer]
    if retailer == "Ryans":
        return base if page == 1 else f"{base}?page={page}"
    if retailer == "Star Tech":
        return re.sub(r"([?&])page=\d+", rf"\g<1>page={page}", base)
    if page == 1:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}page={page}"


def _load_retailer_catalogue_unlocked(retailer):
    if retailer in _CATALOGUE_CACHE:
        return _CATALOGUE_CACHE[retailer]
    session = _catalogue_session()
    records = {}
    expected = None
    for page in range(1, 13):
        url = _catalogue_page_url(retailer, page)
        try:
            response = session.get(url, timeout=(10, 25))
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"{retailer} catalogue page {page} failed: {type(exc).__name__}: {exc}")
            if page == 1:
                raise
            break
        soup = BeautifulSoup(response.text, "html.parser")
        page_text = clean(soup.get_text(" ", strip=True))
        count = re.search(r"(?:Showing\s+\d+\s+to\s+\d+\s+of\s+|(\d+)\s+Products?\s+found)", page_text, re.I)
        if count:
            try:
                expected = max(expected or 0, int(count.group(1) or count.group(2)))
            except (TypeError, ValueError):
                pass

        cards = []
        for selector in (
            ".product-item", ".product-card", ".product-layout", ".product-thumb",
            ".product-box", ".item-product", ".product-grid-item", "li.product",
            "[class*='product-card']", "[class*='product-item']",
        ):
            found = soup.select(selector)
            if len(found) >= 3:
                cards = found
                break
        anchors = []
        if cards:
            for card in cards:
                anchors.extend(card.select("a[href]"))
        else:
            anchors = soup.select("main a[href], #content a[href], .products a[href]")

        added = 0
        for anchor in anchors:
            href = urljoin(response.url, anchor.get("href", ""))
            if not _is_product_url(retailer, href):
                continue
            card = anchor.find_parent(class_=re.compile(r"product|item|card", re.I))
            if card is None:
                card = anchor.parent
            context = clean(card.get_text(" ", strip=True))
            title = clean(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
            if len(title) < 8:
                title = clean(anchor.get("title", "") or card.get("title", ""))
            if not title:
                # use the most informative text fragment, not generic Buy Now labels
                title = context[:240]
            price = extract_price(context)
            stock = "OUT OF STOCK" if re.search(r"out\s+of\s+stock|unavailable", context, re.I) else "NOT CONFIRMED"
            item = {"retailer": retailer, "name": title, "page_text": context,
                    "url": href.split("#")[0], "price": price, "stock_status": stock}
            item.update(extract_specs(title, context))
            old = records.get(item["url"])
            if old is None or len(item["page_text"]) > len(old["page_text"]):
                records[item["url"]] = item
                added += 1
        print(f"{retailer} catalogue page {page}: {added} product cards; unique {len(records)}")
        if added == 0 and page > 1:
            break
        # Stop when pagination says the whole catalogue has been collected.
        if expected and len(records) >= expected:
            break

    if not records:
        raise RuntimeError(f"No product cards extracted from {retailer} laptop catalogue.")
    if expected and len(records) < expected:
        print(f"{retailer} catalogue appears partial: expected {expected}, extracted {len(records)}")
    _CATALOGUE_CACHE[retailer] = list(records.values())
    return _CATALOGUE_CACHE[retailer]


def _load_retailer_catalogue(retailer):
    # Only one worker downloads a retailer's listing pages; the other workers
    # wait and reuse the same catalogue snapshot.
    with _CATALOGUE_LOCKS[retailer]:
        return _load_retailer_catalogue_unlocked(retailer)


def _candidate_score(ucc_product, candidate):
    ucc_sku = normalize(ucc_product.get("sku"))
    candidate_sku = normalize(candidate.get("sku"))
    if ucc_sku and candidate_sku and ucc_sku == candidate_sku:
        return 1000
    ucc_model = normalize(ucc_product.get("model") or "")
    candidate_model = normalize(candidate.get("model") or "")
    ucc_cpu = normalize(ucc_product.get("processor") or "")
    candidate_cpu = normalize(candidate.get("processor") or "")
    if not ucc_model or not candidate_model or ucc_model != candidate_model:
        return 0
    if not ucc_cpu or not candidate_cpu or ucc_cpu != candidate_cpu:
        return 0
    comparison = compare_products(ucc_product, candidate)
    if not comparison.get("price_comparison_allowed"):
        return 0
    return 100 + len(comparison.get("matched_specs", []))


def search_one(product, retailer):
    errors, verified = [], []
    try:
        catalogue = _load_retailer_catalogue(retailer)
        ranked = []
        for candidate in catalogue:
            score = _candidate_score(product, candidate)
            if score > 0:
                ranked.append((score, candidate))
        ranked.sort(key=lambda row: row[0], reverse=True)

        # Candidate list entries must match exact SKU or the full model+CPU
        # with compatible available configuration data.
        for score, candidate in ranked[:5]:
            try:
                detail = parse_product_page(retailer, candidate["url"])
                detail["match"] = compare_products(product, detail)
                if detail["match"]["price_comparison_allowed"] and detail.get("price") is not None:
                    verified.append(detail)
            except Exception as exc:
                errors.append(f"{candidate['url']}: {type(exc).__name__}: {str(exc)[:100]}")
        if verified:
            verified.sort(key=lambda item: (not item["match"]["sku_match"], item["price"] is None))
            return {"status": "VERIFIED CATALOGUE MATCH", "results": verified[:3], "errors": errors[:3]}
    except Exception as exc:
        errors.append(f"Catalogue: {type(exc).__name__}: {str(exc)[:140]}")

    # Do not fall back to broad site search results that can redirect to
    # unrelated landing pages. An unverified item must never become a price alert.
    return {
        "status": "NO VERIFIED PRODUCT FOUND",
        "results": [],
        "errors": errors[:3] or ["No exact SKU or fully verified model/configuration match."],
    }


def search_all_retailers(product):
    return {retailer: search_one(product, retailer) for retailer in RETAILER_DOMAINS}
