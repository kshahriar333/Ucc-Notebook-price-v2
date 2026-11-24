# UCC Acer Price Change Monitor — sample product setup

## Files in this ZIP
- `price_bot.py` — reads the one supplied UCC product, checks retailer prices,
  compares the required specifications, and sends Telegram only for changes.
- `retailer_fetcher.py` — reads the three exact retailer product URLs.
- `.github/workflows/main.yml` — runs every 6 hours and saves `price_history.json`
  back to the repository so the next scan can detect increases/decreases.

## Replace these files in your existing repository
1. Replace `price_bot.py`.
2. Replace `retailer_fetcher.py`.
3. Replace `.github/workflows/main.yml`.
4. Keep your existing `ucc_reader.py`, `match_engine.py`, and `requirements.txt`.
5. Keep GitHub Secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Important
- First successful run sends an initial-baseline message and saves prices.
- Later successful scans send a Telegram message only when UCC or a fully
  specification-verified competitor price changes.
- If a competitor page is unavailable or required specs do not match, that
  retailer is not used for price-change alerts.
- This package monitors only the supplied UCC product SKU `UN.35MSI.02B`.
  Other SKUs need their own verified product URLs added to `DIRECT_PRODUCT_URLS`.
- GitHub Actions must be allowed to write repository contents:
  Repository Settings → Actions → General → Workflow permissions →
  Read and write permissions.
- The workflow is scheduled every 6 hours. GitHub scheduled jobs can start late.
- No live GitHub Actions execution was performed from this chat. Run
  `workflow_dispatch` once to establish the baseline and inspect the log.
