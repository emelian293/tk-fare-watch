# TK Fare-Watch — Aşgabat → İstanbul bilet/fiyat izleyici

Turkmenistan Airlines resmî Türkiye acentesi **turkmenistanairlinestr.com** üzerinden
**Aşgabat (ASB) → İstanbul (IST)** uçuşlarının **ekonomi ve business** tarifelerini izler ve
**Telegram**'dan bildirim gönderir.

**Bildirim durumları**
- 🟢 **Yeni bilet** — kapalı (0 sonuç) bir tarih satışa açıldığında ya da yeni uçuş/sınıf çıktığında
- 🔻 **Fiyat düştü** — izlenen bir (tarih · uçuş · sınıf) fiyatı öncekine göre ucuzladığında
- 📋 **Tam liste** — günde 1 kez (ve istediğin an) mevcut **tüm** müsait tarihler: her gün için uçuş · saat · sınıf · fiyat · **koltuk sayısı**

### Tam listeyi istediğin an almak
GitHub → **Actions** → `tk-fare-watch` → **Run workflow** → "report" kutusu işaretliyken çalıştır. Ya da terminalden:
```bash
gh workflow run watch.yml -R emelian293/tk-fare-watch -f report=true
```
Birkaç saniye içinde Telegram'a güncel tam liste düşer.

Çalışma: **GitHub Actions** ile her **10 dakikada** bir (bulutta, ücretsiz). Bilgisayarın açık olması gerekmez.

İzleme aralığı: bugünden **31.10.2026**'ya kadar (Ağustos–Ekim 2026). Geçmiş günler otomatik atlanır.

---

## Kurulum (tek seferlik, ~10 dk)

### 1) Telegram botu oluştur
1. Telegram'da **@BotFather**'a yaz → `/newbot` → isim ve kullanıcı adı ver.
2. Sana bir **token** verir: `123456789:AAE...` → bir kenara not al.
3. Kendi oluşturduğun bota Telegram'dan **bir mesaj at** (örn. "merhaba"). *(Bot sana ancak sen ona
   yazdıktan sonra mesaj gönderebilir.)*

### 2) chat_id'ni bul
Terminalde (bu klasörde):
```bash
TELEGRAM_TOKEN="SENIN_TOKENIN" python3 get_chat_id.py
```
Çıktıdaki sayıyı (**chat_id**) not al.

### 3) GitHub deposu (public) oluştur ve kodu yükle
- github.com'da yeni bir **public** repo aç (örn. `tk-fare-watch`).
- Bu klasörü ona yükle:
```bash
cd "tk-fare-watch"
git init
git add .
git commit -m "ilk surum"
git branch -M main
git remote add origin https://github.com/KULLANICI_ADIN/tk-fare-watch.git
git push -u origin main
```

### 4) Secrets ekle (token'ı koda yazma!)
Repo sayfasında: **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_TOKEN`   = BotFather token'ın
- `TELEGRAM_CHAT_ID` = 2. adımda bulduğun chat_id

### 5) İlk çalıştırma
Repo → **Actions** sekmesi → `tk-fare-watch` → **Run workflow** (workflow_dispatch).
- İlk çalıştırma **baseline** kurar ve Telegram'a bir "✅ başladım" özeti gönderir.
- Bundan sonra her 10 dakikada otomatik çalışır; sadece **yeni bilet / fiyat düşüşü** olunca haber verir.

> İpucu: Actions ilk kez elle çalıştırıldıktan sonra zamanlanmış (cron) tetikleyici devreye girer.

---

## Yerel test (opsiyonel)
```bash
pip install -r requirements.txt

# Telegram göndermeden, sadece parse + değişiklik mantığını gör (state yazmaz):
python3 fare_watch.py --dry-run --limit 8

# Tek tarih incele:
python3 fare_watch.py --date 06.09.2026 --dry-run

# Telegram bağlantısını test et (token+chat_id ortam değişkeni gerekir):
TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python3 fare_watch.py --test-alert
```

## Ayarlar
`fare_watch.py` içindeki **AYARLAR** bölümünden değiştirilebilir:
- `ORIGIN` / `DEST` — havaalanı kodları (ASB / IST)
- `DATE_START` / `DATE_END` — izleme aralığı
- `REQUEST_DELAY` — tarihler arası bekleme (siteye nazik olmak için)

Sıklığı değiştirmek için `.github/workflows/watch.yml` içindeki `cron` satırını düzenle.

## Dosyalar
| Dosya | Görev |
|-------|-------|
| `fare_watch.py` | Ana script: çek → ayrıştır → karşılaştır → bildir → durumu güncelle |
| `state.json` | Son bilinen fiyatlar/durum (Actions her değişimde geri commit'ler; başta `{}`) |
| `get_chat_id.py` | Telegram chat_id bulma yardımcısı |
| `.github/workflows/watch.yml` | 10 dakikalık cron + manuel çalıştırma |

## Notlar / kısıtlar
- **GitHub cron** "en iyi çaba"dır; yoğunlukta gerçek aralık 10 dk'dan biraz sarkabilir.
- Fiyatlar **USD**; anlık koltuk durumuna göre değişir.
- **1–28 Ağustos** ve **25 Ekim sonrası** şu an satışta değil; açıldıkları an "yeni bilet" bildirimi gelir.
- Site yapısı veya erişim politikası değişirse `fare_watch.py` içindeki `parse_fares` güncellenmelidir.
- Bu araç kişisel takip amaçlıdır; siteye nazik davranır (istekler arası kısa bekleme).
