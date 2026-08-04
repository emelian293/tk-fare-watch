// TK Fare-Watch — Telegram bot (Cloudflare Worker)
// Bota bir AY ADI ("eylul") ya da "tumu" yazilinca, GitHub'daki latest.json'u
// okuyup o ay(lar)in biletlerini hizali tablo halinde cevaplar.
//
// Gerekli degiskenler (wrangler secret / dashboard Variables):
//   BOT_TOKEN       (zorunlu) Telegram bot token
//   OWNER_CHAT_ID   (opsiyonel) sadece bu chat'e cevap ver
//   WEBHOOK_SECRET  (opsiyonel) Telegram setWebhook secret_token ile ayni olmali
//   DATA_URL        (opsiyonel) latest.json adresi (varsayilan asagida)

const DEFAULT_DATA_URL =
  "https://raw.githubusercontent.com/emelian293/tk-fare-watch/main/latest.json";

const AY_ADI = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
  "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const AY_KISA = ["", "Oca", "Sub", "Mar", "Nis", "May", "Haz",
  "Tem", "Agu", "Eyl", "Eki", "Kas", "Ara"];
const GUN_TBL = ["Pzt", "Sal", "Car", "Per", "Cum", "Cmt", "Paz"];
const AY_NO = {
  ocak: 1, subat: 2, "şubat": 2, mart: 3, nisan: 4, mayis: 5, "mayıs": 5,
  haziran: 6, temmuz: 7, agustos: 8, "ağustos": 8, eylul: 9, "eylül": 9,
  ekim: 10, kasim: 11, "kasım": 11, aralik: 12, "aralık": 12,
};
const ALL_WORDS = ["tümü", "tumu", "tüm", "tum", "hepsi", "all", "hep"];

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("TK Fare-Watch bot up");
    if (env.WEBHOOK_SECRET &&
        request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try { update = await request.json(); } catch { return new Response("ok"); }
    const msg = update.message || update.edited_message;
    if (!msg || !msg.text) return new Response("ok");

    const chatId = msg.chat.id;
    if (env.OWNER_CHAT_ID && String(chatId) !== String(env.OWNER_CHAT_ID)) {
      await send(env, chatId, "Bu bot özeldir.");
      return new Response("ok");
    }
    try {
      await handle(env, chatId, msg.text.trim().toLowerCase());
    } catch (e) {
      await send(env, chatId, "⚠️ Hata: " + e.message);
    }
    return new Response("ok");
  },
};

async function handle(env, chatId, text) {
  if (["/start", "yardim", "yardım", "help", "?", "/help"].includes(text)) {
    return send(env, chatId, helpText(), "HTML");
  }

  let want; // "all" | 1..12 | undefined
  const tok = text.split(/\s+/)[0];
  if (ALL_WORDS.includes(text) || ALL_WORDS.includes(tok)) want = "all";
  else if (tok in AY_NO) want = AY_NO[tok];

  if (want === undefined) {
    return send(env, chatId, "Anlamadım 🤔\n\n" + helpText(), "HTML");
  }

  const data = await fetchData(env);
  const dates = data.dates || {};
  let keys = Object.keys(dates).sort();
  if (want !== "all") keys = keys.filter((k) => monthOf(k) === want);

  if (keys.length === 0) {
    const label = want === "all" ? "seçilen aralık" : AY_ADI[want];
    return send(env, chatId,
      `📭 <b>${label}</b> için şu an satışta bilet yok.\n(Ağustos başı ve 25 Ekim sonrası henüz kapalı.)`,
      "HTML");
  }

  const label = want === "all" ? "Tüm aylar" : AY_ADI[want];
  await send(env, chatId,
    `🎫 <b>${label} — ASB→İstanbul</b> · ${keys.length} tarihte bilet\n` +
    `🟢 E=Ekonomi · 🔴 B=Business · Klt=son N koltuk · tek yön/USD\n` +
    `<i>güncelleme: ${esc(data.updated || "")}</i>`,
    "HTML");

  const byMonth = {};
  for (const k of keys) (byMonth[monthOf(k)] ||= []).push(k);
  for (const m of Object.keys(byMonth).map(Number).sort((a, b) => a - b)) {
    const table = monthTable(byMonth[m], dates);
    await send(env, chatId, `<b>${AY_ADI[m]}</b>\n<pre>${esc(table)}</pre>`, "HTML");
  }
}

function monthOf(k) { return parseInt(k.slice(5, 7), 10); }

function weekdayIdx(k) {           // Pazartesi=0 .. Pazar=6
  const js = new Date(k + "T00:00:00Z").getUTCDay(); // 0=Paz .. 6=Cmt
  return (js + 6) % 7;
}

function monthTable(keys, dates) {
  const lines = [row("Tarih", "Gun", "Saat", "Sf", "Fiyat", "Klt")];
  for (const k of keys.sort()) {
    const ds = `${k.slice(8, 10)} ${AY_KISA[monthOf(k)]}`;
    const gn = GUN_TBL[weekdayIdx(k)];
    const fares = (dates[k] || []).slice()
      .sort((a, b) => (a.t || "").localeCompare(b.t || "") || a.c.localeCompare(b.c));
    for (const f of fares) {
      lines.push(row(ds, gn, f.t || "--:--", f.c, `${Math.round(f.p)}$`, f.s ? String(f.s) : "-"));
    }
  }
  return lines.join("\n");
}

function pad(s, w, right) {
  s = String(s);
  if (s.length >= w) return s;
  const sp = " ".repeat(w - s.length);
  return right ? sp + s : s + sp;
}
function row(c1, c2, c3, c4, c5, c6) {
  return pad(c1, 6) + " " + pad(c2, 3) + " " + pad(c3, 5) + " " +
         pad(c4, 2) + " " + pad(c5, 6, true) + " " + pad(c6, 3, true);
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function fetchData(env) {
  const base = env.DATA_URL || DEFAULT_DATA_URL;
  const url = base + (base.includes("?") ? "&" : "?") + "_=" + Math.floor(Date.now() / 60000);
  const r = await fetch(url, { cf: { cacheTtl: 30 } });
  if (!r.ok) throw new Error("veri alınamadı (" + r.status + ")");
  return await r.json();
}

async function send(env, chatId, text, parseMode) {
  const body = { chat_id: chatId, text, disable_web_page_preview: true };
  if (parseMode) body.parse_mode = parseMode;
  await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function helpText() {
  return "✈️ <b>ASB→İstanbul bilet botu</b>\n\n" +
    "Bir <b>ay adı</b> yaz, o ayın tüm biletlerini göndereyim:\n" +
    "<code>ağustos</code> · <code>eylül</code> · <code>ekim</code> …\n\n" +
    "Hepsi için: <b>tümü</b>\n\n" +
    "Ayrıca 🟢 yeni bilet / 🔻 fiyat düşüşü olduğunda otomatik haber gelir.";
}
