"""Discover every product in UCC's laptop category and parse product detail pages."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

from ucc_reader import read_product

CATEGORY_URL = "https://www.ucc.com.bd/laptops"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = (10, 25)
CARD_SELECTORS = (
    ".product-thumb", ".product-layout", ".product-item",
    ".product-card", ".product-grid .item", ".product-list .item",
    "[class*='product-item']", "[class*='product-thumb']",
)


def category_page_url(page):
    if page <= 1:
        return CATEGORY_URL
    parsed = urlparse(CATEGORY_URL)
    query = parse_qs(parsed.query)
    query["page"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def discover_all_product_urls(max_pages=10):
    session = requests.Session()
    session.headers.update(HEADERS)
    product_urls = set()

    for page in range(1, max_pages + 1):
        url = category_page_url(page)
        response = session.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        cards = []
        for selector in CARD_SELECTORS:
            cards = soup.select(selector)
            if cards:
                break

        page_urls = set()
        for card in cards:
            card_text = " ".join(card.stripped_strings).lower()
            if "laptop" not in card_text and "notebook" not in card_text:
                continue
            for anchor in card.select("a[href]"):
                href = urljoin(url, anchor.get("href", ""))
                parsed = urlparse(href)
                host = parsed.netloc.lower().removeprefix("www.")
                path = parsed.path.strip("/").lower()
                if host != "ucc.com.bd" or not path:
                    continue
                if path in {
                    "laptops", "gaming-laptops", "content-creation-laptops",
                    "business-productivity-laptops",
                }:
                    continue
                if any(part in path for part in (
                    "account", "checkout", "compare", "wishlist", "search",
                    "category", "brand", "blog", "news",
                )):
                    continue
                page_urls.add(urlunparse(parsed._replace(query="", fragment="")))

        new_urls = page_urls - product_urls
        product_urls.update(page_urls)
        print(
            f"UCC catalogue page {page}: {len(page_urls)} product links "
            f"({len(new_urls)} new)"
        )
        if page > 1 and not new_urls:
            break

    if len(product_urls) < 10:
        raise RuntimeError(
            f"UCC catalogue returned only {len(product_urls)} product URLs; "
            "stopping to avoid an incomplete scan."
        )
    return sorted(product_urls)


def get_all_laptops():
    urls = discover_all_product_urls()
    products, errors = [], []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(read_product, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                product = future.result()
                if product.get("name") and product.get("price"):
                    products.append(product)
                else:
                    errors.append(f"{url}: missing name or current price")
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:140]}")

    products.sort(key=lambda item: (item.get("name") or "").lower())
    print(
        f"UCC catalogue: {len(urls)} links discovered; "
        f"{len(products)} products parsed with price; {len(errors)} warnings."
    )
    for error in errors[:15]:
        print("UCC catalogue warning:", error)
    if not products:
        raise RuntimeError("No UCC laptop pages could be parsed.")
    return products
