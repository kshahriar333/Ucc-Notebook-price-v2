"""Full UCC laptop price comparison with historical retailer price-change alerts."""
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
        print("Warning: price_history.json could not be read; starting a new baseline.")
        return {}


def save_history(history):
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
            current = f"{current}\\n{line}" if current else line
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
    print("Starting UCC-base laptop price comparison and change detection...")
    products = get_all_laptops()
    if len(products) < 10:
        raise RuntimeError(
            f"Only {len(products)} UCC laptops parsed; refusing an incomplete scan."
        )

    history = load_history()
    current_history = {}
    movements = []
    matched_counts = {retailer: 0 for retailer in RETAILERS}
    higher_counts = {retailer: 0 for retailer in RETAILERS}
    lower_counts = {retailer: 0 for retailer in RETAILERS}
    unavailable_counts = {retailer: 0 for retailer in RETAILERS}
    total_verified_changes = 0

    print(f"Comparing {len(products)} UCC base prices against each retailer...")
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
            print(f"Retailer scan error for {key}: {scan_error}")

        for retailer in RETAILERS:
            result = retailer_results.get(retailer) or {}
            candidates = result.get("results") or []
            candidate = next(
                (
                    item for item in candidates
                    if (item.get("match") or {}).get("price_comparison_allowed") is True
                    and item.get("price") is not None
                    and "OUT OF STOCK" not in str(item.get("stock_status") or "").upper()
                    and "DISCONTINUED" not in str(item.get("stock_status") or "").upper()
                ),
                None,
            )
            if candidate is None or ucc_price is None:
                unavailable_counts[retailer] += 1
                errors = result.get("errors") or []
                print(
                    f"{retailer} {key}: not compared | "
                    f"{result.get('status') or 'NO RESULT'} | "
                    f"{'; '.join(errors[:1])}"
                )
                continue

            retailer_price = int(candidate["price"])
            delta_from_ucc = retailer_price - int(ucc_price)
            current["retailers"][retailer] = {
                "price": retailer_price,
                "url": candidate.get("url"),
                "name": candidate.get("name"),
                "match_status": (candidate.get("match") or {}).get("status"),
            }
            matched_counts[retailer] += 1
            if delta_from_ucc > 0:
                higher_counts[retailer] += 1
            elif delta_from_ucc < 0:
                lower_counts[retailer] += 1

            old_retailer = (old_record.get("retailers") or {}).get(retailer)
            old_price = (
                old_retailer.get("price")
                if isinstance(old_retailer, dict)
                else old_retailer
            )
            if old_price is not None and int(old_price) != retailer_price:
                direction = "PRICE INCREASED" if retailer_price > int(old_price) else "PRICE DECREASED"
                movement = (
                    f"{direction}\n"
                    f"Product: {product.get('name')}\n"
                    f"SKU: {product.get('sku') or key}\n"
                    f"Retailer: {retailer}\n"
                    f"Previous: {money(old_price)}\n"
                    f"Current: {money(retailer_price)}\n"
                    f"Change: {money(abs(retailer_price - int(old_price)))}\n"
                    f"UCC current price: {money(ucc_price)}\n"
                    f"Competitor link: {candidate.get('url')}"
                )
                movements.append(movement)
                total_verified_changes += 1

        # Keep the old verified price if a retailer was temporarily inaccessible.
        old_retailers = old_record.get("retailers") or {}
        for retailer in RETAILERS:
            if retailer not in current["retailers"] and retailer in old_retailers:
                current["retailers"][retailer] = old_retailers[retailer]
        current_history[key] = current

    merged_history = dict(history)
    merged_history.update(current_history)
    save_history(merged_history)

    total_comparisons = sum(matched_counts.values())
    total_possible = len(products) * len(RETAILERS)
    summary = [
        "UCC LAPTOP PRICE MONITOR",
        "========================",
        f"UCC products scanned: {len(products)}",
        f"Verified retailer comparisons this scan: {total_comparisons}/{total_possible}",
        "",
    ]
    for retailer in RETAILERS:
        summary.append(
            f"{retailer}: verified {matched_counts[retailer]}/{len(products)} | "
            f"above UCC {higher_counts[retailer]} | below UCC {lower_counts[retailer]} | "
            f"not verified {unavailable_counts[retailer]}"
        )

    if movements:
        message = (
            "\\n".join(summary)
            + f"\\n\\nVERIFIED PRICE MOVEMENTS: {total_verified_changes}\\n\\n"
            + "\\n\\n".join(movements)
        )
        send_telegram(message)
        print(f"Sent {total_verified_changes} verified price movement alert(s).")
    elif not history:
        message = (
            "\\n".join(summary)
            + "\\n\\nINITIAL BASELINE SAVED. Future verified retailer price increases "
            "or decreases will be sent to Telegram."
        )
        send_telegram(message)
        print("Sent initial baseline report.")
    else:
        print("No verified retailer price movement. No Telegram alert sent.")

    print("Price scan completed.")


if __name__ == "__main__":
    main()
