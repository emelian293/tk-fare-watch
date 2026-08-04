# TK Fare-Watch — Telegram bot (Cloudflare Worker)

Bota **ay adı** (`eylül`) ya da `tümü` yazınca, GitHub'daki `latest.json`'u okuyup o ayın
biletlerini **anlık** (saniyeler içinde) tablo halinde cevaplar. Ücretsiz, 7/24, sunucu gerektirmez.

Otomatik uyarılar (🟢 yeni bilet / 🔻 fiyat düşüşü) yine GitHub Actions'tan gelir — bu bot onlara **ek**tir.

---

## Kurulum — Yöntem A: Cloudflare paneli (CLI yok, önerilen)

1. **Hesap:** [dash.cloudflare.com](https://dash.cloudflare.com) → ücretsiz kayıt / giriş.
2. **Worker oluştur:** Sol menü **Workers & Pages** → **Create** → **Create Worker** →
   bir isim ver (ör. `tk-fare-bot`) → **Deploy** (örnek kod yüklenir).
3. **Kodu yapıştır:** **Edit code** → sağdaki editörün içindekini sil →
   `worker/src/index.js` dosyasının **tamamını** yapıştır → **Deploy**.
4. **Değişkenler:** Worker → **Settings** → **Variables and Secrets** → şunları **Add** et
   (BOT_TOKEN'ı **Encrypt/Secret** yap):
   - `BOT_TOKEN` = Telegram bot token'ın
   - `OWNER_CHAT_ID` = kendi chat_id'in (sadece sana cevap versin)
   - `WEBHOOK_SECRET` = rastgele bir metin (aşağıdaki curl'de aynısını kullan)
   → **Deploy** ile kaydet.
5. **Worker adresini kopyala** (ör. `https://tk-fare-bot.KULLANICI.workers.dev`).
6. **Telegram webhook'unu bağla** (terminalde, kendi değerlerinle):
   ```bash
   curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<WEBHOOK_SECRET>"
   ```
   `{"ok":true,...}` dönerse tamam.
7. **Test:** Telegram'da bota `eylül` yaz → Eylül tablosu gelsin. `tümü` → hepsi.

---

## Kurulum — Yöntem B: wrangler (CLI)

```bash
cd worker
npx wrangler login                 # tarayıcıdan Cloudflare girişi
npx wrangler deploy                # dağıt (URL'i verir)
npx wrangler secret put BOT_TOKEN        # sorunca token'ı yapıştır
npx wrangler secret put OWNER_CHAT_ID    # chat_id
npx wrangler secret put WEBHOOK_SECRET   # rastgele metin
# sonra 6. adımdaki setWebhook curl'ünü çalıştır
```

---

## Değişkenler
| Ad | Zorunlu | Açıklama |
|----|:---:|----|
| `BOT_TOKEN` | ✓ | Telegram bot token (gizli) |
| `OWNER_CHAT_ID` | – | Sadece bu chat'e cevap verir (boşsa herkese) |
| `WEBHOOK_SECRET` | – | setWebhook `secret_token` ile aynı olmalı (güvenlik) |
| `DATA_URL` | – | latest.json adresi (varsayılan koddadır) |

## Webhook komutları
- Durum: `curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"`
- Kaldır: `curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`

> Not: Webhook kurulunca botun `getUpdates` ile polling'i kapanır; GitHub Actions yalnızca
> `sendMessage` kullandığı için otomatik uyarılar bundan etkilenmez.
