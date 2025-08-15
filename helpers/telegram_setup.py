import requests
from credentials import CHAT_ID, TOKEN
import time
import re

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message
    }
    response = requests.get(url, params=payload)
    return response.json()


def is_stop_requested():
    """
    Returns True if a message containing the word 'STOP' (case-insensitive)
    was received in CHAT_ID within the last 2 minutes.
    """
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    try:
        resp = requests.get(url, params={"limit": 100}, timeout=10)
        data = resp.json()
    except Exception:
        return False

    if not isinstance(data, dict) or not data.get("ok"):
        return False

    cutoff = int(time.time()) - 5
    for upd in reversed(data.get("result", [])):
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue

        chat = msg.get("chat", {})
        if str(chat.get("id")) != str(CHAT_ID):
            continue

        if int(msg.get("date", 0)) < cutoff:
            continue

        text = msg.get("text") or ""
        if re.search(r"\bSTOP\b", text, flags=re.IGNORECASE):
            return True

    return False


def send_telegram_image(filepath, caption=None, disable_notification=False):
    """
    Sends an image (from local filepath) to the Telegram chat.
    Returns Telegram's JSON response dict.
    """
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    data = {
        "chat_id": CHAT_ID,
        "caption": caption or "",
        "disable_notification": disable_notification,
    }
    try:
        with open(filepath, "rb") as f:
            files = {"photo": (filepath, f)}
            resp = requests.post(url, data=data, files=files, timeout=30)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}





