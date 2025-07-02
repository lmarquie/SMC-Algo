import requests

def send_telegram_message(message):
    token = "8044176117:AAFNMRKBp5VcvJEJBuzuXNZF_Sm-gnaio18"
    chat_id = "7777458493"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")