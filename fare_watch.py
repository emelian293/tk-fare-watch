#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tk-fare-watch
Turkmenistan Airlines (turkmenistanairlinestr.com) Asgabat -> Istanbul
ekonomi/business tarife izleyici + Telegram bildirim.

Tetikleyiciler:
  * YENI BILET  : kapali (0 sonuc) bir tarih satisa acilinca ya da yeni ucus/sinif cikinca
  * FIYAT DUSTU : izlenen bir (tarih, ucus, sinif) fiyati oncekine gore ucuzlayinca
Ayrica: gunde 1 kez (ve --report ile elle istenince) TUM musait tarihlerin
tam listesi (ucus·saat·sinif·fiyat·koltuk sayisi).

Kullanim:
  python fare_watch.py                # tek tur tarama (Actions bunu calistirir)
  python fare_watch.py --report       # bu calismada tam listeyi gonder
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
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

# Cloudflare, veri-merkezi IP'lerine (GitHub Actions) duz requests ile 403 doner.
# curl_cffi Chrome'un TLS parmak izini taklit ederek bunu asar; yoksa requests'e duser.
try:
    from curl_cffi import requests as httpx
    _IMPERSONATE = "chrome"
except ImportError:
    import requests as httpx
    _IMPERSONATE = None

# ============================================================
# AYARLAR
ORIGIN      = "ASB"                 # Asgabat
DEST        = "IST"                 # Istanbul
DATE_START  = date(2026, 8, 1)      # izleme penceresi baslangici (gecmis gunler atlanir)
DATE_END    = date(2026, 10, 31)    # izleme penceresi sonu
MAX_WORKERS = 6                     # es zamanli istek sayisi (paralel tarama)
COLLAPSE_MIN = 5                    # onceden >=bu kadar tarihte bilet varken tarama 0 bulursa
                                    # -> olasi site sorunu say, state'i KORU (yanlis sifirlamayi onle)
REQUEST_TIMEOUT = 20                # her istek zaman asimi (sn)
FETCH_RETRIES = 3                   # basarisiz istekte tekrar sayisi
# Telegram bilgileri ORTAM DEGISKENINDEN gelir (kodda tutma!)
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
# Bildirim ayarlari (Telegram'dan /ayarlar ile yonetilir) Worker'dan okunur:
NOTIFY_CONFIG_URL = os.environ.get(
    "NOTIFY_CONFIG_URL", "https://tk-fare-bot.emelian293.workers.dev/config")
DEFAULT_NOTIFY = {
    "new_ticket": {"Ekonomi": True, "Premium": True, "Business_months": [8]},
    "price_drop": {"Ekonomi": True, "Business": True, "Premium": True},
}
# ============================================================

BASE = "https://turkmenistanairlinestr.com"
STATE_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
LATEST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest.json")
LOG_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.log")
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

GUN_ADI  = ["Pazartesi", "Sali", "Carsamba", "Persembe", "Cuma", "Cumartesi", "Pazar"]
GUN_KISA = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]   # Cuma/Cumartesi ayrimi
AY_ADI   = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
            "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
# Monospace tablo icin ASCII (diakritik hizayi bozmasin)
GUN_TBL  = ["Pzt", "Sal", "Car", "Per", "Cum", "Cmt", "Paz"]
AY_KISA  = ["", "Oca", "Sub", "Mar", "Nis", "May", "Haz",
            "Tem", "Agu", "Eyl", "Eki", "Kas", "Ara"]
# Ay adi -> ay no (bot sorgusu icin; TR + bazi ASCII varyantlar)
AY_NO = {"ocak":1,"subat":2,"şubat":2,"mart":3,"nisan":4,"mayis":5,"mayıs":5,
         "haziran":6,"temmuz":7,"agustos":8,"ağustos":8,"eylul":9,"eylül":9,
         "ekim":10,"kasim":11,"kasım":11,"aralik":12,"aralık":12}


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

def normalize_cabin(s):
    """Ham sinif etiketini standartlastir: Premium / Business / Ekonomi / (bilinmeyen)."""
    low = s.lower()
    if "premium" in low:
        return "Premium"
    if "business" in low:
        return "Business"
    if "econom" in low or "ekonom" in low:
        return "Ekonomi"
    return s.strip().title()


def detect_cabin(text):
    """Sinif etiketini genel olarak yakalar (Premium dahil). Bulamazsa None."""
    m = re.search(r"\d+\s*KG\s+([A-Za-zÇĞİÖŞÜçğıöşü ]{2,25}?)\s+(?:\d+\s*Koltuk|[\d.]+,\d{2})", text)
    raw = m.group(1) if m else None
    if not raw:
        m2 = re.search(r"\b(Business|Ekonomi|Economy|Premium)\b", text)
        if not m2:
            return None
        raw = m2.group(1)
    return normalize_cabin(raw)


def cabin_letter(cabin):
    return {"Ekonomi": "E", "Business": "B", "Premium": "P"}.get(cabin, (cabin[:1].upper() or "?"))


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

        cabin = detect_cabin(text)
        if not cabin:
            continue

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


def http_get(url):
    if _IMPERSONATE:
        return httpx.get(url, impersonate=_IMPERSONATE, timeout=REQUEST_TIMEOUT)
    return httpx.get(url, timeout=REQUEST_TIMEOUT, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })


def http_post(url, data):
    if _IMPERSONATE:
        return httpx.post(url, data=data, impersonate=_IMPERSONATE, timeout=20)
    return httpx.post(url, data=data, timeout=20)


def load_notify_config():
    """Bildirim ayarlarini Worker'dan (KV) oku; ulasilamazsa varsayilani kullan."""
    try:
        r = http_get(NOTIFY_CONFIG_URL)
        if r.status_code == 200:
            cfg = json.loads(r.text)
            nt = {**DEFAULT_NOTIFY["new_ticket"], **(cfg.get("new_ticket") or {})}
            pd = {**DEFAULT_NOTIFY["price_drop"], **(cfg.get("price_drop") or {})}
            return {"new_ticket": nt, "price_drop": pd}
    except Exception as e:
        log(f"Bildirim ayari alinamadi ({e}), varsayilan kullaniliyor.")
    return DEFAULT_NOTIFY


def _notify_new(cabin, d, cfg):
    """Bu (sinif, tarih) icin YENI BILET uyarisi gonderilsin mi?"""
    nt = cfg["new_ticket"]
    if cabin == "Business":
        return d.month in (nt.get("Business_months") or [])
    return bool(nt.get(cabin, False))   # Ekonomi/Premium -> True ise her ay


def fetch_date(d: date):
    """
    Donus: ('ok', fares_dict) | ('empty', {}) | ('fail', None)
    'fail' = ag/HTTP hatasi ya da beklenmeyen sayfa -> state DEGISTIRILMEZ.
    Thread-guvenli (her cagri kendi istegini yapar).
    """
    url = search_url(d)
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            r = http_get(url)
            if r.status_code != 200:
                if attempt == FETCH_RETRIES:
                    log(f"  [{d}] HTTP {r.status_code} (son deneme)")
                time.sleep(1.5)
                continue
            html = r.text
            low = _strip_tr(html.lower())   # TR karakterleri normalize (i/ı, ç/c ...)
            valid_page = ("siralama" in low) or ("toplam" in low and "sonuc" in low)
            if not valid_page:
                if attempt == FETCH_RETRIES:
                    log(f"  [{d}] Beklenmeyen sayfa (yonlendirme/engelleme?)")
                time.sleep(1.5)
                continue
            fares = parse_fares(html)
            if fares:
                return "ok", fares
            # Gecerli sayfa ama fare yok -> gercekten bos
            return "empty", {}
        except Exception as e:
            if attempt == FETCH_RETRIES:
                log(f"  [{d}] fetch hata: {e}")
            time.sleep(1.5)
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
    st.setdefault("collapse_warned", False)
    return st


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2, sort_keys=True)


# --- Telegram ----------------------------------------------------------------

def tg_send(text: str, dry=False, parse_mode=None):
    if dry or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("TELEGRAM (gonderilmedi/dry):\n" + text)
        return True
    ok = True
    for chunk in _chunks(text, 4000):
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                "disable_web_page_preview": "true"}
        if parse_mode:
            data["parse_mode"] = parse_mode
        try:
            r = http_post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data)
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

def run_once(dry=False, limit=None, only_date=None, force_report=False):
    st = load_state()

    dates = monitored_dates()
    if only_date:
        dates = [only_date]
    if limit:
        dates = dates[:limit]

    baseline = not st.get("started")
    if baseline:
        log("Ilk calistirma: baseline kuruluyor (bildirim gonderilmeyecek).")

    cfg = DEFAULT_NOTIFY if baseline else load_notify_config()

    # Cokme korumasi icin: taramadan onceki durumun anlik goruntusu
    prev_total = sum(1 for v in st["dates_has_any"].values() if v)
    snap_fares = dict(st["fares"])
    snap_has = dict(st["dates_has_any"])

    new_avail = []   # (date, {key:info})  -> yeni bilet
    price_drop = []  # (date, flight, cabin, old, new, info)
    scanned_ok = 0
    have_any_dates = 0
    cheapest = {}    # sinif -> (price, date, flight)  ozet icin

    # --- Tarihleri paralel cek (curl_cffi impersonate) ---
    engine = "curl_cffi" if _IMPERSONATE else "requests"
    log(f"{len(dates)} tarih {MAX_WORKERS} paralel istekle taraniyor ({engine})...")
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_date, d): d for d in dates}
        for fu in as_completed(futs):
            dd = futs[fu]
            try:
                results[dd] = fu.result()
            except Exception as e:
                results[dd] = ("fail", None)
                log(f"[{dd}] thread hata: {e}")

    for d in dates:
        diso = d.isoformat()
        status, fares = results.get(d, ("fail", None))

        if status == "fail":
            log(f"[{diso}] atlandi (fetch fail) - state korunuyor")
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
            # YENI BILET: sinif/ay kurallarina gore filtreli
            new_here = {}
            for k, info in fares.items():
                _fl, cab = k.split("|")
                is_new = (not prev_has) or (k not in prev_fares)
                if is_new and _notify_new(cab, d, cfg):
                    new_here[k] = info
            if new_here:
                new_avail.append((d, new_here))
            # FIYAT DUSUSU: sinif kuralina gore (mevcut fare ucuzladiysa)
            for k, info in fares.items():
                _fl, cab = k.split("|")
                if k in prev_fares and info["price"] < prev_fares[k] and cfg["price_drop"].get(cab, True):
                    price_drop.append((d, _fl, cab, prev_fares[k], info["price"], info))

        # --- state guncelle (yalnizca basarili parse edilen tarih) ---
        st["fares"] = {k: v for k, v in st["fares"].items() if not k.startswith(diso + "|")}
        for k, info in fares.items():
            st["fares"][f"{diso}|{k}"] = info["price"]
        st["dates_has_any"][diso] = bool(fares)

        log(f"[{diso}] {gun_etiketi(d).split()[1]:9} -> {len(fares)} tarife" +
            ("" if fares else " (bos)"))

    log(f"Tarama bitti: {scanned_ok}/{len(dates)} tarih basarili, {have_any_dates} tarihte bilet var.")

    # --- COKME KORUMASI: onceden cok bilet vardi, simdi hicbir tarihte yok -> olasi site sorunu ---
    if (not baseline) and scanned_ok > 0 and have_any_dates == 0 and prev_total >= COLLAPSE_MIN:
        log(f"!! Olasi site sorunu: onceden {prev_total} tarihte bilet vardi, simdi 0. "
            f"State/latest KORUNUYOR, bildirim yok.")
        st["fares"] = snap_fares          # eski veriyi geri koy (yanlis sifirlamayi onle)
        st["dates_has_any"] = snap_has
        if not st.get("collapse_warned"):
            st["collapse_warned"] = True
            tg_send(f"⚠️ Olası site sorunu: tarama hiç bilet bulamadı "
                    f"(önceden {prev_total} tarihte vardı). Veriler korundu, bir kontrol et.", dry=dry)
        if not dry:
            save_state(st)                # eski veri + uyarildi bayragi; latest.json'a DOKUNMA
        return
    if st.get("collapse_warned"):
        st["collapse_warned"] = False     # site duzeldi, bayragi temizle

    # --- Worker'in okuyacagi guncel veri ---
    if not dry:
        write_latest(results)

    # --- Bildirimler ---
    today = date.today().isoformat()
    if baseline:
        title = "✅ TK Fare-Watch başladı — mevcut biletler (ASB→İstanbul)"
        for pm, m in full_report_messages(results, title, cheapest):
            tg_send(m, dry=dry, parse_mode=pm)
        st["started"] = True
        st["last_summary_date"] = today
    else:
        if new_avail:
            tg_send(_new_avail_message(new_avail), dry=dry)
        if price_drop:
            tg_send(_price_drop_message(price_drop), dry=dry)
        if not new_avail and not price_drop:
            log("Degisiklik yok, anlik bildirim yok.")

        if force_report:
            # Elle istenen tam liste (ay ay tablo)
            for pm, m in full_report_messages(results, "📋 Mevcut biletler (ASB→İstanbul)", cheapest):
                tg_send(m, dry=dry, parse_mode=pm)
        elif scanned_ok and st.get("last_summary_date") != today:
            # Gunde 1 kez kisa ozet (tam liste botta 'ay' yazarak alinir)
            tg_send(daily_brief_message(results, cheapest), dry=dry, parse_mode="HTML")
            st["last_summary_date"] = today

    if not dry:
        save_state(st)
    else:
        log("--dry-run: state.json ve latest.json yazilmadi.")


def _cheapest_line(cheapest):
    lines = []
    for cab in ("Ekonomi", "Business", "Premium"):
        c = cheapest.get(cab)
        if c:
            price, d, fl = c
            lines.append(f"  En ucuz {cab}: {price:.0f}$  ({gun_etiketi(d)}, {fl})")
    return "\n".join(lines) if lines else "  (su an satista bilet yok)"


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


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tbl_row(c1, c2, c3, c4, c5, c6):
    return f"{c1:<6} {c2:<3} {c3:<5} {c4:<2} {c5:>6} {c6:>3}"


def _month_table(dates, results):
    """Bir ay icin hizali monospace tablo (basliksiz govde)."""
    lines = [_tbl_row("Tarih", "Gun", "Saat", "Sf", "Fiyat", "Klt")]
    for d in dates:
        fares = results[d][1]
        rows = []
        for key, info in fares.items():
            _fl, cab = key.split("|")
            rows.append((info.get("dep") or "--:--",
                         cabin_letter(cab),
                         info["price"], info.get("seats")))
        rows.sort(key=lambda r: (r[0], r[1]))
        ds, gn = f"{d.day:02d} {AY_KISA[d.month]}", GUN_TBL[d.weekday()]
        for t, c, p, s in rows:
            lines.append(_tbl_row(ds, gn, t, c, f"{p:.0f}$", str(s) if s else "-"))
    return "\n".join(lines)


def full_report_messages(results, title, cheapest):
    """
    Mevcut tum musait tarihleri AY AY, hizali tablo halinde doner.
    Donus: [(parse_mode, text), ...]  (her ay ayri mesaj -> Telegram sinirina takilmaz)
    """
    ok_dates = sorted(d for d, (s, f) in results.items() if s == "ok" and f)
    header = (f"<b>{_esc(title)}</b>\n"
              f"🎫 {len(ok_dates)} tarihte bilet · tek yön / USD\n"
              f"🟢 E=Ekonomi · 🔴 B=Business · Klt=son N koltuk\n"
              + _esc(_cheapest_line(cheapest)))
    msgs = [("HTML", header)]
    cur = []
    cur_m = None
    for d in ok_dates:
        if cur_m is not None and (d.year, d.month) != cur_m:
            msgs.append(_month_msg(cur, results))
            cur = []
        cur_m = (d.year, d.month)
        cur.append(d)
    if cur:
        msgs.append(_month_msg(cur, results))
    return msgs


def _month_msg(dates, results):
    d0 = dates[0]
    label = f"{AY_ADI[d0.month]} {d0.year}"
    return ("HTML", f"<b>{label}</b>\n<pre>{_esc(_month_table(dates, results))}</pre>")


def daily_brief_message(results, cheapest):
    ok = sum(1 for d, (s, f) in results.items() if s == "ok" and f)
    return ("📊 <b>Günlük durum — ASB→İstanbul</b>\n"
            f"🎫 {ok} tarihte bilet var · tek yön / USD\n"
            + _esc(_cheapest_line(cheapest)) +
            "\n\nDetay için bota ay adı yaz (ör. <b>eylül</b>) ya da <b>tümü</b>.")


def write_latest(results):
    """Worker'in okuyacagi tam veri (saat/sinif/fiyat/koltuk) -> latest.json."""
    board = {}
    for d, (s, f) in results.items():
        if s != "ok" or not f:
            continue
        arr = []
        for key, info in f.items():
            fl, cab = key.split("|")
            arr.append({"t": info.get("dep"), "c": cabin_letter(cab),
                        "p": info["price"], "s": info.get("seats"), "fl": fl})
        arr.sort(key=lambda x: (x["t"] or "", x["c"]))
        board[d.isoformat()] = arr
    data = {"updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "range": f"{max(date.today(), DATE_START).isoformat()}..{DATE_END.isoformat()}",
            "dates": board}
    with open(LATEST_FILE, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1, sort_keys=True)


# --- CLI ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Turkmenistan Airlines ASB->IST fare watcher")
    ap.add_argument("--dry-run", action="store_true", help="Telegram gonderme + state yazma yok")
    ap.add_argument("--test-alert", action="store_true", help="Telegram'a ornek mesaj at ve cik")
    ap.add_argument("--limit", type=int, default=None, help="Sadece ilk N tarih")
    ap.add_argument("--date", type=str, default=None, help="Tek tarih debug (GG.AA.YYYY)")
    ap.add_argument("--report", action="store_true",
                    help="Bu calismada mevcut tum biletlerin tam listesini gonder")
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
    run_once(dry=args.dry_run, limit=args.limit, only_date=only, force_report=args.report)
    log("Bitti.")
    log("=" * 55)


if __name__ == "__main__":
    main()
