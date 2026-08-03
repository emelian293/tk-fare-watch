#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tk-fare-watch
Turkmenistan Airlines (turkmenistanairlinestr.com) Asgabat -> Istanbul
ekonomi/business tarife izleyici + Telegram bildirim.

Tetikleyiciler:
  * YENI BILET  : kapali (0 sonuc) bir tarih satisa acilinca ya da yeni ucus/sinif cikinca
  * FIYAT DUSTU : izlenen bir (tarih, ucus, sinif) fiyati oncekine gore ucuzlayinca
Ayrica gunde 1 kez ozet mesaji.

Kullanim:
  python fare_watch.py                # tek tur tarama (Actions bunu calistirir)
  python fare_watch.py --dry-run      # Telegram gonderme + state yazma yok (parse testi)
  python fare_watch.py --test-alert   # Telegram'a ornek mesaj at ve cik
  python fare_watch.py --limit 5      # sadece ilk 5 tarih (hizli test)
  python fare_watch.py --date 06.09.2026   # tek tarih debug
"""

import os
import re
import sys
import json
import time
import argparse
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

# ============================================================
# AYARLAR
ORIGIN      = "ASB"                 # Asgabat
DEST        = "IST"                 # Istanbul
DATE_START  = date(2026, 8, 1)      # izleme penceresi baslangici (gecmis gunler atlanir)
DATE_END    = date(2026, 10, 31)    # izleme penceresi sonu
REQUEST_DELAY = 0.6                 # tarihler arasi nazik bekleme (sn)
REQUEST_TIMEOUT = 25                # her istek zaman asimi (sn)
FETCH_RETRIES = 3                   # basarisiz istekte tekrar sayisi
# Telegram bilgileri ORTAM DEGISKENINDEN gelir (kodda tutma!)
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
# ============================================================

BASE = "https://turkmenistanairlinestr.com"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
LOG_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.log")
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

GUN_ADI = ["Pazartesi", "Sali", "Carsamba", "Persembe", "Cuma", "Cumartesi", "Pazar"]


# --- Yardimcilar -------------------------------------------------------------

def log(msg):
    zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    satir = f"[{zaman}] {msg}"
    print(satir, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(satir + "\n")
    except Exception:
        pass


def search_url(d: date) -> str:
    return f"{BASE}/tr-TR/SearchResult/{ORIGIN}/{DEST}/{d.strftime('%d.%m.%Y')}/-/false/false/1/0/0/1"


def parse_price(s: str):
    """'1.695,00 $' -> 1695.0 ; '575,00 $' -> 575.0 (TR bicimi)."""
    if not s:
        return None
    m = re.search(r"([\d.]+,\d{2})", s)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def monitored_dates():
    start = max(date.today(), DATE_START)
    d = start
    out = []
    while d <= DATE_END:
        out.append(d)
        d += timedelta(days=1)
    return out


# --- Cekme & ayristirma ------------------------------------------------------

def parse_fares(html: str):
    """
    Sonuc sayfasindan (tarih, ucus, sinif) -> en dusuk fiyat cikarir.
    Donus: dict  { 'T5921|Business': {'price':575.0,'dep':'18:20','arr':'20:40','seats':3}, ... }
    """
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for box in soup.select(".result-box"):
        text = box.get_text(" ", strip=True)

        m = re.search(r"T5\d{3,4}", text)
        if not m:
            continue
        flight = m.group(0)

        cm = re.search(r"\b(Business|Ekonomi|Economy)\b", text)
        if not cm:
            continue
        cabin = "Business" if cm.group(1) == "Business" else "Ekonomi"

        ptag = box.select_one("[data-defaultprice]")
        price = parse_price(ptag["data-defaultprice"]) if (ptag and ptag.has_attr("data-defaultprice")) else None
        if price is None:
            price = parse_price(text)
        if price is None:
            continue

        dep = arr = None
        for col in box.select(".result-box-col"):
            h2 = col.find("h2")
            if not h2:
                continue
            t = h2.get_text(strip=True)
            if not re.match(r"^\d{1,2}:\d{2}$", t):
                continue
            span = col.find("span")
            label = span.get_text(strip=True) if span else ""
            if label == ORIGIN and dep is None:
                dep = t
            elif label == DEST and arr is None:
                arr = t

        sm = re.search(r"(\d+)\s*Koltuk", text)
        seats = int(sm.group(1)) if sm else None

        key = f"{flight}|{cabin}"
        prev = result.get(key)
        # Ayni ucus+sinif icin en dusuk fiyati tut (orn. promo eko vs esnek eko)
        if prev is None or price < prev["price"]:
            result[key] = {"price": price, "dep": dep, "arr": arr, "seats": seats}
    return result


def fetch_date(session: requests.Session, d: date):
    """
    Donus: ('ok', fares_dict) | ('empty', {}) | ('fail', None)
    'fail' = ag/HTTP hatasi ya da beklenmeyen sayfa -> state DEGISTIRILMEZ.
    """
    url = search_url(d)
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                log(f"  [{d}] HTTP {r.status_code} (deneme {attempt}/{FETCH_RETRIES})")
                time.sleep(2)
                continue
            html = r.text
            low = _strip_tr(html.lower())   # TR karakterleri normalize (i/ı, ç/c ...)
            valid_page = ("siralama" in low) or ("toplam" in low and "sonuc" in low)
            if not valid_page:
                log(f"  [{d}] Beklenmeyen sayfa (yonlendirme/engelleme?) (deneme {attempt}/{FETCH_RETRIES})")
                time.sleep(2)
                continue
            fares = parse_fares(html)
            if fares:
                return "ok", fares
            # Gecerli sayfa ama fare yok -> gercekten bos
            return "empty", {}
        except Exception as e:
            log(f"  [{d}] fetch hata: {e} (deneme {attempt}/{FETCH_RETRIES})")
            time.sleep(2)
    return "fail", None


def _strip_tr(s: str) -> str:
    # 'sonuç' -> 'sonuc' benzeri kaba normalize (marker eslesmesi icin)
    return (s.replace("ç", "c").replace("ğ", "g").replace("ı", "i")
             .replace("ö", "o").replace("ş", "s").replace("ü", "u"))


# --- Durum -------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
        except Exception as e:
            log(f"state.json okunamadi ({e}), sifirdan baslaniyor")
            st = {}
    else:
        st = {}
    st.setdefault("fares", {})          # 'YYYY-MM-DD|T5xxx|Cabin' -> price
    st.setdefault("dates_has_any", {})  # 'YYYY-MM-DD' -> bool
    st.setdefault("last_summary_date", None)
    st.setdefault("started", False)
    return st


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2, sort_keys=True)


# --- Telegram ----------------------------------------------------------------

def tg_send(text: str, dry=False):
    if dry or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("TELEGRAM (gonderilmedi/dry):\n" + text)
        return True
    ok = True
    for chunk in _chunks(text, 3500):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                      "disable_web_page_preview": "true"},
                timeout=20,
            )
            if r.status_code != 200:
                log(f"Telegram hata HTTP {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:
            log(f"Telegram gonderim hatasi: {e}")
            ok = False
        time.sleep(0.4)
    return ok


def _chunks(text, size):
    if len(text) <= size:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size:
            if cur:
                parts.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        parts.append(cur)
    return parts


# --- Bicimleme ---------------------------------------------------------------

def gun_etiketi(d: date) -> str:
    return f"{d.strftime('%d.%m.%Y')} {GUN_ADI[d.weekday()]}"


def fare_satiri(flight, cabin, info):
    dep = info.get("dep") or "--:--"
    arr = info.get("arr") or "--:--"
    seats = info.get("seats")
    seat_txt = f" ({seats} koltuk)" if seats else ""
    price = info["price"]
    price_txt = f"{price:.0f}$" if price == int(price) else f"{price:.2f}$"
    return f"  {dep}->{arr} {flight} · {cabin} · {price_txt}{seat_txt}"


# --- Ana akis ----------------------------------------------------------------

def run_once(dry=False, limit=None, only_date=None):
    st = load_state()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT,
                            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"})

    dates = monitored_dates()
    if only_date:
        dates = [only_date]
    if limit:
        dates = dates[:limit]

    baseline = not st.get("started")
    if baseline:
        log("Ilk calistirma: baseline kuruluyor (bildirim gonderilmeyecek).")

    new_avail = []   # (date, {key:info})  -> yeni bilet
    price_drop = []  # (date, flight, cabin, old, new, info)
    scanned_ok = 0
    have_any_dates = 0
    cheapest = {"Ekonomi": None, "Business": None}  # (price, date, flight) ozet icin

    for d in dates:
        diso = d.isoformat()
        status, fares = fetch_date(session, d)

        if status == "fail":
            log(f"[{diso}] atlandi (fetch fail) - state korunuyor")
            time.sleep(REQUEST_DELAY)
            continue

        scanned_ok += 1
        fares = fares or {}
        if fares:
            have_any_dates += 1

        # ozet icin en ucuzlari topla
        for key, info in fares.items():
            _f, cab = key.split("|")
            cur = cheapest.get(cab)
            if cur is None or info["price"] < cur[0]:
                cheapest[cab] = (info["price"], d, _f)

        prev_has = st["dates_has_any"].get(diso)
        prev_fares = {k.split("|", 1)[1]: v
                      for k, v in st["fares"].items() if k.startswith(diso + "|")}

        if not baseline and fares:
            if not prev_has:
                # tarih tamamen acildi
                new_avail.append((d, dict(fares)))
            else:
                new_keys = {k: v for k, v in fares.items() if k not in prev_fares}
                if new_keys:
                    new_avail.append((d, new_keys))
                for k, info in fares.items():
                    if k in prev_fares and info["price"] < prev_fares[k]:
                        fl, cab = k.split("|")
                        price_drop.append((d, fl, cab, prev_fares[k], info["price"], info))

        # --- state guncelle (yalnizca basarili parse edilen tarih) ---
        st["fares"] = {k: v for k, v in st["fares"].items() if not k.startswith(diso + "|")}
        for k, info in fares.items():
            st["fares"][f"{diso}|{k}"] = info["price"]
        st["dates_has_any"][diso] = bool(fares)

        log(f"[{diso}] {gun_etiketi(d).split()[1]:9} -> {len(fares)} tarife" +
            ("" if fares else " (bos)"))
        time.sleep(REQUEST_DELAY)

    log(f"Tarama bitti: {scanned_ok}/{len(dates)} tarih basarili, {have_any_dates} tarihte bilet var.")

    # --- Bildirimler ---
    if baseline:
        msg = _baseline_message(scanned_ok, have_any_dates, cheapest)
        tg_send(msg, dry=dry)
        st["started"] = True
        st["last_summary_date"] = date.today().isoformat()
    else:
        if new_avail:
            tg_send(_new_avail_message(new_avail), dry=dry)
        if price_drop:
            tg_send(_price_drop_message(price_drop), dry=dry)
        if not new_avail and not price_drop:
            log("Degisiklik yok, bildirim gonderilmedi.")
        # gunluk ozet
        today = date.today().isoformat()
        if scanned_ok and st.get("last_summary_date") != today:
            tg_send(_summary_message(scanned_ok, have_any_dates, cheapest), dry=dry)
            st["last_summary_date"] = today

    if not dry:
        save_state(st)
    else:
        log("--dry-run: state.json yazilmadi.")


def _cheapest_line(cheapest):
    lines = []
    for cab in ("Ekonomi", "Business"):
        c = cheapest.get(cab)
        if c:
            price, d, fl = c
            lines.append(f"  En ucuz {cab}: {price:.0f}$  ({gun_etiketi(d)}, {fl})")
    return "\n".join(lines) if lines else "  (su an satista bilet yok)"


def _baseline_message(scanned, have_any, cheapest):
    return ("✅ TK Fare-Watch baslatildi (ASB->IST)\n"
            f"Izleme araligi: {max(date.today(), DATE_START).strftime('%d.%m.%Y')} - "
            f"{DATE_END.strftime('%d.%m.%Y')}\n"
            f"Tarandi: {scanned} tarih · Bilet olan: {have_any} tarih\n"
            + _cheapest_line(cheapest) +
            "\nBundan sonra sadece yeni bilet ve fiyat dususlerinde haber verecegim.")


def _new_avail_message(new_avail):
    out = ["🟢 YENI BILET  ASB->IST"]
    for d, fares in sorted(new_avail, key=lambda x: x[0]):
        out.append(f"\n{gun_etiketi(d)}")
        for key, info in sorted(fares.items()):
            fl, cab = key.split("|")
            out.append(fare_satiri(fl, cab, info))
        out.append(f"  🔗 {search_url(d)}")
    return "\n".join(out)


def _price_drop_message(price_drop):
    out = ["🔻 FIYAT DUSTU  ASB->IST"]
    last_day = None
    for d, fl, cab, old, new, info in sorted(price_drop, key=lambda x: x[0]):
        if d != last_day:
            out.append(f"\n{gun_etiketi(d)}")
            last_day = d
        dep = info.get("dep") or "--:--"
        out.append(f"  {dep} {fl} · {cab}: {old:.0f}$ -> {new:.0f}$")
        out.append(f"  🔗 {search_url(d)}")
    return "\n".join(out)


def _summary_message(scanned, have_any, cheapest):
    return ("📊 Gunluk ozet (ASB->IST)\n"
            f"Tarandi: {scanned} tarih · Bilet olan: {have_any} tarih\n"
            + _cheapest_line(cheapest))


# --- CLI ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Turkmenistan Airlines ASB->IST fare watcher")
    ap.add_argument("--dry-run", action="store_true", help="Telegram gonderme + state yazma yok")
    ap.add_argument("--test-alert", action="store_true", help="Telegram'a ornek mesaj at ve cik")
    ap.add_argument("--limit", type=int, default=None, help="Sadece ilk N tarih")
    ap.add_argument("--date", type=str, default=None, help="Tek tarih debug (GG.AA.YYYY)")
    args = ap.parse_args()

    if args.test_alert:
        ok = tg_send("🔔 TK Fare-Watch test mesaji - kurulum calisiyor.")
        log("test-alert gonderildi." if ok else "test-alert BASARISIZ.")
        return

    only = None
    if args.date:
        only = datetime.strptime(args.date, "%d.%m.%Y").date()

    log("=" * 55)
    log("TK Fare-Watch calisiyor")
    run_once(dry=args.dry_run, limit=args.limit, only_date=only)
    log("Bitti.")
    log("=" * 55)


if __name__ == "__main__":
    main()
