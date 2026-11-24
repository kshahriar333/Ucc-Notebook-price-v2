"""UCC full-catalogue laptop price monitor with verified price-change alerts."""
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
    return f"৳{int(value):,}" if value is not None else "Not detected"


def product_key(product):
    sku = re.sub(r"[^A-Z0-9]", "", str(product.get("sku") or "").upper())
    if sku:
        return sku
    slug = urlparse(product.get("url", "")).path.strip("/").split("/")[-1]
    return "URL:" + (slug or re.sub(r"[^A-Z0-9]", "", product.get("name", "").upper()))


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub Secret."
        )
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Split long catalogue alerts into Telegram-safe chunks.
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
        retailers = search_all_retailers(product)
        return product, retailers, None
    except Exception as exc:
        return product, {}, f"{type(exc).__name__}: {str(exc)[:180]}"


def main():
    print("Starting full UCC laptop catalogue scan...")
    products = get_all_laptops()
    if len(products) < 10:
        raise RuntimeError(
            f"Only {len(products)} UCC laptops parsed; refusing to overwrite history "
            "with an incomplete catalogue."
        )

    history = load_history()
    alerts = []
    baseline_count = 0
    new_product_count = 0
    verified_counts = {"Ryans": 0, "Star Tech": 0, "Techland": 0}
    unverified_counts = {"Ryans": 0, "Star Tech": 0, "Techland": 0}
    current_keys = set()

    print(f"Scanning competitor prices for {len(products)} UCC laptops...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(scan_one, product) for product in products]
        scan_results = [future.result() for future in as_completed(futures)]

    for product, retailer_results, scan_error in scan_results:
        key = product_key(product)
        current_keys.add(key)
        previous = history.get(key, {})
        current = {
            "name": product.get("name"),
            "sku": product.get("sku"),
            "url": product.get("url"),
            "ucc": product.get("price"),
            "retailers": {},
        }

        if not previous:
            baseline_count += 1
            new_product_count += 1

        old_ucc = previous.get("ucc")
        new_ucc = current["ucc"]
        if old_ucc is not None and old_ucc != new_ucc:
            direction = "INCREASED" if new_ucc > old_ucc else "DECREASED"
            alerts.append(
                f"UCC {direction}: {product.get('name')} "
                f"({product.get('sku') or key}) | "
                f"{money(old_ucc)} → {money(new_ucc)}"
            )

        if scan_error:
            print(f"Competitor scan error for {key}: {scan_error}")

        for retailer, result in retailer_results.items():
            candidates = result.get("results") or []
            if not candidates:
                unverified_counts[retailer] += 1
                errors = result.get("errors") or []
                print(f"{retailer} {key}: {result.get('status')} | {'; '.join(errors[:2])}")
                continue

            candidate = candidates[0]
            match = candidate.get("match") or {}
            price = candidate.get("price")
            stock = str(candidate.get("stock_status") or "").upper()
            allowed = (
                match.get("price_comparison_allowed") is True
                and price is not None
                and "OUT OF STOCK" not in stock
                and "DISCONTINUED" not in stock
            )
            if not allowed:
                unverified_counts[retailer] += 1
                print(
                    f"{retailer} {key}: not compared | "
                    f"{match.get('status', 'UNVERIFIED')} | stock={stock}"
                )
                continue

            verified_counts[retailer] += 1
            current["retailers"][retailer] = price
            old_price = (previous.get("retailers") or {}).get(retailer)
            if old_price is not None and old_price != price:
                direction = "INCREASED" if price > old_price else "DECREASED"
                alerts.append(
                    f"{retailer.upper()} {direction}: {product.get('name')} "
                    f"({product.get('sku') or key}) | "
                    f"{money(old_price)} → {money(price)}"
                )
            print(
                f"{retailer} {key}: {money(price)} | "
                f"{match.get('status')} | SKU match={match.get('sku_match')}"
            )

        # Preserve the last known verified competitor price if a retailer is
        # temporarily unavailable or a configuration cannot be verified.
        merged = dict(previous.get("retailers") or {})
        merged.update(current["retailers"])
        current["retailers"] = merged
        history[key] = current

    if not current_keys:
        raise RuntimeError("No product keys were collected; history was not updated.")

    save_history(history)
    summary = [
        "UCC FULL LAPTOP CATALOGUE SCAN",
        "==============================",
        f"UCC laptops parsed: {len(products)}",
        f"New products added to history: {new_product_count}",
        f"Verified competitor matches: Ryans {verified_counts['Ryans']}, "
        f"Star Tech {verified_counts['Star Tech']}, Techland {verified_counts['Techland']}",
        f"Unverified/not found: Ryans {unverified_counts['Ryans']}, "
        f"Star Tech {unverified_counts['Star Tech']}, Techland {unverified_counts['Techland']}",
        "Price alerts are generated only for verified configuration matches.",
        "Repository: https://github.com/kshahriar333/Ucc-Notebook-price-v2",
    ]
    print("\n".join(summary))
    print(f"Price changes detected: {len(alerts)}")

    if alerts:
        message = "\n".join(summary)
        if baseline_count:
            message += (
                f"\nNewly tracked laptops added to the baseline: {baseline_count}."
            )
        message += "\n\nPRICE CHANGES\n" + "\n".join(alerts)
        send_telegram(message)
        print("Telegram price-change alert sent.")
    elif baseline_count:
        message = "\n".join(summary) + (
            f"\n\nInitial baseline saved for {baseline_count} newly tracked laptops. "
            "Future verified price changes will trigger alerts."
        )
        send_telegram(message)
        print("Telegram initial catalogue baseline sent.")
    else:
        print("No verified price movement. No Telegram price-change alert sent.")

    print("Full catalogue scan completed.")


if __name__ == "__main__":
    main()
