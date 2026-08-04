// TK Fare-Watch — Telegram bot (Cloudflare Worker)
// Bir AY ADI ("eylul") ya da "tumu" yazilinca GitHub'daki latest.json'u okuyup
// o ay(lar)in biletlerini hizali tablo halinde cevaplar.
//
// ERISIM KONTROLU (KV: ACCESS):
//   - OWNER_CHAT_ID her zaman yetkili + yonetici.
//   - Yetkisiz kisi yazinca: kendi ID'sini gorur, sahibe bildirim gider.
//   - Sahip komutlari: /izinver <id> [ad] · /izinal <id> · /kullanicilar
//
// Degiskenler: BOT_TOKEN (zorunlu), OWNER_CHAT_ID, WEBHOOK_SECRET, DATA_URL (ops.)
// KV binding: ACCESS

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
    if (msg && msg.text) {
      try { await route(env, msg); }
      catch (e) { await send(env, msg.chat.id, "⚠️ Hata: " + e.message); }
    }
    return new Response("ok");
  },
};

async function route(env, msg) {
  const chatId = String(msg.chat.id);
  const text = msg.text.trim();
  const lower = text.toLowerCase();
  const isOwner = env.OWNER_CHAT_ID && chatId === String(env.OWNER_CHAT_ID);

  // --- Sahip: erisim yonetimi komutlari ---
  if (isOwner) {
    let m;
    if ((m = lower.match(/^\/izinver\s+(-?\d+)(?:\s+([\s\S]+))?$/)))
      return cmdAllow(env, chatId, m[1], (m[2] || "").trim());
    if ((m = lower.match(/^\/izinal\s+(-?\d+)/)))
      return cmdRevoke(env, chatId, m[1]);
    if (lower === "/kullanicilar" || lower === "/liste")
      return cmdList(env, chatId);
  }

  // --- Erisim kontrolu ---
  const allowed = isOwner || await isAllowed(env, chatId);
  if (!allowed) return requestAccess(env, msg);

  // --- Yardim ---
  if (["/start", "yardim", "yardım", "help", "?", "/help"].includes(lower))
    return send(env, chatId, helpText(isOwner), "HTML");

  // --- Ay sorgusu ---
  return handleQuery(env, chatId, lower);
}

// ---------- Erisim (KV) ----------

async function isAllowed(env, chatId) {
  if (!env.ACCESS) return false;
  return (await env.ACCESS.get("u:" + chatId)) !== null;
}

async function cmdAllow(env, chatId, id, name) {
  if (!env.ACCESS) return send(env, chatId, "KV bağlı değil.");
  await env.ACCESS.put("u:" + id, JSON.stringify({ name, at: new Date().toISOString() }));
  await send(env, chatId, `✅ <code>${id}</code> ${name ? "(" + esc(name) + ") " : ""}erişime eklendi.`, "HTML");
  // yeni kisiye haber (daha once bota yazdiysa ulasir)
  await send(env, id, "✅ Erişimin açıldı! Bir ay adı yaz (ör. eylül) ya da tümü.");
}

async function cmdRevoke(env, chatId, id) {
  if (!env.ACCESS) return send(env, chatId, "KV bağlı değil.");
  await env.ACCESS.delete("u:" + id);
  return send(env, chatId, `🚫 <code>${id}</code> erişimden çıkarıldı.`, "HTML");
}

async function cmdList(env, chatId) {
  if (!env.ACCESS) return send(env, chatId, "KV bağlı değil.");
  const list = await env.ACCESS.list({ prefix: "u:" });
  if (!list.keys.length)
    return send(env, chatId, "Henüz izinli kullanıcı yok.\nEklemek: <code>/izinver &lt;id&gt; [ad]</code>", "HTML");
  const lines = [];
  for (const k of list.keys) {
    const id = k.name.slice(2);
    let name = "";
    try { name = (JSON.parse(await env.ACCESS.get(k.name) || "{}").name) || ""; } catch {}
    lines.push(`• <code>${id}</code>${name ? " — " + esc(name) : ""}`);
  }
  return send(env, chatId,
    `👥 <b>İzinli kullanıcılar (${lines.length})</b>\n` + lines.join("\n") +
    `\n\nÇıkarmak: <code>/izinal &lt;id&gt;</code>`, "HTML");
}

async function requestAccess(env, msg) {
  const chatId = String(msg.chat.id);
  const who = ([msg.from?.first_name, msg.from?.last_name].filter(Boolean).join(" ") +
               (msg.from?.username ? " @" + msg.from.username : "")).trim();
  await send(env, chatId,
    "🔒 Bu bot özel.\nErişim için sahibine şu numaranı ilet:\n" +
    `<b>${chatId}</b>\nOnaylanınca tekrar yaz.`, "HTML");
  if (env.OWNER_CHAT_ID) {
    await send(env, env.OWNER_CHAT_ID,
      `🔔 <b>Erişim isteği</b>\n${esc(who) || "?"}\nID: <code>${chatId}</code>\n` +
      `İzin ver: <code>/izinver ${chatId}${who ? " " + esc(who) : ""}</code>`, "HTML");
  }
}

// ---------- Ay sorgusu ----------

async function handleQuery(env, chatId, text) {
  let want;
  const tok = text.split(/\s+/)[0];
  if (ALL_WORDS.includes(text) || ALL_WORDS.includes(tok)) want = "all";
  else if (tok in AY_NO) want = AY_NO[tok];
  if (want === undefined)
    return send(env, chatId, "Anlamadım 🤔\n\n" + helpText(false), "HTML");

  const data = await fetchData(env);
  const dates = data.dates || {};
  let keys = Object.keys(dates).sort();
  if (want !== "all") keys = keys.filter((k) => monthOf(k) === want);

  if (keys.length === 0) {
    const label = want === "all" ? "seçilen aralık" : AY_ADI[want];
    return send(env, chatId,
      `📭 <b>${label}</b> için şu an satışta bilet yok.\n(Ağustos başı ve 25 Ekim sonrası henüz kapalı.)`, "HTML");
  }

  const label = want === "all" ? "Tüm aylar" : AY_ADI[want];
  await send(env, chatId,
    `🎫 <b>${label} — ASB→İstanbul</b> · ${keys.length} tarihte bilet\n` +
    `🟢 E=Ekonomi · 🔴 B=Business · Klt=son N koltuk · tek yön/USD\n` +
    `<i>güncelleme: ${esc(data.updated || "")}</i>`, "HTML");

  const byMonth = {};
  for (const k of keys) (byMonth[monthOf(k)] ||= []).push(k);
  for (const m of Object.keys(byMonth).map(Number).sort((a, b) => a - b))
    await send(env, chatId, `<b>${AY_ADI[m]}</b>\n<pre>${esc(monthTable(byMonth[m], dates))}</pre>`, "HTML");
}

// ---------- Tablo ----------

function monthOf(k) { return parseInt(k.slice(5, 7), 10); }
function weekdayIdx(k) { return (new Date(k + "T00:00:00Z").getUTCDay() + 6) % 7; }

function monthTable(keys, dates) {
  const lines = [row("Tarih", "Gun", "Saat", "Sf", "Fiyat", "Klt")];
  for (const k of keys.sort()) {
    const ds = `${k.slice(8, 10)} ${AY_KISA[monthOf(k)]}`;
    const gn = GUN_TBL[weekdayIdx(k)];
    const fares = (dates[k] || []).slice()
      .sort((a, b) => (a.t || "").localeCompare(b.t || "") || a.c.localeCompare(b.c));
    for (const f of fares)
      lines.push(row(ds, gn, f.t || "--:--", f.c, `${Math.round(f.p)}$`, f.s ? String(f.s) : "-"));
  }
  return lines.join("\n");
}

function pad(s, w, right) {
  s = String(s);
  if (s.length >= w) return s;
  const sp = " ".repeat(w - s.length);
  return right ? sp + s : s + sp;
}
function row(a, b, c, d, e, f) {
  return pad(a, 6) + " " + pad(b, 3) + " " + pad(c, 5) + " " +
         pad(d, 2) + " " + pad(e, 6, true) + " " + pad(f, 3, true);
}

// ---------- Yardimcilar ----------

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
  try {
    await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (_) { /* alici bota hic yazmadiysa ulasilamaz; yut */ }
}

function helpText(isOwner) {
  let t = "✈️ <b>ASB→İstanbul bilet botu</b>\n\n" +
    "Bir <b>ay adı</b> yaz, o ayın biletlerini göndereyim:\n" +
    "<code>ağustos</code> · <code>eylül</code> · <code>ekim</code> …\n\n" +
    "Hepsi için: <b>tümü</b>\n\n" +
    "🟢 yeni bilet / 🔻 fiyat düşüşü olunca ayrıca otomatik haber gelir.";
  if (isOwner)
    t += "\n\n<b>Yönetici komutları</b>\n" +
      "<code>/izinver &lt;id&gt; [ad]</code> — erişim ver\n" +
      "<code>/izinal &lt;id&gt;</code> — erişimi al\n" +
      "<code>/kullanicilar</code> — izinli listesi";
  return t;
}
