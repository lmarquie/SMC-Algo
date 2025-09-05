import requests
from credentials import CHAT_ID, TOKEN
import time
import re
import ssl
import certifi
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# Create a session with proper SSL configuration and retry logic
def create_telegram_session():
    session = requests.Session()

    # Set up retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


# Create a global session instance
telegram_session = create_telegram_session()


def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message
    }

    try:
        response = telegram_session.get(
            url,
            params=payload,
            verify=certifi.where(),
            timeout=30
        )
        return response.json()
    except requests.exceptions.SSLError as e:
        print(f"SSL Error sending Telegram message: {e}")
        return {"ok": False, "error": f"SSL Error: {str(e)}"}
    except requests.exceptions.RequestException as e:
        print(f"Request Error sending Telegram message: {e}")
        return {"ok": False, "error": f"Request Error: {str(e)}"}
    except Exception as e:
        print(f"Unexpected error sending Telegram message: {e}")
        return {"ok": False, "error": f"Unexpected Error: {str(e)}"}


def is_stop_requested():
    """
    Returns True if a message containing the word 'STOP' (case-insensitive)
    was received in CHAT_ID within the last 2 minutes.
    """
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    try:
        resp = telegram_session.get(
            url,
            params={"limit": 100},
            verify=certifi.where(),
            timeout=10
        )
        data = resp.json()
    except Exception as e:
        print(f"Error checking stop request: {e}")
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
            resp = telegram_session.post(
                url,
                data=data,
                files=files,
                verify=certifi.where(),
                timeout=30
            )
        return resp.json()
    except requests.exceptions.SSLError as e:
        print(f"SSL Error sending Telegram image: {e}")
        return {"ok": False, "error": f"SSL Error: {str(e)}"}
    except requests.exceptions.RequestException as e:
        print(f"Request Error sending Telegram image: {e}")
        return {"ok": False, "error": f"Request Error: {str(e)}"}
    except Exception as e:
        print(f"Unexpected error sending Telegram image: {e}")
        return {"ok": False, "error": f"Unexpected Error: {str(e)}"}