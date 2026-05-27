"""
Simple Telegram Connectivity Test for VPS.
Run on VPS: venv/bin/python test_telegram.py
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_tg():
    token = os.getenv("TELEGRAM_SCALPER_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    print(f"--- Telegram Test ---")
    print(f"Token: {token[:10]}...{token[-5:] if token else 'None'}")
    print(f"Chat ID: {chat_id}")
    
    if not token or not chat_id:
        print("❌ Error: TELEGRAM_SCALPER_BOT_TOKEN or TELEGRAM_CHAT_ID not found in .env")
        return

    url = f"https://api.telegram.org/bot{token}/getMe"
    print(f"Testing connectivity to: {url}")
    
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            print("✅ Connection to Telegram API: SUCCESS")
            print(f"Bot info: {resp.json().get('result', {}).get('username')}")
        else:
            print(f"❌ Connection to Telegram API: FAILED (Status: {resp.status_code})")
            print(f"Response: {resp.text}")
            return
            
        # Try sending a message
        print("\nSending test message...")
        msg_url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(msg_url, json={
            "chat_id": chat_id,
            "text": "🛠 *VPS Connectivity Test*\nBot is online and can reach Telegram API.",
            "parse_mode": "Markdown"
        }, timeout=10)
        
        if resp.status_code == 200:
            print("✅ Test message: SENT SUCCESSFULLY")
        else:
            print(f"❌ Test message: FAILED (Status: {resp.status_code})")
            print(f"Response: {resp.text}")
            
    except Exception as e:
        print(f"❌ Network Error: {e}")
        print("\nPossible reasons:")
        print("1. Telegram API is blocked by your VPS provider or ISP.")
        print("2. DNS issues (try ping api.telegram.org).")
        print("3. You need to use a proxy (VLESS/VPN).")

if __name__ == "__main__":
    test_tg()
