"""Compare every UCC laptop price against Ryans, Star Tech and Techland."""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

from ucc_catalog import get_all_laptops
from retailer_fetcher import search_all_retailers

HISTORY_FILE = Path("price_history.json")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RETAILERS = ("Ryans", "Star Tech", "Techland")


def load_history():
    if not HISTORY_FILE.exists():
        return {}
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        print("Warning: history file unreadable; a fresh retailer baseline will be saved.")
        return {}


def save_history(history):
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def money(value):
    return f"৳{int(value):,}" if value is not None else "N/A"


def product_key(product):
    sku = re.sub(r"[^A-Z0-9]", "", str(product.get("sku") or "").upper())
    if sku:
        return sku
    slug = urlparse(product.get("url", "")).path.strip("/").split("/")[-1]
    fallback = re.sub(r"[^A-Z0-9]", "", str(product.get("name") or "").upper())
    return "URL:" + (slug or fallback)


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub Secret.")
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks, current = [], ""
    for line in message.splitlines():
        if len(current) + len(line) + 1 > 3800:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for chunk in chunks:
        response = requests.post(
            endpoint,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")


def scan_one(product):
    try:
        return product, search_all_retailers(product), None
    except Exception as exc:
        return product, {}, f"{type(exc).__name__}: {str(exc)[:180]}"


def main():
    print("Starting complete UCC-base retailer price sync...")
    products = get_all_laptops()
    if len(products) < 10:
        raise RuntimeError(f"Only {len(products)} UCC laptops parsed; refusing incomplete scan.")

    history = load_history()
    current_history = {}
    verified_counts = {retailer: 0 for retailer in RETAILERS}
    higher_counts = {retailer: 0 for retailer in RETAILERS}
    lower_counts = {retailer: 0 for retailer in RETAILERS}
    equal_counts = {retailer: 0 for retailer in RETAILERS}
    unverified_counts = {retailer: 0 for retailer in RETAILERS}
    differences = []
    matched_all_three = 0
    complete_equal_products = 0
    print(f"UCC products: {len(products)}; retailer comparisons expected: {len(products) * 3}")

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(scan_one, product) for product in products]
        scan_results = [future.result() for future in as_completed(futures)]

    for product, retailer_results, scan_error in scan_results:
        key = product_key(product)
        ucc_price = product.get("price")
        old_record = history.get(key, {})
        current = {
            "name": product.get("name"),
            "sku": product.get("sku"),
            "url": product.get("url"),
            "ucc_base_price": ucc_price,
            "retailers": {},
        }
        if scan_error:
            print(f"Retailer scan error {key}: {scan_error}")

        product_verified = 0
        product_equal = 0
        for retailer in RETAILERS:
            result = retailer_results.get(retailer) or {}
            candidates = result.get("results") or []
            candidate = next((
                item for item in candidates
                if (item.get("match") or {}).get("price_comparison_allowed") is True
                and item.get("price") is not None
                and not re.search(r"OUT OF STOCK|DISCONTINUED|UNAVAILABLE",
                                  str(item.get("stock_status") or ""), re.I)
            ), None)

            if candidate is None or ucc_price is None:
                unverified_counts[retailer] += 1
                errors = result.get("errors") or []
                print(
                    f"{retailer} {key}: UNVERIFIED | {result.get('status', 'NO RESULT')} | "
                    f"{'; '.join(errors[:1])}"
                )
                continue

            retailer_price = int(candidate["price"])
            base_price = int(ucc_price)
            delta = retailer_price - base_price
            product_verified += 1
            verified_counts[retailer] += 1
            current["retailers"][retailer] = {
                "price": retailer_price,
                "url": candidate.get("url"),
                "name": candidate.get("name"),
                "match_status": (candidate.get("match") or {}).get("status"),
            }

            if delta > 0:
                higher_counts[retailer] += 1
                differences.append(
                    f"PRICE HIGHER: {retailer}\n"
                    f"Product: {product.get('name')}\n"
                    f"SKU: {product.get('sku') or key}\n"
                    f"UCC base: {money(base_price)} | {retailer}: {money(retailer_price)}\n"
                    f"Difference: +{money(delta)}\n"
                    f"Link: {candidate.get('url')}"
                )
            elif delta < 0:
                lower_counts[retailer] += 1
                differences.append(
                    f"PRICE LOWER: {retailer}\n"
                    f"Product: {product.get('name')}\n"
                    f"SKU: {product.get('sku') or key}\n"
                    f"UCC base: {money(base_price)} | {retailer}: {money(retailer_price)}\n"
                    f"Difference: -{money(abs(delta))}\n"
                    f"Link: {candidate.get('url')}"
                )
            else:
                equal_counts[retailer] += 1
                product_equal += 1

        if product_verified == 3:
            matched_all_three += 1
            if product_equal == 3:
                complete_equal_products += 1
        current_history[key] = current

    # Keep previous verified prices when a retailer temporarily fails, but do not
    # count a stale value as a current verified comparison.
    merged = dict(history)
    for key, record in current_history.items():
        old = merged.get(key, {})
        old_retailers = old.get("retailers") or {}
        new_retailers = record.get("retailers") or {}
        for retailer in RETAILERS:
            if retailer not in new_retailers and retailer in old_retailers:
                record["retailers"][retailer] = old_retailers[retailer]
        merged[key] = record
    save_history(merged)

    verified_total = sum(verified_counts.values())
    expected_total = len(products) * len(RETAILERS)
    all_comparisons_verified = verified_total == expected_total
    all_prices_equal = all_comparisons_verified and not differences
    report = [
        "UCC LAPTOP PRICE SYNC REPORT",
        "============================",
        f"UCC laptops scanned: {len(products)}",
        f"Verified retailer comparisons: {verified_total}/{expected_total}",
        f"Products matched on all 3 retailers: {matched_all_three}/{len(products)}",
        "",
    ]
    for retailer in RETAILERS:
        report.append(
            f"{retailer}: verified {verified_counts[retailer]}/{len(products)} | "
            f"higher {higher_counts[retailer]} | lower {lower_counts[retailer]} | "
            f"equal {equal_counts[retailer]} | unverified {unverified_counts[retailer]}"
        )
    report += [
        "",
        f"All retailer prices equal UCC: {'YES' if all_prices_equal else 'NO'}",
    ]

    if differences:
        report += ["", f"PRICE DIFFERENCES AGAINST UCC BASE: {len(differences)}", ""]
        report.extend(differences)
    elif all_prices_equal:
        report += ["", "ALL OK: every verified retailer price equals the UCC base price."]
    else:
        report += [
            "",
            "PARTIAL REPORT: no verified price differences found, but some products "
            "could not be matched. This is NOT an all-match result."
        ]

    send_telegram("\n".join(report))
    print("\n".join(report[:20]))
    print("Telegram UCC-base price sync report sent successfully.")
    print("UCC price movements are not tracked; UCC current price is the comparison baseline.")


if __name__ == "__main__":
    main()
