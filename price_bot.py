"""Compare every retailer's current laptop price directly against UCC base price."""
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
        value = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError):
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
    return "URL:" + (slug or re.sub(r"[^A-Z0-9]", "", product.get("name", "").upper()))


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
    print("Starting UCC-base laptop price comparison...")
    products = get_all_laptops()
    if len(products) < 10:
        raise RuntimeError(
            f"Only {len(products)} UCC laptops parsed; refusing an incomplete comparison."
        )

    history = load_history()
    current_history = {}
    differences = []
    matched_counts = {retailer: 0 for retailer in RETAILERS}
    higher_counts = {retailer: 0 for retailer in RETAILERS}
    lower_counts = {retailer: 0 for retailer in RETAILERS}
    unavailable_counts = {retailer: 0 for retailer in RETAILERS}
    all_equal_products = 0

    print(f"Comparing {len(products)} UCC base prices against each retailer...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(scan_one, product) for product in products]
        scan_results = [future.result() for future in as_completed(futures)]

    for product, retailer_results, scan_error in scan_results:
        key = product_key(product)
        ucc_price = product.get("price")
        current = {
            "name": product.get("name"),
            "sku": product.get("sku"),
            "url": product.get("url"),
            "ucc_base_price": ucc_price,
            "retailers": {},
        }
        product_differences = []
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
                    f"{(result.get('status') or 'NO RESULT')} | "
                    f"{'; '.join(errors[:1])}"
                )
                continue

            retailer_price = candidate["price"]
            delta = retailer_price - ucc_price
            current["retailers"][retailer] = retailer_price
            matched_counts[retailer] += 1
            if delta > 0:
                higher_counts[retailer] += 1
                product_differences.append(
                    f"{retailer}: {money(retailer_price)} "
                    f"({money(delta)} বেশি UCC-এর চেয়ে)"
                )
            elif delta < 0:
                lower_counts[retailer] += 1
                product_differences.append(
                    f"{retailer}: {money(retailer_price)} "
                    f"({money(abs(delta))} কম UCC-এর চেয়ে)"
                )
            else:
                print(f"{retailer} {key}: MATCH / SAME PRICE {money(ucc_price)}")

        if len(current["retailers"]) == len(RETAILERS) and not product_differences:
            all_equal_products += 1

        if product_differences:
            differences.append(
                f"PRODUCT: {product.get('name')} | SKU: {product.get('sku') or key}\n"
                f"UCC BASE: {money(ucc_price)}\n  " + "\n  ".join(product_differences)
            )

        # History is diagnostic only. UCC price changes are never alerts.
        current_history[key] = current

    # Retain older records for products temporarily absent from the current crawl.
    merged_history = dict(history)
    merged_history.update(current_history)
    save_history(merged_history)

    total_comparisons = sum(matched_counts.values())
    total_possible = len(products) * len(RETAILERS)
    fully_synced = (
        total_comparisons == total_possible
        and not differences
        and all_equal_products == len(products)
    )

    summary = [
        "UCC LAPTOP PRICE SYNC REPORT",
        "============================",
        f"UCC products scanned: {len(products)}",
        f"Retailer comparisons verified: {total_comparisons}/{total_possible}",
        f"All three retailers equal UCC: {all_equal_products}/{len(products)}",
        "",
    ]
    for retailer in RETAILERS:
        summary.append(
            f"{retailer}: matched {matched_counts[retailer]}/{len(products)} | "
            f"higher {higher_counts[retailer]} | lower {lower_counts[retailer]} | "
            f"unmatched/unverified {unavailable_counts[retailer]}"
        )

    if fully_synced:
        message = "\n".join(summary) + "\n\nALL OK — ALL MATCH. All tracked retailer prices are synced with UCC."
    elif differences:
        message = "\n".join(summary) + "\nPRICE DIFFERENCES AGAINST UCC BASE\n\n" + "\n\n".join(differences)
        if total_comparisons < total_possible:
            message += (
                "\n\nNOTE: Some products could not be verified. "
                "They are not reported as matched; the report is partial."
            )
    else:
        message = "\n".join(summary) + (
            "\nNo verified price differences found, but some retailer matches are missing. "
            "This is not an ALL MATCH result."
        )

    print(message)
    send_telegram(message)
    print("Telegram UCC-base sync report sent successfully.")
    print("UCC price movements are not tracked or alerted.")


if __name__ == "__main__":
    main()
