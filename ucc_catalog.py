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

# Use the site's normal page size. UCC may ignore a large ?limit= value.
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
    "[class*='product-layout']", "[class*='product-card']",
)
EXCLUDED_PATH_PARTS = (
    "account", "checkout", "compare", "wishlist", "search", "category",
    "brand", "blog", "news", "information", "contact", "about",
)


def build_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=5, connect=5, read=5, status=5, backoff_factor=1.5,
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
    query.pop("limit", None)
    if page <= 1:
        query["page"] = ["1"]
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
    if parsed.query or any(part in path for part in EXCLUDED_PATH_PARTS):
        return None
    if path in {
        "laptops", "gaming-laptops", "content-creation-laptops",
        "business-productivity-laptops", "acer", "msi", "apple",
    }:
        return None
    # Accept common SEO product slugs, including nested product routes.
    if len(path) < 5 or path.count("/") > 2:
        return None
    return urlunparse(parsed._replace(query="", fragment=""))


def is_laptop_context(anchor):
    bits = [
        anchor.get_text(" ", strip=True),
        anchor.get("title", ""),
        anchor.get("href", ""),
    ]
    parent = anchor.find_parent(class_=re.compile(r"product|item|card", re.I))
    if parent:
        bits.append(parent.get_text(" ", strip=True))
        bits.append(" ".join(str(x) for x in parent.get("class", [])))
    context = " ".join(bits).lower()
    return bool(re.search(
        r"laptop|notebook|macbook|aspire|nitro|predator|travelmate|travellite|"
        r"swift|vector|katana|creator|cyborg|modern|prestige|thin|venture|"
        r"helios|stealth|summit|raider|gaming|\bmsi\b|\bacer\b|\bapple\b",
        context,
    ))


def parse_category_page(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    count_match = re.search(r"Showing\s+(\d+)\s+to\s+(\d+)\s+of\s+(\d+)", text, re.I)
    expected_total = int(count_match.group(3)) if count_match else None
    expected_on_page = (
        int(count_match.group(2)) - int(count_match.group(1)) + 1
        if count_match else None
    )

    listing = soup.select_one("#content, .product-list, .product-grid, main") or soup
    candidates = []
    seen_anchors = set()
    for selector in CARD_SELECTORS:
        for card in listing.select(selector):
            for anchor in card.select("a[href]"):
                marker = id(anchor)
                if marker not in seen_anchors:
                    seen_anchors.add(marker)
                    candidates.append((anchor, "card:" + selector))

    # If the card wrappers changed, inspect every anchor in the listing region.
    if not candidates or (expected_on_page and len(candidates) < expected_on_page):
        for anchor in listing.select("a[href]"):
            marker = id(anchor)
            if marker not in seen_anchors:
                seen_anchors.add(marker)
                candidates.append((anchor, "listing-fallback"))

    page_urls = set()
    rejected = []
    for anchor, source in candidates:
        url = product_url_from_anchor(anchor, page_url)
        if not url:
            continue
        if is_laptop_context(anchor):
            page_urls.add(url)
        elif source.startswith("card:"):
            # Product card wrapper itself is strong evidence of a product link.
            page_urls.add(url)
        else:
            rejected.append((url, anchor.get_text(" ", strip=True)[:90]))

    page_links = set()
    for anchor in soup.select(".pagination a[href], ul.pagination a[href], a[href*='page=']"):
        href = urljoin(page_url, anchor.get("href", ""))
        q = parse_qs(urlparse(href).query)
        if "page" in q and q["page"][0].isdigit():
            page_links.add(int(q["page"][0]))

    print(
        f"UCC page diagnostics: expected_on_page={expected_on_page}, "
        f"candidate_anchors={len(candidates)}, product_urls={len(page_urls)}, "
        f"rejected_non_laptop={len(rejected)}, pagination={sorted(page_links)}"
    )
    for url, label in rejected[:8]:
        print(f"UCC ignored listing link: {label!r} -> {url}")
    return page_urls, expected_total, sorted(page_links)


def discover_all_product_urls(max_pages=MAX_PAGES):
    session = build_session()
    product_urls = set()
    expected_total = None
    visited_pages = set()

    # Standard pagination avoids the site's inconsistent handling of ?limit=100.
    for page in range(1, max_pages + 1):
        visited_pages.add(page)
        url = category_page_url(page)
        last_error = None
        for attempt in range(1, 4):
            try:
                response = session.get(url, timeout=TIMEOUT)
                response.raise_for_status()
                page_urls, page_expected, page_links = parse_category_page(
                    response.text, response.url
                )
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

        # Stop only after an actually empty page or a page that adds no products.
        if not page_urls or (page > 1 and not new_urls):
            break

        # Follow any non-sequential pagination links on the next iterations if needed.
        future_pages = [p for p in page_links if p > page and p not in visited_pages]
        if future_pages and min(future_pages) > page + 1:
            print(f"UCC pagination also advertises pages: {sorted(page_links)}")

    print(
        f"UCC category reports {expected_total} products; "
        f"discovered {len(product_urls)} unique product URLs across {len(visited_pages)} page(s)."
    )
    if len(product_urls) < 10:
        raise RuntimeError(f"Only {len(product_urls)} UCC laptop product URLs found; refusing partial scan.")
    if expected_total and len(product_urls) != expected_total:
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
                if product.get("name"):
                    products.append(product)
                    if product.get("price") is None:
                        print(f"UCC product has no readable base price (will be marked unverified): {url}")
                else:
                    errors.append(f"{url}: missing product name")
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:140]}")

    products.sort(key=lambda item: (item.get("name") or "").lower())
    print(
        f"UCC catalogue: {len(urls)} links discovered; "
        f"{len(products)} products parsed; {len(errors)} warnings."
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
