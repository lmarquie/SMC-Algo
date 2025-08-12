import requests
from credentials import CHAT_ID, TOKEN

MESSAGE = 'Hello from your Telegram bot!'

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message
    }
    response = requests.get(url, params=payload)
    return response.json()
