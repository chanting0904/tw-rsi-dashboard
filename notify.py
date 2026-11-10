# -*- coding: utf-8 -*-
"""只在出問題或需要注意時發 Telegram 通知；每週買賣清單請看網頁。

沒有設定 TG_TOKEN / TG_CHAT_ID（例如本機）時不發送、也不報錯。
"""
import os

import requests


def alert(text):
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        print("（未設定 Telegram，略過通知）", text)
        return False
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data={"chat_id": chat, "text": text}, timeout=20)
        return response.ok
    except requests.RequestException as error:
        print("Telegram 通知失敗：", error)
        return False
