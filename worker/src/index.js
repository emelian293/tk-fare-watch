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
// Sinif sozcukleri (trNorm'dan gecmis, ASCII halleriyle eslesir)
const CABIN_WORDS = {
  ekonomi: "E", economy: "E", eko: "E", eco: "E",
  business: "B", biznes: "B", biz: "B", bussiness: "B",
  premium: "P", prm: "P",
};
const CABIN_ADI = { E: "Ekonomi", B: "Business", P: "Premium" };

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      // Python tarama scripti bildirim ayarlarini buradan okur
      if (new URL(request.url).pathname === "/config")
        return new Response(JSON.stringify(await getCfg(env)),
          { headers: { "content-type": "application/json" } });
      return new Response("TK Fare-Watch bot up");
    }
    // Python tarama scripti her SAGLIKLI taramadan sonra "son kontrol" damgasi atar
    if (new URL(request.url).pathname === "/heartbeat") {
      let body;
      try { body = await request.json(); } catch { return new Response("bad", { status: 400 }); }
      if (!env.WEBHOOK_SECRET || body.secret !== env.WEBHOOK_SECRET)
        return new Response("forbidden", { status: 403 });
      if (env.ACCESS)
        await env.ACCESS.put("sys:lastscan",
          JSON.stringify({ t: Date.now(), found: body.found ?? null }));
      return new Response("ok");
    }
    // Python tarama scripti bildirimleri buradan YETKILI HERKESE yollar
    if (new URL(request.url).pathname === "/broadcast") {
      let body;
      try { body = await request.json(); } catch { return new Response("bad", { status: 400 }); }
      if (!env.WEBHOOK_SECRET || body.secret !== env.WEBHOOK_SECRET)
        return new Response("forbidden", { status: 403 });
      await broadcastToAll(env, body.text, body.parse_mode, body.reply_markup);
      return new Response("ok");
    }
    if (env.WEBHOOK_SECRET &&
        request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try { update = await request.json(); } catch { return new Response("ok"); }
    if (update.callback_query) {                       // "Aldım / Olmadı" butonlari
      try { await onCallback(env, update.callback_query); } catch (_) {}
      return new Response("ok");
    }
    const msg = update.message || update.edited_message;
    if (msg && msg.text) {
      try { await route(env, msg); }
      catch (e) { await send(env, msg.chat.id, "⚠️ Hata: " + e.message); }
    }
    return new Response("ok");
  },

  // Cloudflare Cron (guvenilir): GitHub tarama workflow'unu tetikler
  async scheduled(event, env, ctx) {
    ctx.waitUntil(triggerScan(env));
  },
};

async function triggerCheck(env) {
  /* Musteri raporunu GitHub'da uretip Telegram'a yollatir.
     Rapor kisisel veri icerdigi icin Worker/KV'de SAKLANMAZ; dogrudan sahibe gider. */
  if (!env.GH_TOKEN) return false;
  const owner = env.GH_OWNER || "emelian293";
  const repo = env.GH_REPO || "tk-fare-watch";
  const wf = env.GH_WORKFLOW || "watch.yml";
  try {
    const r = await fetch(
      `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${wf}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GH_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "tk-fare-bot",
        },
        body: JSON.stringify({ ref: "main", inputs: { check: "true", report: "false" } }),
      });
    return r.ok;
  } catch (_) { return false; }
}


async function triggerScan(env) {
  if (!env.GH_TOKEN) return; // token yoksa sessizce gec
  const owner = env.GH_OWNER || "emelian293";
  const repo = env.GH_REPO || "tk-fare-watch";
  const wf = env.GH_WORKFLOW || "watch.yml";
  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${wf}/dispatches`;
  const headers = {
    "Authorization": `Bearer ${env.GH_TOKEN}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "tk-fare-bot",
  };
  // report=false -> otomatik tarama tam liste GONDERMEZ (sadece yeni bilet/fiyat)
  const body = JSON.stringify({ ref: "main", inputs: { report: "false" } });

  // Gecici hatalara karsi 3 kez dene (kalici/yetki hatasinda erken cik)
  let status = 0;
  for (let i = 0; i < 3; i++) {
    try {
      const r = await fetch(url, { method: "POST", headers, body });
      status = r.status;
      if (r.ok) break;
    } catch (_) { status = 0; }
    if (status === 401 || status === 403 || status === 404) break;
    await new Promise((res) => setTimeout(res, 1500));
  }
  const ok = status >= 200 && status < 300;

  if (!env.ACCESS) { // KV yoksa: sadece basarisizsa uyar
    if (!ok && env.OWNER_CHAT_ID)
      await send(env, env.OWNER_CHAT_ID, `⚠️ Tarama tetiklenemedi (GitHub ${status}).`);
    return;
  }
  if (ok) { // basarili -> sayac + uyari bayragi temizle
    await env.ACCESS.delete("sys:trigfail");
    await env.ACCESS.delete("sys:trigwarned");
    return;
  }
  // basarisiz: ardisik sayaci artir; sadece KALICI ya da YETKI hatasinda ve BIR KEZ uyar
  const fails = (parseInt((await env.ACCESS.get("sys:trigfail")) || "0", 10) || 0) + 1;
  await env.ACCESS.put("sys:trigfail", String(fails));
  const actionable = status === 401 || status === 403 || status === 404;
  const persistent = fails >= 3; // ~15 dk ardisik
  if ((actionable || persistent) && !(await env.ACCESS.get("sys:trigwarned")) && env.OWNER_CHAT_ID) {
    await env.ACCESS.put("sys:trigwarned", "1");
    const why = (status === 401 || status === 403)
      ? "GitHub token süresi dolmuş olabilir — yenilemen gerekebilir."
      : status === 404
        ? "workflow bulunamadı."
        : `${fails} taramadır tetiklenemiyor (~${fails * 5} dk).`;
    await send(env, env.OWNER_CHAT_ID, `⚠️ Tarama tetiklenemedi (GitHub ${status}). ${why}`);
  }
}

async function broadcastToAll(env, text, parseMode, replyMarkup) {
  const ids = new Set();
  if (env.OWNER_CHAT_ID) ids.add(String(env.OWNER_CHAT_ID));
  if (env.ACCESS) {
    const list = await env.ACCESS.list({ prefix: "u:" });
    for (const k of list.keys) ids.add(k.name.slice(2));
  }
  for (const id of ids) await send(env, id, text, parseMode, replyMarkup);
}

/* "✅ Aldım / ❌ Olmadı" butonuna basilinca: mesaji guncelle + tabloya yazdir.
   Tabloya yazma isini GitHub'a devrediyoruz (servis hesabi anahtari orada duruyor). */
async function onCallback(env, cq) {
  const data = String(cq.data || "");
  const chatId = cq.message && cq.message.chat && cq.message.chat.id;
  const m = data.match(/^m\|(\d+)\|(\d{8})\|([01])$/);
  await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/answerCallbackQuery`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ callback_query_id: cq.id,
                           text: m ? (m[3] === "1" ? "Kaydediliyor: Alındı" : "Kaydediliyor: Olmadı")
                                   : "Anlaşılmadı" }),
  });
  if (!m) return;
  const [, satir, ymd, ok] = m;
  const kim = (cq.from && (cq.from.first_name || cq.from.username)) || "";
  const damga = new Date().toLocaleString("tr-TR", { timeZone: "Europe/Istanbul" });

  // Butonlari kaldir (tekrar basilmasin). Mesaj METNINE dokunmuyoruz:
  // yoksa <code> bloklari duz metne doner ve dokun-kopyala ozelligi kaybolur.
  if (cq.message) {
    await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/editMessageReplyMarkup`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, message_id: cq.message.message_id,
                             reply_markup: { inline_keyboard: [] } }),
    });
  }
  // Kisa teyit (yetkili herkes gorsun: ayni bilet icin iki kisi ugrasmasin)
  await broadcastToAll(env,
    `${ok === "1" ? "✅ <b>ALINDI</b>" : "❌ <b>Olmadı</b>"} — ${esc(kim)} · ${esc(damga)}`,
    "HTML");
  // Tabloya yaz (GitHub tarafinda; ~1 dk)
  await dispatchWorkflow(env, { mark: `${satir}|${ymd}|${ok}`, report: "false" });
}

async function dispatchWorkflow(env, inputs) {
  if (!env.GH_TOKEN) return false;
  const owner = env.GH_OWNER || "emelian293";
  const repo = env.GH_REPO || "tk-fare-watch";
  const wf = env.GH_WORKFLOW || "watch.yml";
  try {
    const r = await fetch(
      `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${wf}/dispatches`,
      { method: "POST",
        headers: { "Authorization": `Bearer ${env.GH_TOKEN}`,
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "tk-fare-bot" },
        body: JSON.stringify({ ref: "main", inputs }) });
    return r.ok;
  } catch (_) { return false; }
}

async function route(env, msg) {
  const chatId = String(msg.chat.id);
  const text = msg.text.trim();
  const lower = text.toLowerCase();
  const isOwner = env.OWNER_CHAT_ID && chatId === String(env.OWNER_CHAT_ID);

  // --- Sahip: erisim yonetimi komutlari ("/" opsiyonel, TR harf toleransli) ---
  if (isOwner) {
    const parts = text.replace(/^\//, "").trim().split(/\s+/);
    const cmd = trNorm(parts[0].toLowerCase());
    const id = parts[1];
    if (cmd === "izinver" && id && /^-?\d+$/.test(id))
      return cmdAllow(env, chatId, id, parts.slice(2).join(" "));
    if (cmd === "izinal" && id && /^-?\d+$/.test(id))
      return cmdRevoke(env, chatId, id);
    if (["kullanicilar", "kullanici", "liste", "users"].includes(cmd))
      return cmdList(env, chatId);
    // Musteri raporu: kriterler + su an uyan biletler (yalniz sahip)
    if (["musteri", "musteriler", "musterilerim"].includes(cmd)) {
      const ok = await triggerCheck(env);
      return send(env, chatId, ok
        ? "⏳ <b>Müşteri raporu hazırlanıyor…</b>\nGüncel biletlerle birlikte ~1 dk içinde gelecek."
        : "⚠️ Rapor tetiklenemedi (GitHub bağlantısı).", "HTML");
    }
    if (["ayarlar", "ayar", "settings"].includes(cmd))
      return cmdSettings(env, chatId);
    if (cmd === "business")
      return cmdBusiness(env, chatId, parts.slice(1));
    if (cmd === "bildirim")
      return cmdBildirim(env, chatId, parts.slice(1));
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

// ---------- Bildirim ayarlari (KV) ----------

const DEFAULT_CFG = {
  new_ticket: { Ekonomi: true, Premium: true, Business_months: [8] },
  price_drop: { Ekonomi: true, Business: true, Premium: true },
};

async function getCfg(env) {
  if (!env.ACCESS) return DEFAULT_CFG;
  try {
    const raw = await env.ACCESS.get("cfg");
    if (!raw) return DEFAULT_CFG;
    const c = JSON.parse(raw);
    return {
      new_ticket: { ...DEFAULT_CFG.new_ticket, ...(c.new_ticket || {}) },
      price_drop: { ...DEFAULT_CFG.price_drop, ...(c.price_drop || {}) },
    };
  } catch { return DEFAULT_CFG; }
}

async function putCfg(env, cfg) { await env.ACCESS.put("cfg", JSON.stringify(cfg)); }

const ONOFF = (b) => (b ? "açık ✅" : "kapalı ❌");
const AC_WORDS = ["ac", "acik", "open", "on", "1", "evet", "aç", "açık"];
const KAPA_WORDS = ["kapa", "kapat", "kapali", "off", "0", "hayir", "kapalı", "hayır"];

async function cmdSettings(env, chatId) {
  const c = await getCfg(env);
  const months = (c.new_ticket.Business_months || []).map((m) => AY_ADI[m]).join(", ") || "kapalı";
  const t =
    "⚙️ <b>Bildirim ayarları</b>\n\n" +
    "<b>🟢 Yeni bilet</b>\n" +
    `• Ekonomi: ${ONOFF(c.new_ticket.Ekonomi)} (her ay)\n` +
    `• Premium: ${ONOFF(c.new_ticket.Premium)} (her ay)\n` +
    `• Business: <b>${months}</b>\n\n` +
    "<b>🔻 Fiyat düşüşü</b> (her ay)\n" +
    `• Ekonomi: ${ONOFF(c.price_drop.Ekonomi)}\n` +
    `• Business: ${ONOFF(c.price_drop.Business)}\n` +
    `• Premium: ${ONOFF(c.price_drop.Premium)}\n\n` +
    "<b>Değiştir:</b>\n" +
    "<code>/business eylül ekim</code> — Business yeni bilet ayları\n" +
    "<code>/business kapalı</code> — Business yeni bileti kapat\n" +
    "<code>/bildirim yeni-premium kapa</code> — bir ayarı aç/kapat\n" +
    "<i>anahtarlar: yeni-ekonomi, yeni-premium, dusus-ekonomi, dusus-business, dusus-premium</i>";
  return send(env, chatId, t, "HTML");
}

async function cmdBusiness(env, chatId, args) {
  if (!args.length)
    return send(env, chatId, "Kullanım: <code>/business eylül ekim</code> ya da <code>/business kapalı</code>", "HTML");
  const c = await getCfg(env);
  const first = trNorm(args[0].toLowerCase());
  if (["kapali", "kapat", "yok", "hicbiri", "hic", "none", "off"].includes(first)) {
    c.new_ticket.Business_months = [];
  } else {
    const months = [];
    for (const a of args) {
      const key = trNorm(a.toLowerCase());
      if (key in AY_NO) months.push(AY_NO[key]);
      else if (/^\d{1,2}$/.test(a) && +a >= 1 && +a <= 12) months.push(+a);
    }
    if (!months.length)
      return send(env, chatId, "Ay anlaşılmadı. Örn: <code>/business eylül</code>", "HTML");
    c.new_ticket.Business_months = [...new Set(months)].sort((a, b) => a - b);
  }
  await putCfg(env, c);
  const lbl = c.new_ticket.Business_months.map((m) => AY_ADI[m]).join(", ") || "kapalı";
  return send(env, chatId, `✅ Business yeni bilet ayları: <b>${lbl}</b>`, "HTML");
}

async function cmdBildirim(env, chatId, args) {
  if (args.length < 2)
    return send(env, chatId, "Kullanım: <code>/bildirim yeni-ekonomi kapa</code>", "HTML");
  const key = trNorm(args[0].toLowerCase());
  const v = trNorm(args[1].toLowerCase());
  const val = AC_WORDS.includes(v) ? true : KAPA_WORDS.includes(v) ? false : null;
  if (val === null) return send(env, chatId, "İkinci kelime <code>ac</code> ya da <code>kapa</code> olmalı.", "HTML");
  const map = {
    "yeni-ekonomi": ["new_ticket", "Ekonomi"], "yeni-premium": ["new_ticket", "Premium"],
    "dusus-ekonomi": ["price_drop", "Ekonomi"], "dusus-business": ["price_drop", "Business"],
    "dusus-premium": ["price_drop", "Premium"],
  };
  const path = map[key];
  if (!path)
    return send(env, chatId, "Bilinmeyen anahtar.\nGeçerli: yeni-ekonomi, yeni-premium, dusus-ekonomi, dusus-business, dusus-premium", "HTML");
  const c = await getCfg(env);
  c[path[0]][path[1]] = val;
  await putCfg(env, c);
  return send(env, chatId, `✅ <b>${key}</b> → ${val ? "açık ✅" : "kapalı ❌"}`, "HTML");
}

// ---------- Ay sorgusu ----------

async function handleQuery(env, chatId, text) {
  // "eylül" · "tümü" · "ekonomi" · "eylül ekonomi" — sozcukler her sirada olabilir
  let want, cabin;
  for (const raw of text.split(/\s+/).filter(Boolean)) {
    const n = trNorm(raw);
    if (ALL_WORDS.includes(raw) || ALL_WORDS.includes(n)) want = "all";
    else if (raw in AY_NO) want = AY_NO[raw];
    else if (n in AY_NO) want = AY_NO[n];
    else if (CABIN_WORDS[n]) cabin = CABIN_WORDS[n];
  }
  if (want === undefined && cabin) want = "all";   // sadece sinif yazildiysa: tum aylar
  if (want === undefined)
    return send(env, chatId, "Anlamadım 🤔\n\n" + helpText(false), "HTML");

  const [data, hb] = await Promise.all([fetchData(env), lastScanInfo(env)]);
  let dates = data.dates || {};
  if (cabin) {   // sinif filtresi: sadece o sinifin tarifeleri kalsin
    const only = {};
    for (const k of Object.keys(dates)) {
      const keep = (dates[k] || []).filter((f) => f.c === cabin);
      if (keep.length) only[k] = keep;
    }
    dates = only;
  }
  let keys = Object.keys(dates).sort();
  if (want !== "all") keys = keys.filter((k) => monthOf(k) === want);

  const scope = (want === "all" ? "Tüm aylar" : AY_ADI[want]) +
                (cabin ? " · " + CABIN_ADI[cabin] : "");
  if (keys.length === 0) {
    return send(env, chatId,
      `📭 <b>${scope}</b> için şu an satışta bilet yok.\n` + freshnessLine(data, hb), "HTML");
  }

  const label = scope;
  await send(env, chatId,
    `🎫 <b>${label} — ASB→İstanbul</b> · ${keys.length} tarihte bilet\n` +
    `E=Ekonomi · B=Business · P=Premium · Klt=son N koltuk · tek yön/USD\n` +
    freshnessLine(data, hb), "HTML");

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

function trNorm(s) {   // Turkce harfleri ASCII'ye (komut eslesmesi icin)
  return String(s)
    // "İ".toLowerCase() -> "i" + birlesik nokta (U+0307) birakir; once onu temizle
    .replace(/[̀-ͯ]/g, "")
    .replace(/ı/g, "i").replace(/İ/g, "i").replace(/ş/g, "s")
    .replace(/ç/g, "c").replace(/ğ/g, "g").replace(/ö/g, "o").replace(/ü/g, "u");
}

async function fetchData(env) {
  // Her sorguda TAZE oku: 10 sn'lik cache-buster + CDN cache kapali.
  // (raw.githubusercontent varsayilan olarak 5 dk cache'ler -> bayat tablo sebebiydi)
  const base = env.DATA_URL || DEFAULT_DATA_URL;
  const url = base + (base.includes("?") ? "&" : "?") + "_=" + Math.floor(Date.now() / 10000);
  const r = await fetch(url, { cf: { cacheTtl: 0, cacheEverything: false } });
  if (!r.ok) throw new Error("veri alınamadı (" + r.status + ")");
  return await r.json();
}

async function lastScanInfo(env) {
  if (!env.ACCESS) return null;
  try { return JSON.parse((await env.ACCESS.get("sys:lastscan")) || "null"); }
  catch { return null; }
}

function agoText(ms) {
  const m = Math.max(0, Math.round(ms / 60000));
  if (m < 1) return "az önce";
  if (m < 60) return m + " dk önce";
  const h = Math.floor(m / 60);
  return h + " sa " + (m % 60) + " dk önce";
}

/** Tablonun ne kadar taze oldugunu tek satirda anlatir (+ gerekirse uyarir). */
function freshnessLine(data, hb) {
  const parts = [];
  if (data && data.updated) parts.push("veri: " + esc(data.updated) + " (değişim)");
  let warn = "";
  if (hb && hb.t) {
    const age = Date.now() - hb.t;
    parts.push("son kontrol: " + agoText(age));
    if (age > 20 * 60 * 1000)
      warn = "\n⚠️ <b>Sistem " + agoText(age).replace(" önce", "") +
             "dır kontrol edemedi — liste güncel olmayabilir.</b>";
  } else {
    warn = "\n⚠️ <b>Son kontrol zamanı bilinmiyor — liste güncel olmayabilir.</b>";
  }
  return "<i>🕐 " + parts.join(" · ") + "</i>" + warn;
}

async function send(env, chatId, text, parseMode, replyMarkup) {
  const body = { chat_id: chatId, text, disable_web_page_preview: true };
  if (parseMode) body.parse_mode = parseMode;
  if (replyMarkup) body.reply_markup = replyMarkup;
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
    "<b>Sınıfa göre</b> (tüm aylar):\n" +
    "<code>ekonomi</code> · <code>business</code> · <code>premium</code>\n" +
    "İkisini birleştirebilirsin: <code>eylül ekonomi</code>\n\n" +
    "🟢 yeni bilet / 🔻 fiyat düşüşü olunca ayrıca otomatik haber gelir.";
  if (isOwner)
    t += "\n\n<b>Yönetici komutları</b>\n" +
      "<code>müşteri</code> — müşteri kriterleri + şu an uyan biletler\n" +
      "<code>/ayarlar</code> — bildirim ayarları\n" +
      "<code>/business eylül</code> — Business yeni bilet ayı\n" +
      "<code>/izinver &lt;id&gt; [ad]</code> — erişim ver\n" +
      "<code>/izinal &lt;id&gt;</code> — erişimi al\n" +
      "<code>/kullanicilar</code> — izinli listesi";
  return t;
}
