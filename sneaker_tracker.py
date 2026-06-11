import os
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

message = """
🚀 Sneaker Tracker avviato

Monitor:
- Travis Scott Tropical Pink
- SKU: IQ7604-101

Test GitHub → Telegram riuscito.
"""

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

requests.post(
    url,
    data={
        "chat_id": CHAT_ID,
        "text": message
    }
)

print("Telegram message sent")
