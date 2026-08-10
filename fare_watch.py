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
import threading
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
MAX_WORKERS = 3                     # es zamanli istek sayisi (site yuke duyarli: dusuk tut)
COLLAPSE_MIN = 5                    # saglikli tarama esigi (baseline + tam-cokme algisi)
DISAPPEAR_AFTER = 1                 # bos DOGRULANDIKTAN sonra kac tarama beklensin (EMPTY_VERIFY zaten 3 kez dogruluyor)
                                    # (site cirpinmasinda yanlis "yeni bilet" uyarisini onler)
ZERO_WARN = 4                       # tarama kac ardisik kez TAM 0 bulursa (~20 dk) uyari at
EMPTY_VERIFY = 2                    # 2. asamada bir 'bos' sonuc kac kez daha dogrulansin
SUSPECT_RATIO = 0.25                # bilinen dolu tarihlerin bu oranindan fazlasi ayni anda bosaldiysa
                                    # -> site throttle ediyor say, veriyi DONDUR (toplu yanlis silme olmaz)
OUTAGE_RATIO = 0.5                  # tarama, bilinen dolu tarihlerin bu oraninin altina duserse
                                    # tarama GUVENILMEZ say -> veriyi dondur (yanlis bosalmayi onle)
REQUEST_TIMEOUT = 20                # her istek zaman asimi (sn)
FETCH_RETRIES = 3                   # basarisiz istekte tekrar sayisi
# Telegram bilgileri ORTAM DEGISKENINDEN gelir (kodda tutma!)
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
# Bildirim ayarlari (Telegram'dan /ayarlar ile yonetilir) Worker'dan okunur:
NOTIFY_CONFIG_URL = os.environ.get(
    "NOTIFY_CONFIG_URL", "https://tk-fare-bot.emelian293.workers.dev/config")
# Yetkili TUM kullanicilara bildirim: Worker /broadcast (owner + KV izinliler)
WORKER_BASE = NOTIFY_CONFIG_URL.rsplit("/config", 1)[0]
BROADCAST_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
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


_AY_RE = re.compile(
    r"(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)")


def box_departure_date(text, ref_year):
    """Bir result-box metnindeki ILK 'GG Ay' (kalkis tarihi) -> date. Bulamazsa None."""
    m = _AY_RE.search(text)
    if not m:
        return None
    mon = AY_NO.get(m.group(2).lower())
    if not mon:
        return None
    try:
        return date(ref_year, mon, int(m.group(1)))
    except ValueError:
        return None


def parse_fares(html: str, query_date=None):
    """
    Sonuc sayfasindan (tarih, ucus, sinif) -> en dusuk fiyat cikarir.
    query_date verilirse: kutu-ici KALKIS tarihi bu tarihle uyusmayan kutular ELENIR
    (sitenin 'yakin tarih onerisi' hayaletlerini onler -> yanlis pozitif kalkar).
    Donus: dict  { 'T5921|Business': {'price':575.0,'dep':'18:20','arr':'20:40','seats':3}, ... }
    """
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    mismatch = []
    for box in soup.select(".result-box"):
        text = box.get_text(" ", strip=True)

        m = re.search(r"T5\d{3,4}", text)
        if not m:
            continue
        flight = m.group(0)

        # TARIH DOGRULAMA: kutunun kalkis tarihi sorgu tarihiyle ayni olmali
        if query_date is not None:
            bdd = box_departure_date(text, query_date.year)
            if bdd is not None and bdd != query_date:
                mismatch.append(f"{flight}@{bdd.isoformat()}")
                continue

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
    if mismatch:
        log(f"  [{query_date}] tarih-uyumsuz {len(mismatch)} kutu ELENDI (hayalet oneri?): {mismatch[:5]}")
    return result


_tls = threading.local()


def _session():
    """
    Her is parcacigi icin, ANA SAYFA ziyaretiyle isitilmis bir oturum.
    Gercek tarayici gibi davranir (ASP.NET_SessionId cerezi alir); soguk,
    cerezsiz isteklere gore site cok daha kararli sonuc donduruyor.
    """
    s = getattr(_tls, "s", None)
    if s is None:
        s = httpx.Session(impersonate=_IMPERSONATE) if _IMPERSONATE else httpx.Session()
        if not _IMPERSONATE:
            s.headers.update({
                "User-Agent": USER_AGENT,
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
        try:
            s.get(BASE + "/tr-TR", timeout=REQUEST_TIMEOUT)   # oturum cerezini al
        except Exception:
            pass
        _tls.s = s
    return s


def http_get(url):
    return _session().get(url, timeout=REQUEST_TIMEOUT)


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


def _fetch_once(d: date):
    """Tek deneme -> ('ok', fares) | ('empty', {}) | ('fail', None)."""
    try:
        r = http_get(search_url(d))
        if r.status_code != 200:
            return "fail", None
        html = r.text
        low = _strip_tr(html.lower())   # TR karakterleri normalize (i/ı, ç/c ...)
        if not (("siralama" in low) or ("toplam" in low and "sonuc" in low)):
            return "fail", None         # yonlendirme/engelleme sayfasi
        fares = parse_fares(html, d)
        return ("ok", fares) if fares else ("empty", {})
    except Exception:
        return "fail", None


def fetch_date(d: date, verify_empty=False):
    """
    1. asama: nazik tek gecis. 'fail' (ag/HTTP) durumunda birkac kez dener;
    'empty' sonucu OLDUGU GIBI dondurur (dogrulama 2. asamada, sadece supheliler icin).
    verify_empty parametresi geriye donuk uyumluluk icin durur.
    """
    for _ in range(FETCH_RETRIES):
        s, f = _fetch_once(d)
        if s in ("ok", "empty"):
            return s, f
        time.sleep(1.2)
    log(f"  [{d}] dogrulanamadi - eski veri korunuyor")
    return "fail", None


def verify_empties(results, st):
    """
    2. ASAMA — yanlis "tukendi" alarmini onler.
    Onceden DOLU olup bu taramada BOS donen tarihler 'suphelidir': site yuk altinda
    gecici olarak bos sayfa servis edebiliyor.
      * Supheli sayisi cok ise (> SUSPECT_RATIO)  -> site throttle ediyor: hicbirine
        dokunma, hepsini 'fail' yap (veri korunur, yanlis toplu silme olmaz).
      * Supheli sayisi az ise -> her birini yavas yavas yeniden sor. Tekrar dolu
        gelirse gercek veri geri alinir; israrla bos ise gercekten tukenmistir.
    Donus: throttle suphesi var mi (bool)
    """
    suspects = [d for d, (s, _f) in results.items()
                if s == "empty" and st["dates_has_any"].get(d.isoformat())]
    if not suspects:
        return False
    prev_total = sum(1 for v in st["dates_has_any"].values() if v)
    if prev_total >= COLLAPSE_MIN and len(suspects) > prev_total * SUSPECT_RATIO:
        log(f"!! {len(suspects)}/{prev_total} tarih ayni anda bosaldi -> throttle suphesi, "
            f"hicbiri silinmiyor (veri korunuyor).")
        for d in suspects:
            results[d] = ("fail", None)
        return True

    log(f"2. asama: {len(suspects)} supheli tarih yeniden dogrulaniyor...")
    for d in suspects:
        for _ in range(EMPTY_VERIFY):
            time.sleep(1.0)
            s, f = _fetch_once(d)
            if s == "ok" and f:
                results[d] = ("ok", f)
                log(f"  [{d}] aslinda DOLU (gecici bos yanit) - duzeltildi")
                break
        else:
            log(f"  [{d}] bos dogrulandi -> gercekten tukendi")
    return False


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
    st.setdefault("misses", {})         # 'YYYY-MM-DD' -> ardisik bos tarama sayisi (debounce)
    st.setdefault("zero_streak", 0)     # ardisik TAM-0 tarama sayisi (tam cokme algisi)
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


def check_customers(dry=False):
    """
    Kurulum dogrulama: Sheets okunuyor mu, kriterler dogru mu?
    Actions loglari PUBLIC oldugu icin loga SADECE sayilar yazilir;
    isim/kriter detayi yalnizca Telegram'a (sahibe) gider.
    """
    import customers as cust
    people = cust.load_customers(log=log)
    d = cust.LAST_DIAG
    if not people:
        # Basliklar ve sayilar kisisel veri degil -> loga yazilabilir (tani icin sart)
        log(f"SONUC: 0 gecerli musteri. TANI: {d if d else 'Sheets hic okunamadi (yetki/ID?)'}")
        msg = ["⚠️ <b>Geçerli müşteri satırı bulunamadı</b>\n"]
        if not d:
            msg.append("Tabloya <b>hiç erişilemedi</b>. Muhtemel sebep:\n"
                       "• Tablo servis hesabına paylaşılmadı\n• Yanlış tablo kimliği")
        else:
            msg.append(f"Okunan satır: <b>{d.get('satir', 0)}</b>")
            if d.get("basliklar"):
                msg.append("Bulunan başlıklar:\n<code>"
                           + _esc(" | ".join(str(x) for x in d["basliklar"])) + "</code>")
            if d.get("neden"):
                msg.append(f"Sebep: <b>{_esc(d['neden'])}</b>")
            msg.append("\nGerekli: <b>Başlangıç Tarihi</b> ve <b>Bitiş Tarihi</b> "
                       "sütunları dolu olmalı (örn. <code>25.09.2026</code>).")
        tg_send("\n".join(msg), dry=dry, parse_mode="HTML")
        return
    log(f"SONUC: {len(people)} musteri okundu, kriterler gecerli. (detay Telegram'a gonderildi)")
    lines = [f"✅ <b>Müşteri tablosu okundu</b> — {len(people)} kayıt\n"]
    for c in people:
        ad = f"{c.get('ad','')} {c.get('soyad','')}".strip()
        cab = c["cabin"] or "Hepsi"
        eksik = [k for k, v in (("pasaport", c.get("pasaport")), ("doğum", c.get("dogum")),
                                ("telefon", c.get("telefon")), ("e-posta", c.get("eposta")))
                 if not v]
        lines.append(f"• <b>{_esc(ad)}</b> — {c['bas'].strftime('%d.%m.%Y')}–"
                     f"{c['bit'].strftime('%d.%m.%Y')} · {cab}"
                     + (f"\n   ⚠️ eksik: {', '.join(eksik)}" if eksik else ""))
    lines.append("\nEşleşen bilet çıktığında bildirim gelecek.")
    tg_send("\n".join(lines), dry=dry, parse_mode="HTML")


def notify_customers(new_avail, st, dry=False):
    """
    Yeni biletleri Sheets'teki musteri kriterleriyle eslestirip SAHIBE bildirir.
    * Yalnizca tg_send (sahip) kullanilir; broadcast DEGIL -> kisisel veri yayilmaz.
    * Ayni (musteri, tarih, ucus, sinif) bir daha bildirilmez (state['notified']).
    * Kart bilgisi ne okunur ne yazilir; odemeyi kullanici elle tamamlar.
    """
    try:
        import customers as cust
    except Exception:
        return
    people = cust.load_customers(log=log)
    if not people:
        return
    hits = cust.match(people, new_avail)
    if not hits:
        return

    seen = set(st.setdefault("notified", []))
    sent = 0
    for c, d, cab, info in hits:
        fl = info.get("fl") or ""
        key = f"{_norm_name(c)}|{d.isoformat()}|{fl}|{cab}|{info['price']:.0f}"
        if key in seen:
            continue
        seen.add(key)
        tg_send(_customer_message(c, d, cab, info), dry=dry, parse_mode="HTML")
        sent += 1
    st["notified"] = sorted(seen)[-800:]     # listeyi sinirla (eski kayitlar dusulur)
    if sent:
        log(f"{sent} musteri eslesmesi bildirildi ({len(people)} musteri tarandi).")


def _norm_name(c):
    """
    Musteri kimligini KISA HASH'e cevirir. state.json public repoya commit edildigi
    icin oraya isim yazilmaz; sadece tekrar-bildirimi engelleyecek kadar kimlik tutulur.
    """
    import hashlib
    raw = f"{c.get('ad','')}|{c.get('soyad','')}|{c.get('pasaport','')}".strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _customer_message(c, d, cab, info):
    """Dokun-kopyala formatinda musteri bildirimi (KART BILGISI YOK)."""
    def row(label, val):
        return f"{label} <code>{_esc(val)}</code>" if val else ""
    seats = info.get("seats")
    lines = [
        f"🎯 <b>{_esc((c.get('ad','') + ' ' + c.get('soyad','')).strip())}</b> için uygun bilet!",
        f"{_gun_baslik(d)} · {cab} · <b>{info['price']:.0f}$</b>"
        + (f" · son {seats} koltuk" if seats else ""),
        "",
        "<b>Yolcu bilgileri</b> — değere dokun, kopyalanır:",
    ]
    for lbl, val in (("Ad", c.get("ad")), ("Soyad", c.get("soyad")),
                     ("Cinsiyet", c.get("cinsiyet")), ("Doğum", c.get("dogum")),
                     ("Pasaport", c.get("pasaport")),
                     ("Pas. bitiş", c.get("pasaport_bitis")),
                     ("Uyruk", c.get("uyruk")), ("Telefon", c.get("telefon")),
                     ("E-posta", c.get("eposta"))):
        r = row(lbl, val)
        if r:
            lines.append(r)
    lines += ["", f'▶️ <a href="{search_url(d)}">Bileti aç</a> → Seç → Devam Et',
              "💳 Kartı telefonun kendi otomatik doldurmasıyla gir; SATIN AL'a sen bas."]
    return "\n".join(lines)


def send_heartbeat(found, dry=False):
    """
    'Son kontrol' bilgisini Worker'a (KV) yaz. Boylece bot, veri degismemis olsa bile
    "son kontrol: 2 dk once" diyebilir -> kullanici listenin taze mi bayat mi oldugunu bilir.
    Git commit'i gerektirmez (her 5 dk commit spam'i olmasin diye).
    """
    if dry or not (WORKER_BASE and BROADCAST_SECRET):
        return
    try:
        kw = {"impersonate": _IMPERSONATE} if _IMPERSONATE else {}
        httpx.post(WORKER_BASE.rstrip("/") + "/heartbeat",
                   json={"secret": BROADCAST_SECRET, "found": found}, timeout=15, **kw)
    except Exception as e:
        log(f"heartbeat gonderilemedi: {e}")


def broadcast(text, parse_mode=None, dry=False):
    """Yeni bilet / fiyat dususu -> yetkili TUM kullanicilara (owner + KV izinliler).
    Worker'in /broadcast ucuna gonderir; basarisizsa sadece owner'a duser."""
    if dry:
        log("BROADCAST (dry):\n" + text)
        return True
    if WORKER_BASE and BROADCAST_SECRET:
        try:
            payload = {"secret": BROADCAST_SECRET, "text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            url = WORKER_BASE.rstrip("/") + "/broadcast"
            kw = {"impersonate": _IMPERSONATE} if _IMPERSONATE else {}
            r = httpx.post(url, json=payload, timeout=20, **kw)
            if r.status_code == 200:
                log("broadcast: yetkili kullanicilara gonderildi.")
                return True
            log(f"broadcast HTTP {r.status_code}: {r.text[:150]} -> owner'a dusuluyor")
        except Exception as e:
            log(f"broadcast hata: {e} -> owner'a dusuluyor")
    return tg_send(text, dry=dry, parse_mode=parse_mode)


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

    prev_total = sum(1 for v in st["dates_has_any"].values() if v)

    # --- Tarihleri paralel cek ---
    engine = "curl_cffi" if _IMPERSONATE else "requests"
    log(f"{len(dates)} tarih {MAX_WORKERS} paralel istekle taraniyor ({engine})...")
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        # Onceden DOLU bilinen tarihlerde "bos" sonucu dogrula (yanlis bosalmayi onler)
        futs = {ex.submit(fetch_date, d,
                          bool(st["dates_has_any"].get(d.isoformat()))): d for d in dates}
        for fu in as_completed(futs):
            dd = futs[fu]
            try:
                results[dd] = fu.result()
            except Exception as e:
                results[dd] = ("fail", None)
                log(f"[{dd}] thread hata: {e}")

    # 2. asama: "tukendi" sanilan tarihleri dogrula (gecici bos yanitlari duzeltir)
    if not baseline:
        verify_empties(results, st)

    scanned_ok = sum(1 for (s, _) in results.values() if s != "fail")
    scan_found = sum(1 for (s, f) in results.values() if s == "ok" and f)
    today = date.today().isoformat()

    cheapest = {}    # sinif -> (price, date, flight)
    for d, (s, f) in results.items():
        if s != "ok":
            continue
        for key, info in (f or {}).items():
            _fl, cab = key.split("|")
            cur = cheapest.get(cab)
            if cur is None or info["price"] < cur[0]:
                cheapest[cab] = (info["price"], d, _fl)

    log(f"Tarama bitti: {scanned_ok}/{len(dates)} basarili, ham {scan_found} tarihte bilet.")

    # --- GUVENILMEZ TARAMA (outage / sessiz engelleme): veriyi DONDUR (mutasyon yok) ---
    # Sadece "tam 0" degil, ANI BUYUK DUSUS de guvenilmezdir: site bos sayfa servis
    # ediyorsa liste yanlis bosalmasin. Gercek tukenmeler tek tek olur, toptan degil.
    if (not baseline) and scanned_ok > 0 and prev_total >= COLLAPSE_MIN \
            and scan_found < prev_total * OUTAGE_RATIO:
        st["zero_streak"] = st.get("zero_streak", 0) + 1
        log(f"!! Guvenilmez tarama: {scan_found} bilet (onceden {prev_total}). "
            f"Veri donduruldu (streak={st['zero_streak']}).")
        if st["zero_streak"] >= ZERO_WARN and not st.get("collapse_warned"):
            st["collapse_warned"] = True
            tg_send(f"⚠️ Site ~{ZERO_WARN*5} dakikadır güvenilir sonuç döndürmüyor "
                    f"(olası geçici site sorunu). Liste dondurulduğu için "
                    f"gösterilen veriler bir miktar eski olabilir.", dry=dry)
        if not dry:
            save_state(st)
        return
    st["zero_streak"] = 0
    if st.get("collapse_warned"):
        st["collapse_warned"] = False

    # --- BASELINE: yalnizca SAGLIKLI tarama ile kur (cirpinma sirasinda erteler) ---
    if baseline:
        if scan_found < COLLAPSE_MIN:
            log(f"Baseline erteleniyor: saglksz tarama ({scan_found} bilet).")
            return
        for d, (s, f) in results.items():
            if s == "ok" and f:
                diso = d.isoformat()
                for k, info in f.items():
                    st["fares"][f"{diso}|{k}"] = info["price"]
                st["dates_has_any"][diso] = True
        if not dry:
            write_latest(results, st)
        for pm, m in full_report_messages(
                results, "✅ TK Fare-Watch senkronize edildi — mevcut biletler (ASB→İstanbul)", cheapest):
            tg_send(m, dry=dry, parse_mode=pm)
        st["started"] = True
        st["last_summary_date"] = today
        if not dry:
            save_state(st)
        return

    # --- NORMAL: per-tarih debounce + degisiklik tespiti ---
    new_avail = []
    price_drop = []
    for d in dates:
        diso = d.isoformat()
        status, fares = results.get(d, ("fail", None))
        if status == "fail":
            continue
        prev_has = st["dates_has_any"].get(diso)
        prev_fares = {k.split("|", 1)[1]: v
                      for k, v in st["fares"].items() if k.startswith(diso + "|")}

        if status == "empty" or not fares:
            # Bos dondu: onceden doluysa HEMEN silme (cirpinma) -> sayaci artir, veriyi KORU
            if prev_has:
                miss = st["misses"].get(diso, 0) + 1
                if miss >= DISAPPEAR_AFTER:
                    st["fares"] = {k: v for k, v in st["fares"].items() if not k.startswith(diso + "|")}
                    st["dates_has_any"][diso] = False
                    st["misses"].pop(diso, None)
                else:
                    st["misses"][diso] = miss
            else:
                st["dates_has_any"][diso] = False
                st["misses"].pop(diso, None)
            continue

        # Dolu tarih
        st["misses"].pop(diso, None)
        new_here = {}
        for k, info in fares.items():
            _fl, cab = k.split("|")
            is_new = (not prev_has) or (k not in prev_fares)
            if is_new and _notify_new(cab, d, cfg):
                new_here[k] = info
        if new_here:
            new_avail.append((d, new_here))
        for k, info in fares.items():
            _fl, cab = k.split("|")
            if k in prev_fares and info["price"] < prev_fares[k] and cfg["price_drop"].get(cab, True):
                price_drop.append((d, _fl, cab, prev_fares[k], info["price"], info))
        st["fares"] = {k: v for k, v in st["fares"].items() if not k.startswith(diso + "|")}
        for k, info in fares.items():
            st["fares"][f"{diso}|{k}"] = info["price"]
        st["dates_has_any"][diso] = True

    if not dry:
        write_latest(results, st)
        send_heartbeat(scan_found)   # "son kontrol" damgasi (veri degismese de tazelik belli olsun)

    # --- Bildirimler: yeni bilet + fiyat dususu -> YETKILI HERKESE (broadcast) ---
    if new_avail:
        broadcast(_new_avail_message(new_avail), parse_mode="HTML", dry=dry)
        notify_customers(new_avail, st, dry=dry)   # kriterle eslesen musteriler (sadece sahibe)
    if price_drop:
        broadcast(_price_drop_message(price_drop), parse_mode="HTML", dry=dry)
    if not new_avail and not price_drop:
        log("Degisiklik yok, anlik bildirim yok.")

    if force_report:
        for pm, m in full_report_messages(results, "📋 Mevcut biletler (ASB→İstanbul)", cheapest):
            tg_send(m, dry=dry, parse_mode=pm)
    elif scan_found >= COLLAPSE_MIN and st.get("last_summary_date") != today:
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


def _gun_baslik(d):
    return f"{d.day} {AY_ADI[d.month]} {GUN_ADI[d.weekday()]}"


def _new_avail_message(new_avail):
    out = ["🟢 <b>Yeni bilet</b> — ASB→İstanbul"]
    for d, fares in sorted(new_avail, key=lambda x: x[0]):
        out.append(f"\n<b>{_gun_baslik(d)}</b>")
        rows = []
        for key, info in fares.items():
            _fl, cab = key.split("|")
            rows.append((info.get("dep") or "--:--", cab, info["price"], info.get("seats")))
        for dep, cab, price, seats in sorted(rows):
            seat = f" · <b>son {seats} koltuk</b>" if seats else ""
            out.append(f"  {dep} · {cab} · <b>{price:.0f}$</b>{seat}")
    return "\n".join(out)


def _price_drop_message(price_drop):
    out = ["🔻 <b>Fiyat düştü</b> — ASB→İstanbul"]
    last_day = None
    for d, fl, cab, old, new, info in sorted(price_drop, key=lambda x: x[0]):
        if d != last_day:
            out.append(f"\n<b>{_gun_baslik(d)}</b>")
            last_day = d
        dep = info.get("dep") or "--:--"
        out.append(f"  {dep} · {cab}: {old:.0f}$ → <b>{new:.0f}$</b>")
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


def write_latest(results, st):
    """
    latest.json = BOTUN GOSTERDIGI veri. Ilke: gosterimde GERCEK oncelikli.
      - 'ok'    -> o tarih guncel tarifelerle DEGISTIRILIR (fiyat/koltuk dahil)
      - 'empty' -> HEMEN kaldirilir. state.json'daki debounce'tan BAGIMSIZ:
                   satista yoksa botun listesinde de gorunmemeli (tukenen bilet kalmasin).
      - 'fail'  -> dogrulanamadi (ag/HTTP), eski kayit KORUNUR.
    Bu fonksiyon yalnizca SAGLIKLI taramada cagrilir (tam cokme korumasi once doner).

    Icerik degismediyse 'updated' damgasi degistirilmez -> dosya bayt-ayni kalir
    -> bos commit olmaz, ama gercek her degisiklik aninda yayinlanir.
    """
    prev, prev_updated = {}, None
    if os.path.exists(LATEST_FILE):
        try:
            with open(LATEST_FILE, encoding="utf-8") as fp:
                old = json.load(fp) or {}
            prev = old.get("dates", {}) or {}
            prev_updated = old.get("updated")
        except Exception:
            prev, prev_updated = {}, None

    board = dict(prev)
    for d, (s, f) in results.items():
        diso = d.isoformat()
        if s == "ok" and f:
            arr = []
            for key, info in f.items():
                fl, cab = key.split("|")
                arr.append({"t": info.get("dep"), "c": cabin_letter(cab),
                            "p": info["price"], "s": info.get("seats"), "fl": fl})
            arr.sort(key=lambda x: (x["t"] or "", x["c"]))
            board[diso] = arr
        elif s == "empty":
            board.pop(diso, None)   # satista yok -> gosterimden HEMEN cik
        # 'fail' -> dogrulanamadi, eski kaydi koru

    valid = {d.isoformat() for d in monitored_dates()}
    board = {k: v for k, v in board.items() if k in valid}   # gecmis/aralik disi temizle

    # --- TEK KAYNAK: tablo, state.json'in birebir yansimasi olmali ---
    # Bildirimler state'e bakar; tablo da ayni gercegi gostersin diye state'te
    # olmayan hicbir tarih/tarife tabloda kalamaz (hayalet kayit birikmesin).
    allowed = set()
    for k in st["fares"]:
        diso, fl, cab = k.split("|")
        allowed.add((diso, fl, cabin_letter(cab)))
    for diso in list(board):
        if not st["dates_has_any"].get(diso):
            board.pop(diso)                      # state "yok" diyorsa tabloda da yok
            continue
        kept = [e for e in board[diso] if (diso, e.get("fl"), e.get("c")) in allowed]
        if kept:
            board[diso] = kept
        else:
            board.pop(diso)

    changed = board != prev
    data = {"updated": (datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                        if (changed or not prev_updated) else prev_updated),
            "range": f"{max(date.today(), DATE_START).isoformat()}..{DATE_END.isoformat()}",
            "dates": board}
    with open(LATEST_FILE, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1, sort_keys=True)
    if changed:
        log("latest.json guncellendi (gosterim verisi degisti).")


# --- CLI ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Turkmenistan Airlines ASB->IST fare watcher")
    ap.add_argument("--dry-run", action="store_true", help="Telegram gonderme + state yazma yok")
    ap.add_argument("--test-alert", action="store_true", help="Telegram'a ornek mesaj at ve cik")
    ap.add_argument("--limit", type=int, default=None, help="Sadece ilk N tarih")
    ap.add_argument("--date", type=str, default=None, help="Tek tarih debug (GG.AA.YYYY)")
    ap.add_argument("--report", action="store_true",
                    help="Bu calismada mevcut tum biletlerin tam listesini gonder")
    ap.add_argument("--check-customers", action="store_true",
                    help="Musteri tablosu kurulumunu dogrula (detay Telegram'a)")
    args = ap.parse_args()

    if args.check_customers:
        log("Musteri tablosu kontrol ediliyor...")
        check_customers(dry=args.dry_run)
        return

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
