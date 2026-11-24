"""UCC Acer price-change notifier.

First successful scan stores a baseline. Later scans send Telegram alerts only
when UCC or a verified competitor selling price changes. History is persisted
in price_history.json by the GitHub Actions workflow.
"""
import json
import os
from pathlib import Path
import requests

from ucc_reader import read_product
from retailer_fetcher import search_all_retailers
from match_engine import compare_products

UCC_PRODUCT_URL = (
    "https://www.ucc.com.bd/"
    "acer-aspire-15-as15-42-r5-ryzen-5-7430u-16gb-82-ddr4-3200mhz-"
    "512gb-nvme-ssd-156-inch-fhd-ips-backlit-55whr-pure-silver"
)
HISTORY_FILE = Path("price_history.json")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_history():
    if not HISTORY_FILE.exists():
        return {}
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_history(history):
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def money(value):
    return f"৳{int(value):,}" if value is not None else "Not detected"


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub Secret."
        )
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Keep messages safely below Telegram's message length limit.
    response = requests.post(
        endpoint,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message[:3900]},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")


def main():
    print("Reading the fixed UCC product page...")
    product = read_product(UCC_PRODUCT_URL)
    if not product.get("price"):
        raise RuntimeError("UCC selling price was not detected; history not changed.")
    if not product.get("sku"):
        raise RuntimeError("UCC SKU was not detected; history not changed.")

    print(
        f"UCC product: {product.get('name')} | SKU={product.get('sku')} "
        f"| Price={money(product.get('price'))}"
    )
    retailer_results = search_all_retailers(product)
    history = load_history()
    sku = product["sku"].upper()
    previous = history.get(sku)
    current = {
        "ucc": product.get("price"),
        "retailers": {},
    }

    alerts = []
    if previous is None:
        alerts.append(
            "INITIAL BASELINE SAVED\n"
            "This is the first successful scan. Future price movements "
            "will trigger alerts."
        )
    else:
        old_ucc = previous.get("ucc")
        new_ucc = current["ucc"]
        if old_ucc is not None and old_ucc != new_ucc:
            direction = "INCREASED" if new_ucc > old_ucc else "DECREASED"
            alerts.append(
                f"UCC PRICE {direction}: {money(old_ucc)} → {money(new_ucc)}"
            )

    report_lines = [
        "UCC ACER PRICE CHANGE MONITOR",
        "============================",
        f"Product: {product.get('name')}",
        f"SKU: {sku}",
        f"UCC price: {money(product.get('price'))}",
        f"UCC link: {UCC_PRODUCT_URL}",
    ]

    for retailer, result in retailer_results.items():
        print(f"{retailer}: {result.get('status')}")
        report_lines.extend(["", f"{retailer}: {result.get('status')}"])
        if not result.get("results"):
            for error in result.get("errors", []):
                print(f"  ERROR: {error}")
                report_lines.append(f"Note: {error}")
            continue

        candidate = result["results"][0]
        match = compare_products(product, candidate)
        print(
            f"  SKU={candidate.get('sku')} price={money(candidate.get('price'))} "
            f"stock={candidate.get('stock_status', 'unknown')} "
            f"match={match['status']} checks={match['checks']}"
        )
        report_lines.extend([
            f"Product: {candidate.get('name')}",
            f"SKU: {candidate.get('sku') or 'Not detected'}",
            f"Price: {money(candidate.get('price'))}",
            f"Availability: {candidate.get('stock_status', 'Not reported')}",
            f"Spec match: {match['status']}",
            "Checks: " + ", ".join(
                f"{key}={value}" for key, value in match["checks"].items()
            ),
            f"Link: {candidate.get('url')}",
        ])

        if candidate.get("price_note"):
            report_lines.append(f"Price note: {candidate['price_note']}")

        # Never alert/compare a competitor price until all required specs match.
        if not match["price_comparison_allowed"]:
            report_lines.append("Price movement tracking: NOT ENABLED (specs unverified).")
            continue

        new_price = candidate.get("price")
        if new_price is None:
            report_lines.append("Price movement tracking: waiting for a readable price.")
            continue

        current["retailers"][retailer] = new_price
        old_price = (
            previous.get("retailers", {}).get(retailer)
            if previous else None
        )
        if old_price is not None and old_price != new_price:
            direction = "INCREASED" if new_price > old_price else "DECREASED"
            alerts.append(
                f"{retailer.upper()} PRICE {direction}: "
                f"{money(old_price)} → {money(new_price)}"
            )

    # Store only successful, verified current prices. Keep previous values if a
    # retailer is temporarily unavailable or its specs cannot be verified.
    if previous:
        merged = dict(previous.get("retailers", {}))
        merged.update(current["retailers"])
        current["retailers"] = merged
    history[sku] = current
    save_history(history)

    if alerts:
        message = "\n\n".join(alerts) + "\n\n" + "\n".join(report_lines)
        send_telegram(message)
        print("Telegram alert sent.")
    else:
        print("No verified price movement. No Telegram alert sent.")

    print("Scan completed.")


if __name__ == "__main__":
    main()
