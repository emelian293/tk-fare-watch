// ==UserScript==
// @name         TK Fare-Watch — Yolcu Bilgisi Otomatik Doldur
// @namespace    tk-fare-watch
// @version      1.1
// @description  Telegram'daki bilet linkindeki yolcu bilgilerini ödeme formuna doldurur. KART bilgilerine DOKUNMAZ.
// @match        https://turkmenistanairlinestr.com/*
// @run-at       document-idle
// @grant        none
// @updateURL    https://raw.githubusercontent.com/emelian293/tk-fare-watch/main/mobile/autofill.user.js
// @downloadURL  https://raw.githubusercontent.com/emelian293/tk-fare-watch/main/mobile/autofill.user.js
// ==/UserScript==

/*
 NASIL CALISIR
 1) Telegram'daki "Bileti aç" linki sunu icerir:  ...#p=<base64 yolcu bilgisi>
    '#' sonrasi SUNUCUYA GONDERILMEZ — yalnizca telefonun tarayicisinda kalir.
 2) Bu betik arama sayfasinda o veriyi alip sessionStorage'a koyar ve adres cubugunu temizler.
 3) Sec -> Devam Et ile odeme sayfasina gecince, yolcu alanlarini doldurur.
 4) KART alanlari (kart no / CVV / son kullanma) KESINLIKLE doldurulmaz — onlari
    telefonun kendi otomatik doldurmasiyla sen girersin ve SATIN AL'a sen basarsin.
*/

(function () {
  "use strict";
  const KEY = "tkfw_pax";

  // --- 1) Linkteki veriyi yakala -------------------------------------------
  const m = location.hash.match(/[#&]p=([A-Za-z0-9\-_]+)/);
  if (m) {
    try {
      let b = m[1].replace(/-/g, "+").replace(/_/g, "/");
      while (b.length % 4) b += "=";
      const pax = JSON.parse(decodeURIComponent(escape(atob(b))));
      sessionStorage.setItem(KEY, JSON.stringify(pax));
      history.replaceState(null, "", location.pathname + location.search); // adresi temizle
      toast("👤 " + [pax.ad, pax.soyad].filter(Boolean).join(" ") +
            " hazır — Seç → Devam Et yapınca form dolacak");
    } catch (e) { /* bozuk veri: sessizce gec */ }
  }

  // --- 2) Odeme sayfasindaysa doldur ---------------------------------------
  const pax = readPax();
  if (!pax) return;
  if (!/\/Flight\/Payment\//i.test(location.pathname)) return;

  let tries = 0;
  const timer = setInterval(() => {
    if (document.querySelector('[name="Name[0]"]')) { clearInterval(timer); fill(pax); }
    else if (++tries > 40) clearInterval(timer);   // ~20 sn sonra vazgec
  }, 500);

  // --- Doldurma -------------------------------------------------------------
  function fill(p) {
    let n = 0;
    n += setText('[name="Name[0]"]', p.ad);
    n += setText('[name="SurName[0]"]', p.soyad);
    n += setText('[name="PassportNo[0]"]', p.pasaport);
    n += setText('[name="BirthDate[0]"]', p.dogum);
    n += setText('[name="TravellerEmail"]', p.eposta) || setText('[name="Email"]', p.eposta);
    n += setSelect('[name="Gender[0]"]', genderWords(p.cinsiyet));
    // Ulke: secenek degeri ISO kodu (TM=Türkmenistan) — hem ada hem koda bak
    n += setSelect('[name="PassportCountry[0]"]', [p.uyruk, ulkeKodu(p.uyruk)]);
    n += fillPhone(p.telefon);
    // NOT: Bu formda "pasaport bitis tarihi" alani YOKTUR.
    //      ExpiredMonth/ExpiredYear KARTIN son kullanma tarihidir -> ASLA doldurulmaz.
    toast(n ? `✅ ${n} alan dolduruldu — kartı sen gir, SATIN AL'a sen bas`
            : "⚠️ Alanlar bulunamadı (site değişmiş olabilir)");
    addButton(p);   // gerekirse tekrar doldur
  }

  /* Telefon: site ulke kodunu AYRI bir secicide tutuyor (993 = TM - 993),
     numara alani ise kodsuz bekliyor ("555 123 45 66"). Ona gore bol. */
  function fillPhone(tel) {
    const raw = String(tel || "").replace(/[^\d+]/g, "");
    if (!raw) return 0;
    if (raw.startsWith("+")) {
      const bare = raw.slice(1);
      const sel = document.querySelector('[name="TravellerGsmCountry"]');
      if (sel) {
        for (let len = 4; len >= 1; len--) {           // en uzun eslesen ulke kodu
          const cc = bare.slice(0, len);
          const opt = Array.from(sel.options).find(o => o.value === cc);
          if (opt) {
            sel.value = opt.value;
            sel.dispatchEvent(new Event("change", { bubbles: true }));
            return 1 + setText('[name="GsmNumber"]', bare.slice(len));
          }
        }
      }
      return setText('[name="GsmNumber"]', bare);
    }
    return setText('[name="GsmNumber"]', raw.replace(/^0+/, ""));   // bastaki 0'i at
  }

  /* Yaygin ulke adlari -> ISO kodu (secici deger olarak kodu kullaniyor) */
  function ulkeKodu(u) {
    const n = String(u || "").toLocaleLowerCase("tr");
    if (/türkmen|turkmen/.test(n)) return "TM";
    if (/türkiye|turkiye|turkey/.test(n)) return "TR";
    if (/özbek|ozbek|uzbek/.test(n)) return "UZ";
    if (/rus|russia/.test(n)) return "RU";
    if (/azerb/.test(n)) return "AZ";
    return null;
  }

  // KART alanlari: hicbir kosulda doldurulmaz (CardNumber, Cvc, ExpiredMonth/Year, CardOwner)
  const KART = /card|cvc|cvv|expired/i;
  const kartMi = el => KART.test((el && (el.name || el.id)) || "");

  function setText(sel, val) {
    const el = document.querySelector(sel);
    if (!el || !val || kartMi(el)) return 0;
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    // React/jQuery bagli alanlar icin dogal deger atamasi + olaylar
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, val);
    for (const ev of ["input", "change", "blur", "keyup"])
      el.dispatchEvent(new Event(ev, { bubbles: true }));
    return 1;
  }

  function setSelect(sel, wanted) {
    const el = document.querySelector(sel);
    if (!el || !wanted || !wanted.length || kartMi(el)) return 0;
    const norm = s => String(s || "").toLocaleLowerCase("tr")
      .replace(/[̀-ͯ]/g, "")
      .replace(/ı/g, "i").replace(/ş/g, "s").replace(/ç/g, "c")
      .replace(/ğ/g, "g").replace(/ö/g, "o").replace(/ü/g, "u").trim();
    const targets = wanted.filter(Boolean).map(norm);
    for (const o of el.options) {
      const t = norm(o.text), v = norm(o.value);
      if (targets.some(w => t === w || v === w || (w.length > 2 && t.startsWith(w)))) {
        el.value = o.value;
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return 1;
      }
    }
    return 0;
  }

  function genderWords(g) {
    const n = String(g || "").toLocaleLowerCase("tr");
    if (/^(e|erkek|bay|male|m)$/.test(n)) return ["erkek", "male", "bay", "m", "1"];
    if (/^(k|kadın|kadin|bayan|female|f)$/.test(n)) return ["kadin", "kadın", "female", "bayan", "f", "2"];
    return [g];
  }

  function readPax() {
    try { return JSON.parse(sessionStorage.getItem(KEY) || "null"); }
    catch (e) { return null; }
  }

  // --- Yardimci arayuz ------------------------------------------------------
  function addButton(p) {
    if (document.getElementById("tkfw-btn")) return;
    const b = document.createElement("button");
    b.id = "tkfw-btn";
    b.textContent = "👤 Tekrar doldur";
    b.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:99999;padding:12px 16px;" +
      "border:0;border-radius:24px;background:#16a34a;color:#fff;font:600 15px system-ui;" +
      "box-shadow:0 4px 14px rgba(0,0,0,.3)";
    b.onclick = (e) => { e.preventDefault(); fill(p); };
    document.body.appendChild(b);
  }

  function toast(msg) {
    const t = document.createElement("div");
    t.textContent = msg;
    t.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);top:14px;z-index:99999;" +
      "max-width:92%;padding:12px 16px;border-radius:12px;background:#111;color:#fff;" +
      "font:500 14px system-ui;box-shadow:0 4px 14px rgba(0,0,0,.35);text-align:center";
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 5000);
  }
})();
