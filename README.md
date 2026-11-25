# UCC All-Laptop Price Monitor

This package is designed to discover **all laptop product pages listed under UCC's `/laptops` category**, then search Ryans, Star Tech and Techland for the same manufacturer SKU / part number.

## What it does
- Re-discovers the UCC laptop catalogue on every scheduled run, including category pagination.
- Reads each UCC product page and extracts its SKU / part number.
- Searches each competitor by the exact SKU first.
- Opens candidate product pages and accepts them only when the expected SKU is visibly confirmed (or the exact SKU is present on the page).
- Does **not** guess a match from a similar product name when the SKU cannot be confirmed.
- Tracks UCC and competitor prices in `state.json`.
- Sends a Telegram message when a tracked price rises or falls, when a product is first added, when a site repeatedly fails, or when a site recovers.
- Sends nothing when no tracked price changes.
- Saves `products.json` and `state.json` to the repository after each run.

## Setup
1. Upload the complete contents of this ZIP into a GitHub repository (keep `.github/workflows/monitor.yml`).
2. Add repository Actions secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. In **Settings → Actions → General → Workflow permissions**, enable **Read and write permissions**.
4. In **Actions**, run **UCC All Laptop Price Monitor** manually once. The first run establishes the baseline and sends the initial report.
5. Later runs are scheduled every 6 hours.

## Important matching behaviour
- The known Acer Aspire 15 SKU `UN.35MSI.02B` has the exact three competitor URLs supplied by the user as a seed.
- Other SKUs are discovered through each retailer's SKU search.
- A product with no UCC SKU, or with no same-SKU competitor page found, is reported as unverified/not found. It is not treated as a price match.
- Retailer anti-bot protection, HTML changes, or search indexing delays can prevent some matches. Check the GitHub Actions log for those cases. No scraper can guarantee that every retailer page will be accessible on every run.

## Local test
```bash
pip install -r requirements.txt
python tests/test_monitor.py
python monitor.py --dry-run
```
`tests/test_monitor.py` is an offline test suite using sample pages. It does not prove that live retailer websites are currently accessible.
