"""Discover every product in UCC's all-brand laptop category."""
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

from ucc_reader import read_product

# This is the general laptop category (Acer + MSI + Apple), not Acer-only.
CATEGORY_URL = "https://www.ucc.com.bd/laptops"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}
TIMEOUT = (15, 45)
MAX_PAGES = 10
CARD_SELECTORS = (
    ".product-thumb", ".product-layout", ".product-item",
    ".product-card", ".product-grid .item", ".product-list .item",
    "[class*='product-item']", "[class*='product-thumb']",
)
EXCLUDED_PATH_PARTS = (
    "account", "checkout", "compare", "wishlist", "search", "category",
    "brand", "blog", "news", "information", "contact", "about",
)


def build_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET",)),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def category_page_url(page):
    parsed = urlparse(CATEGORY_URL)
    query = parse_qs(parsed.query)
    if page <= 1:
        query.pop("page", None)
    else:
        query["page"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def product_url_from_anchor(anchor, base_url):
    href = anchor.get("href", "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return None
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.strip("/").lower()
    if host != "ucc.com.bd" or not path:
        return None
    if any(part in path for part in EXCLUDED_PATH_PARTS):
        return None
    if path in {"laptops", "gaming-laptops", "content-creation-laptops",
                "business-productivity-laptops"}:
        return None
    # UCC product detail pages use one SEO slug path, unlike category routes.
    if "/" in path or len(path) < 12:
        return None
    return urlunparse(parsed._replace(query="", fragment=""))


def parse_category_page(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for selector in CARD_SELECTORS:
        found = soup.select(selector)
        if len(found) >= 3:
            cards = found
            break
    page_urls = set()
    if cards:
        for card in cards:
            for anchor in card.select("a[href]"):
                url = product_url_from_anchor(anchor, page_url)
                if url:
                    page_urls.add(url)

    # Fallback: collect product-detail links from listing sections only.
    if not page_urls:
        listing = soup.select_one("#content, .product-list, .product-grid, main")
        if listing:
            for anchor in listing.select("a[href]"):
                url = product_url_from_anchor(anchor, page_url)
                if url:
                    page_urls.add(url)

    text = soup.get_text(" ", strip=True)
    count_match = re.search(r"Showing\s+\d+\s+to\s+\d+\s+of\s+(\d+)", text, re.I)
    expected_total = int(count_match.group(1)) if count_match else None
    page_links = []
    for anchor in soup.select(".pagination a[href], ul.pagination a[href], a[href*='page=']"):
        href = urljoin(page_url, anchor.get("href", ""))
        parsed = urlparse(href)
        q = parse_qs(parsed.query)
        if "page" in q and q["page"][0].isdigit():
            page_links.append(int(q["page"][0]))
    return page_urls, expected_total, page_links


def discover_all_product_urls(max_pages=MAX_PAGES):
    session = build_session()
    product_urls = set()
    expected_total = None
    pages_to_visit = [1]
    visited_pages = set()

    while pages_to_visit and len(visited_pages) < max_pages:
        page = pages_to_visit.pop(0)
        if page in visited_pages:
            continue
        visited_pages.add(page)
        url = category_page_url(page)
        last_error = None
        for attempt in range(1, 4):
            try:
                response = session.get(url, timeout=TIMEOUT)
                response.raise_for_status()
                page_urls, page_expected, page_links = parse_category_page(response.text, response.url)
                break
            except requests.RequestException as exc:
                last_error = exc
                print(f"UCC category page {page} attempt {attempt}/3 failed: {type(exc).__name__}: {exc}")
                time.sleep(attempt * 2)
        else:
            raise RuntimeError(f"Could not load UCC laptop category page {page}: {last_error}")

        if page_expected:
            expected_total = max(expected_total or 0, page_expected)
        new_urls = page_urls - product_urls
        product_urls.update(page_urls)
        print(
            f"UCC all-laptop page {page}: {len(page_urls)} product links "
            f"({len(new_urls)} new); discovered total {len(product_urls)}"
        )

        # Use actual pagination links when available; otherwise try sequential pages.
        for linked_page in page_links:
            if linked_page not in visited_pages and linked_page not in pages_to_visit:
                pages_to_visit.append(linked_page)
        if not page_links and page_urls and len(page_urls) >= 10:
            next_page = page + 1
            if next_page not in visited_pages and next_page not in pages_to_visit:
                pages_to_visit.append(next_page)

        if page > 1 and not new_urls and not page_links:
            break

    print(f"UCC category reports {expected_total} products; discovered {len(product_urls)} unique product URLs.")
    if len(product_urls) < 10:
        raise RuntimeError(f"Only {len(product_urls)} UCC laptop product URLs found; refusing partial scan.")
    if expected_total and len(product_urls) < expected_total:
        raise RuntimeError(
            f"Incomplete UCC catalogue: expected {expected_total} products but found "
            f"{len(product_urls)}. No partial price report will be sent."
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
                if product.get("name") and product.get("price") is not None:
                    products.append(product)
                else:
                    errors.append(f"{url}: missing product name or current price")
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:140]}")

    products.sort(key=lambda item: (item.get("name") or "").lower())
    print(
        f"UCC catalogue: {len(urls)} links discovered; "
        f"{len(products)} products parsed with price; {len(errors)} warnings."
    )
    for error in errors[:20]:
        print("UCC catalogue warning:", error)
    if errors:
        raise RuntimeError(
            f"UCC catalogue is incomplete: {len(errors)} product pages failed. "
            "Refusing to send a misleading partial report."
        )
    if len(products) != len(urls) or not products:
        raise RuntimeError("UCC catalogue parse count mismatch; refusing partial scan.")
    return products
