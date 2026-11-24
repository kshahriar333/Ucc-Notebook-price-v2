
"""Retailer-specific, conservative public product search.

Only product-detail pages are returned. Search pages, categories, login pages,
offers, cart pages, builder pages, and Cloudflare email-protection links are
never treated as products. Access restrictions are reported, not bypassed.
"""
import json
import re
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = (10, 22)

RETAILERS = {
    "Ryans": {
        "domain": "ryans.com",
        "search_urls": [
            "https://www.ryans.com/search?q={query}",
            "https://www.ryans.com/search?search={query}",
        ],
    },
    "Star Tech": {
        "domain": "startech.com.bd",
        "search_urls": [
            "https://www.startech.com.bd/search?search={query}",
            "https://www.startech.com.bd/index.php?route=product/search&search={query}",
        ],
    },
    "Techland": {
        "domain": "techlandbd.com",
        "search_urls": [
            "https://www.techlandbd.com/index.php?route=product/search&search={query}",
            "https://www.techlandbd.com/search?search={query}",
        ],
    },
}

NON_PRODUCT_PATHS = (
    "search", "category", "categories", "brand", "brands", "offers", "offer",
    "login", "logout", "register", "account", "customer", "cart", "checkout",
    "wishlist", "compare", "pc-builder", "builder", "tools", "shop-laptop",
    "shop-laptop-computer", "laptop-and-laptop-accessories", "pc-components",
    "computer-components", "computer-accessories", "new-arrival", "contact",
    "about", "blog", "news", "faq", "service", "complaint", "privacy",
    "terms", "shipping", "payment", "return",
)
BAD_URL_PARTS = ("/cdn-cgi/l/email-protection", "javascript:", "mailto:", "#")


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def extract_price(value):
    text = str(value or "")
    for pattern in (
        r"(?:৳|Tk\.?|BDT)\s*([\d,]+)",
        r"([\d,]+)\s*(?:৳|Tk\.?|BDT)"
    ):
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
            return match.group(1).upper()

    return None


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_soup(url, session):
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def is_product_candidate_url(url, domain):
    if not url or any(part in url.lower() for part in BAD_URL_PARTS):
        return False

    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")

    if host != domain:
        return False

    path = parsed.path.lower().strip("/")

    if not path:
        return False

    segments = [segment for segment in path.split("/") if segment]

    if not segments:
        return False

    if any(blocked in path for blocked in NON_PRODUCT_PATHS):
        return False

    if parsed.query and (
        "route=product/search" in parsed.query
        or "search=" in parsed.query
    ):
        return False

    return True


def extract_result_anchors(soup):
    """Return anchors from product-card-like containers only."""

    selectors = [
        ".product-thumb",
        ".product-layout",
        ".product-item",
        ".product-card",
        ".product-grid .item",
        ".product-list .item",
        ".product-box",
        "[class*='product-thumb']",
        "[class*='product-item']",
        "[class*='product-card']",
        "[itemtype*='Product']",
    ]

    anchors = []
    seen_nodes = set()

    for selector in selectors:
        for container in soup.select(selector):
            for anchor in container.select("a[href]"):
                marker = (
                    anchor.get("href"),
                    anchor.get_text(" ", strip=True)
                )

                if marker not in seen_nodes:
                    seen_nodes.add(marker)
                    anchors.append(anchor)

    return anchors


def parse_product_page(url, session, expected_domain):
    soup = get_soup(url, session)

    heading = soup.find("h1")
    name = clean(
        heading.get_text(" ", strip=True) if heading else ""
    )

    if not name and soup.title:
        name = clean(soup.title.get_text(" ", strip=True))

    page_text = clean(soup.get_text(" ", strip=True))
    lower_text = page_text.lower()

    has_product_schema = bool(
        soup.select(
            '[itemtype*="Product"], '
            '[itemprop="price"], '
            'meta[property="product:price:amount"]'
        )
        or soup.select('script[type="application/ld+json"]')
    )

    has_price_marker = bool(
        soup.select(
            '[itemprop="price"], '
            'meta[property="product:price:amount"], '
            'meta[property="og:price:amount"], '
            '.price, .price-new, .product-price'
        )
    )

    has_cart_control = bool(
        soup.select(
            'button[name="add-to-cart"], '
            'button#button-cart, '
            'button[onclick*="cart.add"], '
            '.add-to-cart, '
            '[data-button-action="add-to-cart"]'
        )
        or "add to cart" in lower_text
    )

    if not name or not (
        has_product_schema or has_price_marker or has_cart_control
    ):
        return None

    generic_title = any(
        term in name.lower()
        for term in (
            "account login",
            "latest offers",
            "new arrival products",
            "pc builder",
            "build your own pc",
            "your cart",
            "complaint",
            "useful tools",
            "laptop and laptop accessories price",
            "computer components price",
            "computer accessories price",
            "latest desktop pc price",
        )
    )

    if generic_title:
        return None

    price = None

    for tag in soup.select(
        '[itemprop="price"], '
        'meta[property="product:price:amount"], '
        'meta[property="og:price:amount"], '
        '.price, .price-new, .product-price, .special-price'
    ):
        price = extract_price(
            tag.get("content") or tag.get_text(" ", strip=True)
        )

        if price:
            break

    if price is None:

        def walk(obj):
            if isinstance(obj, list):
                for item in obj:
                    found = walk(item)
                    if found:
                        return found

            elif isinstance(obj, dict):
                if "@graph" in obj:
                    found = walk(obj["@graph"])
                    if found:
                        return found

                offers = obj.get("offers")

                if offers:
                    found = walk(offers)
                    if found:
                        return found

                found = extract_price(obj.get("price"))

                if found:
                    return found

            return None

        for script in soup.select(
            'script[type="application/ld+json"]'
        ):
            try:
                price = walk(
                    json.loads(script.string or script.get_text())
                )

                if price:
                    break

            except (ValueError, TypeError):
                continue

    return {
        "retailer": expected_domain,
        "name": name,
        "sku": extract_sku(page_text),
        "price": price,
        "url": url,
        "page_text": page_text,
    }


def search_page(term, retailer_name, session, search_url_template):
    config = RETAILERS[retailer_name]

    search_url = search_url_template.format(
        query=quote(term)
    )

    soup = get_soup(search_url, session)

    anchors = extract_result_anchors(soup)

    candidates = []
    seen = set()

    for anchor in anchors:
        url = urljoin(
            search_url,
            anchor.get("href", "")
        )

        if not is_product_candidate_url(
            url,
            config["domain"]
        ):
            continue

        if url in seen:
            continue

        seen.add(url)

        try:
            item = parse_product_page(
                url,
                session,
                config["domain"]
            )

            if item:
                candidates.append(item)

        except requests.RequestException:
            continue

    return candidates


def search_one(product, retailer_name):
    config = RETAILERS[retailer_name]

    session = make_session()

    search_terms = []

    if product.get("sku"):
        search_terms.append(
            ("SKU", product["sku"])
        )

    if product.get("model"):
        search_terms.append(
            ("MODEL", product["model"])
        )

    if product.get("name"):
        compact = " ".join(
            str(product["name"]).split()[:8]
        )

        search_terms.append(
            ("NAME", compact)
        )

    errors = []
    attempted_any = False

    for search_type, term in search_terms:

        all_candidates = []

        for template in config["search_urls"]:

            try:
                attempted_any = True

                candidates = search_page(
                    term,
                    retailer_name,
                    session,
                    template
                )

                all_candidates.extend(candidates)

            except requests.HTTPError as exc:
                status = getattr(
                    exc.response,
                    "status_code",
                    None
                )

                errors.append(
                    f"{status or 'HTTP'} from "
                    f"{template.split('?')[0]}"
                )

            except requests.RequestException as exc:
                errors.append(
                    f"{type(exc).__name__}: {str(exc)[:100]}"
                )

        if search_type == "SKU":

            exact = [
                item
                for item in all_candidates
                if item.get("sku")
                and normalize(item["sku"])
                == normalize(product["sku"])
            ]

            if exact:
                return {
                    "status": "OK",
                    "results": deduplicate(exact),
                    "errors": []
                }

            continue

        filtered = deduplicate(all_candidates)

        if filtered:
            return {
                "status": "OK - VALID PRODUCT PAGES",
                "results": filtered[:6],
                "errors": errors[:3]
            }

    if errors and not attempted_any:
        status = "REQUEST ERROR"

    elif errors and not search_terms:
        status = "REQUEST ERROR"

    elif errors:
        status = "REQUEST ERROR / BLOCKED OR URL CHANGED"

    else:
        status = "NO VALID PRODUCT FOUND"

    return {
        "status": status,
        "results": [],
        "errors": errors[:4]
    }


def deduplicate(items):
    unique = {}

    for item in items:
        unique[item["url"]] = item

    return list(unique.values())


def search_all_retailers(product):
    return {
        name: search_one(product, name)
        for name in RETAILERS
    }
