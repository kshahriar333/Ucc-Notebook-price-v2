# UCC All-Laptop Price Monitor

This repository monitors the laptop products listed in the UCC **All Laptops** category and compares them with Ryans, Star Tech and Techland.

## How it works

1. On every run, `catalog.py` crawls UCC's `https://www.ucc.com.bd/laptops` category and its pagination.
2. It opens each discovered UCC product page and extracts the manufacturer SKU / part number.
3. It searches each retailer using that exact SKU and accepts a competitor page only when the SKU is confirmed on the page.
4. `monitor.py` reads prices from UCC and each verified competitor page, compares them with the previous saved prices, and sends Telegram alerts for price increases/decreases.
5. The workflow saves the refreshed product catalogue and monitoring state to `products.json` and `state.json`.

## Telegram alerts

- First run: sends a **Monitoring started** baseline message for each discovered product.
- Later runs: sends alerts only for verified price changes, repeated page failures, SKU mismatches, or page recovery.
- No price movement: no message.
- Missing competitor match: reported as **same-SKU product page not found**. It is never treated as a price match.
- SKU not visible on a competitor page: its price is not compared.
- All displayed prices are checked against UCC's current price in the report.

## Schedule

GitHub Actions runs every 6 hours. You can also start **UCC All Laptop Price Monitor** manually from the repository's Actions tab.

## Required GitHub configuration

Add these repository Actions secrets (never put their values in source files):

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Then open **Settings → Actions → General → Workflow permissions** and enable **Read and write permissions** so the workflow can save `products.json` and `state.json`.

## Local run

```bash
pip install -r requirements.txt
python monitor.py --dry-run
```

The dry run still needs internet access to read UCC and retailer pages, but it does not send Telegram messages or overwrite the monitoring state.

## Matching limitations

The monitor prioritizes correctness over guessed matches. If a UCC listing has no readable SKU, a retailer search does not return a matching product, a competitor page hides the SKU, or a retailer blocks automated requests, that comparison remains unverified. Retailer page layouts and anti-bot protections can change, so check the GitHub Actions logs for coverage warnings. The system cannot guarantee every retailer listing will be accessible on every run.
