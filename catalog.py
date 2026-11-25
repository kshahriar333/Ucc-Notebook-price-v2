"""Discover UCC Acer laptops and exact-SKU pages from the three Acer categories."""
import json
import re
import time
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

from monitor import HEADERS, norm, check_site, visible_text
from spec_matcher import extract_identity as extract_laptop_identity, compare_laptops

UCC_CATEGORY = "https://www.ucc.com.bd/laptops"
COMPETITOR_CATEGORIES = {
    "Ryans": "https://www.ryans.com/category/all-laptop-acer",
    "Star Tech": "https://www.startech.com.bd/acer-laptop",
    "Techland": "https://www.techlandbd.com/shop-laptop-computer/brand-laptops/shop-acer-laptop",
}
HOSTS = {
    "UCC": "ucc.com.bd",
    "Ryans": "ryans.com",
    "Star Tech": "startech.com.bd",
    "Techland": "techlandbd.com",
}
TIMEOUT = (12, 25)
MAX_CATEGORY_PAGES = 30
CARD_SELECTORS = (
    ".product-thumb", ".product-layout", ".product-item", ".product-card",
    ".product-grid .item", ".product-list .item", "[class*='product-item']",
    "[class*='product-card']",
)
EXCLUDED_PATHS = (
    "category", "brand", "search", "account", "checkout", "compare",
    "wishlist", "blog", "news", "contact", "information", "cart",
    "shop-laptop-computer", "all-laptop-acer", "acer-laptop",
)
KNOWN_URLS = {
    "UN35MSI02B": {
        "Ryans": "https://www.ryans.com/acer-aspire-15-as15-42-amd-ryzen-5-7430u-15.6-inch-fhd-laptop",
        "Star Tech": "https://www.startech.com.bd/acer-aspire-15-as15-42-r5-ryzen-5-15-inch-laptop",
        "Techland": "https://www.techlandbd.com/acer-aspire-15-as15-42-ryzen-5-7430u-156-inch-fhd-ips-laptop",
    }
}


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def get(session, url):
    response = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


def page_url(base, page):
    if page == 1:
        return base
    parsed = urlparse(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def is_product_path(url, host):
    parsed = urlparse(url)
    if parsed.netloc.lower().removeprefix("www.") != host:
        return False
    path = parsed.path.strip("/").lower()
    if not path or path.count("/") > 2:
        return False
    if any(part in path for part in EXCLUDED_PATHS):
        return False
    if path in ("laptops", "acer-brand-laptops"):
        return False
    return True


def listing_links(html, listing_url, host):
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for selector in CARD_SELECTORS:
        cards = soup.select(selector)
        if cards:
            break

    anchors = []
    if cards:
        for card in cards:
            anchors.extend(card.select("a[href]"))
    else:
        anchors = soup.select("a[href]")

    links = set()
    for anchor in anchors:
        href = anchor.get("href", "").strip()
        if not href:
            continue
        url = urljoin(listing_url, href).split("#", 1)[0]
        if is_product_path(url, host):
            links.add(url)
    return links


def discover_listing_products(session, category_url, site):
    host = HOSTS[site]
    found = set()
    for page in range(1, MAX_CATEGORY_PAGES + 1):
        url = page_url(category_url, page)
        html = get(session, url)
        batch = listing_links(html, url, host)
        new_links = batch - found
        found.update(batch)
        print(f"{site} category page {page}: {len(batch)} links, {len(new_links)} new")
        if page > 1 and not new_links:
            break
        time.sleep(0.25)
    return sorted(found)


def extract_identity(html, url):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = clean(
        heading.get_text(" ", strip=True) if heading
        else (soup.title.get_text(" ", strip=True) if soup.title else url)
    )
    text = clean(soup.get_text(" ", strip=True))

    brand_values = []
    for tag in soup.select('[itemprop="brand"], meta[property="product:brand"], meta[name="brand"]'):
        brand_values.extend([tag.get("content", ""), tag.get_text(" ", strip=True)])
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                brand = item.get("brand")
                if isinstance(brand, dict):
                    brand_values.append(brand.get("name", ""))
                elif isinstance(brand, str):
                    brand_values.append(brand)
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))

    sku = None
    patterns = (
        r"(?:SKU|MPN|Part\s*(?:No\.?|Number|#)|Product\s*Code)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9._/\-]{3,})",
        r"\b([A-Z]{2}\.[A-Z0-9]{2,12}\.[A-Z0-9]{2,12})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            sku = match.group(1).strip(".")
            break
    is_acer = "acer" in title.lower() or any("acer" in str(v).lower() for v in brand_values)
    return title, sku, is_acer


def product_records(session, urls, site):
    records = []
    for index, url in enumerate(urls, 1):
        try:
            html = get(session, url)
            title, sku, is_acer = extract_identity(html, url)
            if is_acer:
                identity = extract_laptop_identity(title, visible_text(html))
                records.append({"name": title, "sku": sku, "identity": identity.to_dict(), "url": url})
        except Exception as exc:
            print(f"{site} detail skipped: {url} ({type(exc).__name__}: {str(exc)[:100]})")
        if index % 20 == 0:
            print(f"{site}: inspected {index}/{len(urls)} product pages")
        time.sleep(0.12)
    return records


def identity_from_dict(data):
    from spec_matcher import LaptopIdentity
    return LaptopIdentity(**{k: data.get(k) for k in LaptopIdentity.__dataclass_fields__})


def find_verified_match(session, site, reference, index):
    ref_identity = identity_from_dict(reference["identity"])
    for record in index.get(site, []):
        candidate = identity_from_dict(record["identity"])
        decision = compare_laptops(ref_identity, candidate)
        if decision["matched"]:
            print(f"    {site}: {decision['method']} -> {record['url']}")
            return record["url"], decision["method"]
    return "", "NO_MATCH"


def build_catalog():
    with requests.Session() as session:
        session.headers.update(HEADERS)

        # UCC's all-laptop category is the source, but only Acer product details
        # are retained. Z-series boards and every non-Acer product are excluded.
        ucc_links = discover_listing_products(session, UCC_CATEGORY, "UCC")
        print("UCC listing product links:", len(ucc_links))
        ucc_records = product_records(session, ucc_links, "UCC")
        products_by_sku = {}
        for record in ucc_records:
            sku = record.get("sku")
            key = norm(sku) if sku else "NOSKU-" + norm(record["url"])
            products_by_sku.setdefault(key, {
                "id": key, "name": record["name"], "sku": sku,
                "identity": record["identity"],
                "urls": {"UCC": record["url"], "Ryans": "", "Star Tech": "", "Techland": ""},
                "match_methods": {"UCC": "UCC_SOURCE", "Ryans": "", "Star Tech": "", "Techland": ""},
            })
        products = list(products_by_sku.values())
        print("UCC Acer products discovered:", len(products))
        if not products:
            raise RuntimeError("No Acer laptop with a readable SKU was found on UCC. Existing state was not replaced.")

        # Build each competitor index only from the Acer category URL supplied
        # by the user. No generic laptop searches and no non-Acer category scans.
        competitor_index = {}
        for site, category in COMPETITOR_CATEGORIES.items():
            links = discover_listing_products(session, category, site)
            print(f"{site} Acer listing product links:", len(links))
            competitor_index[site] = product_records(session, links, site)
            print(f"{site} Acer product details inspected:", len(competitor_index[site]))

        for number, product in enumerate(products, 1):
            print(f"[{number}/{len(products)}] {product['name']} | SKU {product.get('sku') or 'not listed'}")
            for site in ("Ryans", "Star Tech", "Techland"):
                url, method = find_verified_match(session, site, product, competitor_index)
                product["urls"][site] = url
                product["match_methods"][site] = method
                print(f"  {site}: {method}")
    with open("products.json", "w", encoding="utf-8") as file:
        json.dump(products, file, ensure_ascii=False, indent=2)
    print("Saved Acer-only catalogue:", len(products))
    return products


if __name__ == "__main__":
    build_catalog()
