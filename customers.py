#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Musteri eslestirme — Google Sheets'ten kriterleri okur, yeni biletlerle eslestirir.

GIZLILIK KURALLARI (bilerek boyle):
  * KART bilgileri (kart no / CVV / son kullanma) HIC OKUNMAZ. Sutun tabloda olsa
    bile bu modul onlari istemez, tasimaz, yazmaz. Odeme daima elle yapilir.
  * Musteri bildirimi YALNIZCA sahibe (TELEGRAM_CHAT_ID) gider; broadcast EDILMEZ.
    Yoksa pasaport gibi bilgiler tum yetkili kullanicilara giderdi.
  * Loglara kisisel veri yazilmaz (sadece musteri adi ve sayilar).

Sheets sutunlari (1. satir baslik, sira onemsiz — baslik adiyla eslesir):
  Ad | Soyad | Cinsiyet | Telefon | E-posta | Pasaport No | Pasaport Bitis |
  Dogum Tarihi | Uyruk | Baslangic Tarihi | Bitis Tarihi | Bilet Turu | Aktif
"""

import os
import json
import unicodedata
from datetime import date, datetime

SHEET_ID = os.environ.get("CUSTOMERS_SHEET_ID", "").strip()
SHEET_RANGE = os.environ.get("CUSTOMERS_SHEET_RANGE", "A1:Z200").strip()
SA_JSON = os.environ.get("GOOGLE_SA_JSON", "").strip()

# Kart sutunlari: OKUNMAZ. Guvenlik icin acikca disarida birakilir.
BLOCKED = ("kart", "card", "cvv", "cvc", "sonkullanma", "sonkullanim", "expiry", "expire")


def _norm(s):
    """Baslik/deger normalize: kucuk harf, TR karakterler sade, bosluksuz."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.lower().replace(" ", "").replace("-", "").replace("_", "").strip()


def _parse_date(v):
    """'25.09.2026' / '2026-09-25' / '25/09/2026' -> date. Bos/hatali -> None."""
    v = str(v or "").strip()
    if not v:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _cabin_of(v):
    """Tablodaki bilet turu -> 'Ekonomi'/'Business'/'Premium'/None(=hepsi)."""
    n = _norm(v)
    if not n or n in ("hepsi", "tumu", "tum", "farketmez", "hepsibir", "all"):
        return None
    if "ekonom" in n or "econom" in n:
        return "Ekonomi"
    if "business" in n or "biznes" in n:
        return "Business"
    if "premium" in n:
        return "Premium"
    return None


def load_customers(log=print):
    """
    Sheets'ten musterileri oku. Donus: [ {ad, soyad, cinsiyet, telefon, eposta,
    pasaport, pasaport_bitis, dogum, uyruk, bas, bit, cabin, aktif}, ... ]
    Kurulum eksikse bos liste (sistem normal calismaya devam eder).
    """
    if not (SHEET_ID and SA_JSON):
        return []
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        log("google-auth kurulu degil; musteri eslestirme atlandi.")
        return []
    try:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(SA_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        sess = AuthorizedSession(creds)
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
               f"/values/{SHEET_RANGE}")
        r = sess.get(url, timeout=25)
        if r.status_code != 200:
            log(f"Sheets okunamadi (HTTP {r.status_code}) - musteri eslestirme atlandi.")
            return []
        rows = (r.json() or {}).get("values", [])
    except Exception as e:
        log(f"Sheets hatasi ({type(e).__name__}) - musteri eslestirme atlandi.")
        return []

    if len(rows) < 2:
        return []
    head = [_norm(h) for h in rows[0]]

    def col(row, *names):
        for nm in names:
            if nm in head:
                i = head.index(nm)
                if i < len(row):
                    return str(row[i]).strip()
        return ""

    out = []
    for row in rows[1:]:
        if not any(str(c).strip() for c in row):
            continue
        aktif = _norm(col(row, "aktif", "durum"))
        if aktif in ("hayir", "pasif", "kapali", "no", "0", "false"):
            continue
        bas = _parse_date(col(row, "baslangictarihi", "baslangic", "ilktarih"))
        bit = _parse_date(col(row, "bitistarihi", "bitis", "sontarih"))
        if not (bas and bit):
            continue    # kriter yoksa eslestirilemez
        out.append({
            "ad":       col(row, "ad", "isim", "adi"),
            "soyad":    col(row, "soyad", "soyadi"),
            "cinsiyet": col(row, "cinsiyet", "gender"),
            "telefon":  col(row, "telefon", "gsm", "tel"),
            "eposta":   col(row, "eposta", "email", "eposta"),
            "pasaport": col(row, "pasaportno", "pasaport"),
            "pasaport_bitis": col(row, "pasaportbitistarihi", "pasaportbitis"),
            "dogum":    col(row, "dogumtarihi", "dogum"),
            "uyruk":    col(row, "uyruk", "nationality", "pasaportulkesi"),
            "bas": bas, "bit": bit,
            "cabin": _cabin_of(col(row, "biletturu", "bilettipi", "sinif")),
        })
    return out


def match(customers, new_avail):
    """
    new_avail: [(date, {'T5921|Ekonomi': {price,dep,arr,seats}, ...}), ...]
    Donus: [ (musteri, tarih, cabin, info), ... ]  (her musteri kendi kriterine gore)
    """
    hits = []
    for d, fares in new_avail:
        for key, info in fares.items():
            _fl, cab = key.split("|")
            for c in customers:
                if not (c["bas"] <= d <= c["bit"]):
                    continue
                if c["cabin"] and c["cabin"] != cab:
                    continue
                hits.append((c, d, cab, dict(info, fl=_fl)))
    return hits
