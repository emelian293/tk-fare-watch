#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram chat_id bulucu.
Kullanim:
  1) Telegram'da botuna ('@...') herhangi bir mesaj at (orn. "merhaba").
  2) TELEGRAM_TOKEN=... python get_chat_id.py
Cikti: sana yazan sohbet(ler)in chat_id'si.
"""
import os
import sys
import requests

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
if not TOKEN and len(sys.argv) > 1:
    TOKEN = sys.argv[1].strip()

if not TOKEN:
    print("HATA: TELEGRAM_TOKEN yok. Ornek: TELEGRAM_TOKEN=123:abc python get_chat_id.py")
    sys.exit(1)

r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=20)
data = r.json()
if not data.get("ok"):
    print("Telegram API hatasi:", data)
    sys.exit(1)

updates = data.get("result", [])
if not updates:
    print("Guncelleme yok. Once botuna bir mesaj at, sonra tekrar calistir.")
    sys.exit(0)

seen = {}
for u in updates:
    msg = u.get("message") or u.get("channel_post") or {}
    chat = msg.get("chat", {})
    if "id" in chat:
        seen[chat["id"]] = chat.get("title") or (
            (chat.get("first_name", "") + " " + chat.get("last_name", "")).strip()
            or chat.get("username", "")
        )

if not seen:
    print("chat_id bulunamadi. Botuna dogrudan mesaj attigina emin ol.")
else:
    print("Bulunan chat_id'ler:")
    for cid, name in seen.items():
        print(f"  {cid}   ({name})")
    print("\nBunu GitHub Secrets'a TELEGRAM_CHAT_ID olarak ekle.")
