\
"""Periodic Telegram command responder for GitHub Actions.

GitHub Actions is not an always-on server. This workflow checks for Telegram
commands on a schedule, so replies can be delayed until the next scheduled run.
"""
import os
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_TIMEOUT = 15


def api(method, **kwargs):
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN GitHub Secret.")
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=kwargs,
        timeout=API_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error for {method}: {payload}")
    return payload.get("result")


def send(chat_id, text):
    api("sendMessage", chat_id=chat_id, text=text)


def command_name(text):
    first = (text or "").strip().split(maxsplit=1)[0].lower() if (text or "").strip() else ""
    return first.split("@", 1)[0]


def main():
    if not TOKEN or not ALLOWED_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub Secret.")

    # Fetch pending updates. Only accept messages from the configured chat.
    updates = api("getUpdates", offset=0, timeout=0, allowed_updates='["message"]') or []
    highest_update_id = None

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            highest_update_id = max(highest_update_id or update_id, update_id)

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if chat_id != str(ALLOWED_CHAT_ID):
            continue

        text = message.get("text", "")
        cmd = command_name(text)

        if cmd == "/start":
            send(
                chat_id,
                "Welcome to UCC Acer Notebook Price Monitor!\n\n"
                "I can send price-monitor reports from GitHub Actions.\n"
                "Commands:\n"
                "/start - Show this welcome message\n"
                "/status - Check monitor setup status\n\n"
                "Note: This bot checks commands periodically through GitHub Actions, "
                "so replies may take a few minutes.",
            )
        elif cmd == "/status":
            send(
                chat_id,
                "UCC Acer Notebook Price Monitor\n"
                "Telegram command service: active when its GitHub Actions workflow runs.\n"
                "Price scan schedule: every 6 hours.\n"
                "Use the GitHub Actions tab to see the latest scan result.",
            )

    # Telegram confirms updates only when a subsequent getUpdates call uses
    # an offset greater than the update_id. This prevents old commands replying again.
    if highest_update_id is not None:
        api(
            "getUpdates",
            offset=highest_update_id + 1,
            timeout=0,
            allowed_updates='["message"]',
        )
    print(f"Checked Telegram updates. Pending updates received: {len(updates)}")


if __name__ == "__main__":
    main()
