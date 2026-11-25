#!/usr/bin/env python3
"""
UCC Price Monitor
-----------------
Reads the exact product pages listed in products.json (UCC, Ryans, Star Tech,
Techland), checks the SKU / part number on each page, reads the price, and
sends a Telegram message when something changes.

Run:  python monitor.py            (sends Telegram messages, saves state.json)
      python monitor.py --dry-run  (prints only, saves nothing)
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.getenv("PRODUCTS_FILE", os.path.join(BASE, "products.json"))
STATE_FILE = os.getenv("STATE_FILE", os.path.join(BASE, "state.json"))

SITE_ORDER = ["UCC", "Ryans", "Star Tech", "Techland"]
MIN_PRICE, MAX_PRICE = 5_000, 2_000_000
BIG_JUMP = 0.30  # flag price changes bigger than 30 percent

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
BLOCK_MARKERS = ("just a moment", "attention required", "cf-chl", "captcha",
                 "access denied", "enable javascript and cookies", "bot detection")

PROBLEM_TEXT = {
    "BLOCKED": "website blocked the bot (bot check)",
    "ERROR": "cannot open the page",
    "NO_PRICE": "price not found on the page",
    "SKU_MISMATCH": "page shows a DIFFERENT SKU",
    "SKU_UNVERIFIED": "SKU could not be confirmed; price not compared",
    "NO_MATCH": "same-SKU product page not found",
}


# ------------------------------------------------------------------ helpers
def norm(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def money(n):
    return f"৳{int(n):,}"


def to_int(value):
    try:
        v = int(round(float(str(value).replace(",", "").strip())))
    except (ValueError, TypeError):
        return None
    return v if MIN_PRICE <= v <= MAX_PRICE else None


class FetchError(Exception):
    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind, self.msg = kind, msg


def looks_blocked(text):
    head = (text or "")[:6000].lower()
    return any(m in head for m in BLOCK_MARKERS)


def fetch(url, session):
    last = FetchError("ERROR", "unknown error")
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=(10, 25))
        except requests.RequestException as exc:
            last = FetchError("ERROR", str(exc)[:120])
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (403, 429) or (r.status_code == 503 and looks_blocked(r.text)):
            raise FetchError("BLOCKED", f"HTTP {r.status_code}")
        if r.status_code == 404:
            raise FetchError("ERROR", "HTTP 404 (page removed or link changed)")
        if r.status_code >= 400:
            last = FetchError("ERROR", f"HTTP {r.status_code}")
            time.sleep(2 * (attempt + 1))
            continue
        if len(r.text) < 30_000 and looks_blocked(r.text):
            raise FetchError("BLOCKED", "bot check page")
        return r.text
    raise last


# ------------------------------------------------------------------ parsing
AMT_RE = re.compile(r"(?:৳|Tk\.?|BDT)\s*(\d[\d,]*)|(\d[\d,]*)\s*(?:৳|Tk\b|BDT)", re.I)
SKU_RE = re.compile(
    r"\b(?:SKU|MPN|Part\s*(?:No\.?|Number|#))\s*[:#\-]?\s*"
    r"([A-Za-z0-9](?=[A-Za-z0-9.\-/_]*\d)[A-Za-z0-9.\-/_]{3,})", re.I)
PRICE_SELECTORS = [".price-new", ".special-price", "[itemprop=price]",
                   ".product-price", ".price"]


def visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["head", "script", "style", "noscript", "template"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def price_from_meta(soup):
    for tag in soup.select('meta[property="product:price:amount"], '
                           'meta[property="og:price:amount"], meta[itemprop="price"]'):
        v = to_int(tag.get("content"))
        if v:
            return v
    return None


def price_from_jsonld(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                for key in ("price", "lowPrice"):
                    v = to_int(d.get(key))
                    if v:
                        return v
                stack.extend(x for x in d.values() if isinstance(x, (dict, list)))
    return None


def price_from_text(text, anchor):
    """First price after the product heading. If an old price is followed
    right away by a lower price, the lower one is the selling price."""
    start = text.find(anchor) if anchor else -1
    start = max(start, 0)
    found = []
    for m in AMT_RE.finditer(text, start):
        v = to_int((m.group(1) or m.group(2)))
        if v:
            found.append((v, m.start(), m.end()))
        if len(found) == 2:
            break
    if not found:
        return None
    if len(found) == 2:
        (v1, _, e1), (v2, s2, _) = found
        if s2 - e1 < 25 and 0.6 * v1 <= v2 < v1:
            return v2
    return found[0][0]


def price_from_selectors(soup):
    for sel in PRICE_SELECTORS:
        for tag in soup.select(sel):
            v = to_int(tag.get("content")) or to_int(re.sub(r"[^\d.]", "", tag.get_text(" ")))
            if v:
                return v
    return None


def check_site(site, url, expected_norm, session):
    res = {"site": site, "url": url, "status": "OK", "price": None,
           "sku_found": None, "sku_status": "UNVERIFIED", "error": ""}
    try:
        html = fetch(url, session)
    except FetchError as exc:
        res.update(status=exc.kind, error=exc.msg)
        return res

    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    h1text = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else ""
    text = visible_text(html)

    price = (price_from_meta(soup) or price_from_jsonld(soup)
             or price_from_text(text, h1text) or price_from_selectors(soup))
    labeled = SKU_RE.findall(text)
    first = labeled[0].strip(".") if labeled else None
    res["sku_found"] = first

    if expected_norm:
        if first:
            res["sku_status"] = "VERIFIED" if norm(first) == expected_norm else "MISMATCH"
        else:
            res["sku_status"] = "PRESENT" if expected_norm in norm(text) else "UNVERIFIED"

    if res["sku_status"] == "MISMATCH":
        res["status"] = "SKU_MISMATCH"
        res["error"] = f"page SKU is {first}"
    elif expected_norm and res["sku_status"] == "UNVERIFIED":
        res["status"] = "SKU_UNVERIFIED"
        res["error"] = "page did not expose the expected SKU"
        price = None
    elif price is None:
        res["status"] = "NO_PRICE"
    res["price"] = price
    return res


# ------------------------------------------------------------------ messages
def relation(price, ucc_price):
    if ucc_price is None or price is None:
        return ""
    diff = price - ucc_price
    if diff == 0:
        return "✅ same as UCC"
    if diff < 0:
        return f"🔴 {money(-diff)} cheaper than UCC"
    return f"🟢 {money(diff)} higher than UCC"


def now_lines(urls, entries):
    ucc_price = (entries.get("UCC") or {}).get("price")
    lines = []
    for site in SITE_ORDER:
        if site not in urls:
            continue
        e = entries.get(site) or {}
        price, status = e.get("price"), e.get("status", "ERROR")
        if status == "OK" and price:
            tag = "(our price)" if site == "UCC" else relation(price, ucc_price)
            warn = " ⚠️SKU not confirmed" if e.get("sku_status") == "UNVERIFIED" else ""
            lines.append(f"{site}: {money(price)} {tag}{warn}".rstrip())
        else:
            last = f" | last known {money(price)}" if price else ""
            lines.append(f"{site}: ⚠️ {PROBLEM_TEXT.get(status, status)}{last}")
    return "\n".join(lines)


# ------------------------------------------------------------------ core
def run(products, state, fetch_session, now, force_report=False, sleep=1.5):
    """Returns (messages, new_state). No file or network writes here."""
    first_run = not state
    new_state, messages = {}, []
    today = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%d %b %Y, %I:%M %p")

    for p in products:
        urls = p["urls"]
        key = p.get("id") or urls["UCC"]
        title = p.get("name") or urls["UCC"]
        expected = norm(p.get("sku")) or None
        display_sku = p.get("sku")

        results = {}
        for site in SITE_ORDER:
            page_url = urls.get(site)
            if not page_url:
                results[site] = {"site": site, "url": "", "status": "NO_MATCH",
                                 "price": None, "sku_found": None,
                                 "sku_status": "UNVERIFIED", "error": "SKU search found no verified product page"}
                continue
            res = check_site(site, page_url, expected, fetch_session)
            results[site] = res
            if site == "UCC" and not expected and res["sku_found"]:
                expected, display_sku = norm(res["sku_found"]), res["sku_found"]
            time.sleep(sleep)

        prev_all = state.get(key, {})
        entries, changes, problems, recovered = {}, [], [], []

        for site, res in results.items():
            prev = prev_all.get(site, {})
            e = dict(prev)
            if res["status"] == "OK":
                old, new = prev.get("price"), res["price"]
                if old is not None and old != new:
                    changes.append((site, old, new))
                    e["last_change"] = today
                if prev.get("alerted"):
                    recovered.append(site)
                e.update(price=new, status="OK", fails=0, alerted=False,
                         sku_status=res["sku_status"])
            else:
                fails = prev.get("fails", 0) + 1
                e.update(status=res["status"], fails=fails, sku_status=res["sku_status"])
                limit = 1 if res["status"] == "SKU_MISMATCH" else 2
                if fails >= limit and not prev.get("alerted"):
                    problems.append((site, res))
                    e["alerted"] = True
            entries[site] = e
        new_state[key] = entries

        # ---- build message for this product
        head = f"{title}\nSKU: {display_sku or 'unknown'}"
        link_sites = []
        parts = []
        for site, old, new in changes:
            diff = new - old
            arrow, word = ("🔻", "DOWN") if diff < 0 else ("🔺", "UP")
            flag = "  ⚠️ big change, please verify" if abs(diff) / old > BIG_JUMP else ""
            parts.append(f"{arrow} {site} price {word}: {money(old)} → {money(new)} "
                         f"({'-' if diff < 0 else '+'}{money(abs(diff))}){flag}")
            link_sites.append(site)
        for site, res in problems:
            extra = f" ({res['error']})" if res["error"] else ""
            parts.append(f"⚠️ {site}: {PROBLEM_TEXT.get(res['status'], res['status'])}{extra}")
            link_sites.append(site)
        for site in recovered:
            parts.append(f"✅ {site} can be read again")

        if parts:
            links = "\n".join(f"{s}: {urls[s]}" for s in link_sites if s in urls)
            messages.append(f"{head}\n\n" + "\n".join(parts) + "\n\nNow:\n"
                            + now_lines(urls, entries) + (f"\n\n{links}" if links else "")
                            + f"\n\n🕒 {stamp}")
        elif first_run or force_report:
            title_line = "✅ Monitoring started" if first_run else "📊 Price report"
            messages.append(f"{title_line}\n{head}\n\n" + now_lines(urls, entries)
                            + f"\n\n🕒 {stamp}")

    return messages, new_state


# ------------------------------------------------------------------ io
def send_telegram(text, token, chat_id):
    for i in range(0, len(text), 3900):
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text[i:i + 3900],
                  "disable_web_page_preview": "true"},
            timeout=20)
        r.raise_for_status()
        if not r.json().get("ok"):
            raise RuntimeError(f"Telegram rejected the message: {r.text[:200]}")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def main():
    dry = "--dry-run" in sys.argv
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not dry and not (token and chat_id):
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. "
                 "Add them as GitHub Secrets, or use --dry-run.")

    from catalog import build_catalog
    products = build_catalog()
    if not products:
        sys.exit("UCC catalogue discovery returned no products.")
    state = load_json(STATE_FILE, {})
    force = os.getenv("FORCE_REPORT", "").lower() in ("1", "true", "yes")
    now = datetime.now(ZoneInfo("Asia/Dhaka"))

    with requests.Session() as session:
        messages, new_state = run(products, state, session, now, force_report=force)

    for m in messages:
        if dry:
            print(m, "\n" + "-" * 40)
        else:
            send_telegram(m, token, chat_id)   # if this fails, state is NOT saved
    print(f"Done. Messages: {len(messages)}")

    if not dry and new_state != state:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, STATE_FILE)


if __name__ == "__main__":
    main()
