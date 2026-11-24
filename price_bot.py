"""Entry point for the UCC Acer notebook price monitor."""
import os
import requests

from ucc_reader import get_acer_products
from retailer_fetcher import search_all_retailers
from match_engine import compare_products

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def money(value):
    return f"৳{int(value):,}" if isinstance(value, (int, float)) else "Not detected"


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub Secret.")
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Split messages below Telegram's 4096-character limit.
    chunks, current = [], ""
    for line in message.splitlines():
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)
    for chunk in chunks:
        response = requests.post(
            endpoint,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API rejected message: {payload}")


def build_report(product, retailer_results, index, total):
    lines = [
        f"UCC ACER PRICE MONITOR ({index}/{total})",
        "=" * 34,
        f"Product: {product.get('name') or 'Unknown'}",
        f"SKU: {product.get('sku') or 'Not detected'}",
        f"Model: {product.get('model') or 'Not detected'}",
        f"CPU: {product.get('processor') or 'Not detected'}",
        f"RAM: {product.get('ram') or 'Not detected'}",
        f"DDR: {product.get('ddr') or 'Not detected'}",
        f"SSD: {product.get('ssd') or 'Not detected'}",
        f"UCC price: {money(product.get('price'))}",
        f"UCC link: {product.get('url')}",
    ]
    for retailer, result in retailer_results.items():
        lines.extend(["", f"--- {retailer}: {result.get('status')} ---"])
        for error in result.get("errors", []):
            lines.append(f"Search note: {error}")
        candidates = result.get("results", [])
        if not candidates:
            lines.append("No product page retrieved; no price comparison.")
            continue
        for candidate in candidates:
            match = compare_products(product, candidate)
            lines.extend([
                f"Product: {candidate.get('name') or 'Unknown'}",
                f"Match: {match['status']}",
                "Spec check: " + ", ".join(
                    f"{key}={value}" for key, value in match["checks"].items()
                ),
            ])
            if match["price_comparison_allowed"]:
                lines.append(f"Competitor price: {money(candidate.get('price'))}")
                if candidate.get("price") is not None and product.get("price") is not None:
                    difference = int(candidate["price"]) - int(product["price"])
                    lines.append(f"Difference (competitor - UCC): {money(difference)}")
            else:
                lines.append("Price comparison: SKIPPED (specs mismatch or not fully verified)")
            lines.append(f"Product link: {candidate.get('url')}")
    return "\n".join(lines)


def main():
    print("Starting UCC Acer category scan...")
    products = get_acer_products()
    print(f"Discovered {len(products)} Acer product pages.")
    if not products:
        raise RuntimeError("No Acer products found.")

    successful = 0
    for index, product in enumerate(products, 1):
        print(f"[{index}/{len(products)}] {product.get('name')} | SKU={product.get('sku')}")
        try:
            results = search_all_retailers(product)
            report = build_report(product, results, index, len(products))
            send_telegram(report)
            successful += 1
        except Exception as exc:
            print(f"Product processing/report failed for {product.get('url')}: {exc!r}")
    print(f"Finished. Telegram reports sent: {successful}/{len(products)}")


if __name__ == "__main__":
    main()
