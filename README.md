# UCC Acer Laptop Price Monitor

This monitor is intentionally limited to **Acer laptops only**.

## Product sources

- UCC source category: https://www.ucc.com.bd/laptops
  - The crawler opens the UCC laptop listing and keeps only product detail pages identified as Acer.
  - Non-Acer products, including motherboard/model pages such as Z590 and Z690, are excluded.
- Ryans Acer category: https://www.ryans.com/category/all-laptop-acer
- Star Tech Acer category: https://www.startech.com.bd/acer-laptop
- Techland Acer category: https://www.techlandbd.com/shop-laptop-computer/brand-laptops/shop-acer-laptop

Competitor products are discovered only from the three Acer category pages above. The monitor does not search the retailers' general laptop catalogues.

## Matching and alerts

1. Read the UCC Acer product title, SKU/MPN/Part Number and current price.
2. Build an index of Acer product pages from each supplied retailer category.
3. Match by exact normalized SKU only. Similar model names are not enough.
4. If the SKU is missing or does not match, do not compare the price.
5. Save previous verified prices in `state.json`.
6. Send Telegram alerts when a verified price changes, a monitored page repeatedly fails, or a page recovers.

The first successful run creates the baseline. Unmatched products are reported as not found/unverified; the system does not invent competitor prices.

## Schedule and setup

GitHub Actions runs every 6 hours and also runs after changes are pushed to `main`.

Required repository Actions secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Enable **Settings → Actions → General → Workflow permissions → Read and write permissions** so the workflow can save `products.json` and `state.json`.

## Run locally

```bash
pip install -r requirements.txt
python -m py_compile monitor.py catalog.py
python tests/test_monitor.py
python tests/test_catalog.py
python monitor.py --dry-run
```

The dry run accesses live pages but does not send Telegram messages or save monitoring state.

## Coverage notes

A product without a visible UCC SKU, a retailer listing without the same SKU, a blocked retailer page, or a changed site layout remains unverified. Check the Actions logs for exact counts and skipped pages. Retailer category structures and anti-bot protections can change; full coverage cannot be assumed until the live run reports its counts.
