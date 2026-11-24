# UCC Acer Notebook Price Monitor

## What it does
- Reads Acer laptop product links from the public UCC Acer category page.
- Reads UCC SKU, model, processor, RAM, DDR type, SSD capacity and price when visible.
- Searches public search pages on Ryans, Star Tech and Techland.
- Searches in this order: exact UCC SKU, model, then product name.
- Compares price only when model, processor, RAM, DDR and SSD are all detected and match.
- Labels a same-spec product with a different SKU as `SPECIFICATION MATCH`, not exact SKU.
- Reports missing/blocked retailer results instead of inventing prices.

## GitHub setup
1. Upload all files and folders in this ZIP to the root of your repository, replacing the old versions.
2. In repository Settings -> Secrets and variables -> Actions, add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Open Actions -> UCC Acer Notebook Price Monitor -> Run workflow.

## Schedule
The workflow runs every 6 hours. GitHub scheduled workflows may start later than the exact minute.

## Important limitations
- This uses public retailer website pages only. It does not bypass login, CAPTCHA, rate limits or anti-bot blocks.
- Retailer HTML changes can break search result extraction.
- Missing specifications are marked UNKNOWN; prices are not compared in that case.
- The category crawler reports an error if it cannot discover any product pages. Always review the first Actions log after installation.
- A website may show a product listing price that differs from its live checkout price.
