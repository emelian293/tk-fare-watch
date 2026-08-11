# 📱 Telefonda otomatik doldurma

Telegram'daki **"Bileti aç"** linkine dokunduğunda, ödeme sayfasındaki **yolcu bilgileri**
(ad, soyad, cinsiyet, doğum, pasaport, uyruk, telefon, e-posta) otomatik dolar.

**Kart bilgileri doldurulmaz** — onları telefonunun kendi otomatik doldurmasıyla sen girer,
**SATIN AL**'a sen basarsın.

---

## Neden bir tarayıcı eklentisi gerekiyor?

Bir link, başka bir sitenin formunu dolduramaz — tarayıcılar bunu güvenlik gereği engeller
(yoksa herhangi bir link bankan sitesine veri yazabilirdi). Formu doldurabilmek için
**sayfanın içinde çalışan** küçük bir betik gerekir. Bunu yapan da "kullanıcı betiği" eklentisidir.

Veri nasıl taşınıyor? Linkin sonundaki `#p=...` kısmında. **`#` sonrası sunucuya asla gönderilmez** —
sadece telefonunun tarayıcısında kalır. Yani yolcu bilgileri havayolu sitesine önceden sızmaz.

---

## Android kurulumu (~5 dk) — önerilen

Kiwi Browser'ın geliştirmesi durdu; **Firefox** hem güncel hem eklenti desteği resmî.

1. Play Store'dan **Firefox** kur (varsa atla).
2. Firefox'u aç → sağ üst **⋮** → **Eklentiler** (Add-ons).
3. Listeden **Tampermonkey**'i bul → **+ Ekle** → izin ver.
4. Şu adrese git:
   `https://raw.githubusercontent.com/emelian293/tk-fare-watch/main/mobile/autofill.user.js`
5. Tampermonkey kurulum ekranı açılır → **Install / Kur**.
6. Telegram'ı aç, bildirimdeki **Bileti aç** linkine bas → *"Firefox ile aç"* seç.
   (Kolaylık: Telegram → Ayarlar → Bağlantıları **harici tarayıcıda aç** yaparsan hep Firefox'ta açılır.)

## iPhone kurulumu (~5 dk)

1. App Store'dan **Userscripts** uygulamasını kur (ücretsiz).
2. Ayarlar → **Safari** → **Uzantılar** → **Userscripts**'i aç, izinleri **İzin Ver**.
3. Userscripts uygulamasını aç → bir klasör seç.
4. Safari'de yukarıdaki `autofill.user.js` adresini aç → içeriği kopyala →
   Userscripts'te **+** ile yeni betik oluşturup yapıştır → kaydet.
5. Telegram'dan linke dokun (Safari'de açılsın).

---

## Kullanım

1. 🔔 Bildirim gelir → **Bileti aç**'a dokun
2. Üstte kısa bir bilgi çıkar: *"👤 Plany Planyyev hazır"*
3. **Seç** → **Devam Et**
4. Yolcu alanları **kendiliğinden dolar** (*"✅ 8 alan dolduruldu"*)
5. Kartı gir (telefonun otomatik doldurması) → **SATIN AL**

Dolmazsa sağ altta **👤 Tekrar doldur** düğmesi var.

---

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| Hiçbir şey olmuyor | Link Firefox/Safari'de mi açıldı? Telegram'ın kendi tarayıcısında eklenti çalışmaz. |
| "Alanlar bulunamadı" | Ödeme sayfasına geçmeden çalıştırılmış olabilir; **👤 Tekrar doldur**'a bas. |
| Bazı alanlar boş kaldı | O alan tabloda boş olabilir (`müşteri` raporu eksikleri gösterir). |
| Cinsiyet/uyruk seçilmedi | Tablodaki yazım farklı olabilir (örn. `Erkek`, `Kadın`, `Türkmenistan`). |

> Betik yalnızca `turkmenistanairlinestr.com` üzerinde çalışır ve **hiçbir veriyi dışarı göndermez**;
> tüm işlem telefonunun içinde olur.
