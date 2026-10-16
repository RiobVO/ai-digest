// Приём оценок 👍/👎 для ИИ-дайджеста: кнопки в Telegram (вебхук) и на странице GitHub Pages.
// Сборщик забирает оценки через GET /votes с токеном. Хранилище — KV: последняя оценка по записи перезаписывает прежнюю.

const ID_RE = /^\d{8}-\d{1,3}$/;
const VOTE_PREFIX = "vote:";
const ANSWERS = { up: "👍 Учту — больше такого", down: "👎 Понял — меньше такого" };

const enc = new TextEncoder();

function safeEqual(a, b) {
  const x = enc.encode(a);
  const y = enc.encode(b);
  if (x.length !== y.length) return false;
  // Сравнение за постоянное время, чтобы токен нельзя было подобрать по времени ответа
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

async function saveVote(env, id, v, via) {
  await env.VOTES.put(VOTE_PREFIX + id, JSON.stringify({ id, v, via, at: new Date().toISOString() }));
}

async function callTelegram(env, method, body) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function answerCallback(env, callbackId, text) {
  await callTelegram(env, "answerCallbackQuery", { callback_query_id: callbackId, text });
}

const MARK = "✅ ";

// Отмечаем выбранную кнопку записи, снимая отметку с парной: кнопки остаются, чтобы можно было передумать
function markVoted(markup, id, chosen) {
  return {
    inline_keyboard: markup.inline_keyboard.map((row) => row.map((button) => {
      const [kind, buttonId] = String(button.callback_data ?? "").split(":");
      if (buttonId !== id) return button;
      const base = button.text.startsWith(MARK) ? button.text.slice(MARK.length) : button.text;
      return { ...button, text: kind === chosen ? MARK + base : base };
    })),
  };
}

async function telegram(request, env) {
  const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") ?? "";
  if (!safeEqual(secret, env.WEBHOOK_SECRET)) return new Response("forbidden", { status: 403 });
  const update = await request.json();
  const cb = update.callback_query;
  // Telegram повторяет доставку при любом не-2xx, поэтому на всё остальное отвечаем 200
  if (!cb) return new Response("ok");
  if (String(cb.from?.id) !== String(env.OWNER_CHAT_ID)) {
    await answerCallback(env, cb.id, "Оценки принимаются только от владельца дайджеста");
    return new Response("ok");
  }
  const [kind, id] = String(cb.data ?? "").split(":");
  const v = kind === "u" ? "up" : kind === "d" ? "down" : null;
  if (!v || !ID_RE.test(id ?? "")) return new Response("ok");
  await saveVote(env, id, v, "telegram");
  await answerCallback(env, cb.id, ANSWERS[v]);
  const message = cb.message;
  if (message?.reply_markup) {
    // Повторное нажатие той же кнопки Telegram отклонит как «message is not modified» — это безвредно
    await callTelegram(env, "editMessageReplyMarkup", {
      chat_id: message.chat.id, message_id: message.message_id, reply_markup: markVoted(message.reply_markup, id, kind),
    });
  }
  return new Response("ok");
}

function cors(env) {
  return { "Access-Control-Allow-Origin": env.PAGE_ORIGIN, "Access-Control-Allow-Methods": "POST", Vary: "Origin" };
}

async function pageVote(request, url, env) {
  if (request.headers.get("Origin") !== env.PAGE_ORIGIN) return new Response("forbidden", { status: 403 });
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(env) });
  if (request.method !== "POST") return new Response("method not allowed", { status: 405, headers: cors(env) });
  const id = url.searchParams.get("e") ?? "";
  const v = url.searchParams.get("v");
  if (!ID_RE.test(id) || !(v in ANSWERS)) return new Response("bad request", { status: 400, headers: cors(env) });
  await saveVote(env, id, v, "page");
  return Response.json({ ok: true }, { headers: cors(env) });
}

async function listVotes(request, env) {
  const auth = request.headers.get("Authorization") ?? "";
  if (!safeEqual(auth, `Bearer ${env.READ_TOKEN}`)) return new Response("unauthorized", { status: 401 });
  const votes = [];
  let cursor;
  do {
    const page = await env.VOTES.list({ prefix: VOTE_PREFIX, cursor });
    for (const { name } of page.keys) {
      const raw = await env.VOTES.get(name);
      if (raw) votes.push(JSON.parse(raw));
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return Response.json({ votes });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/telegram" && request.method === "POST") return telegram(request, env);
    if (url.pathname === "/vote") return pageVote(request, url, env);
    if (url.pathname === "/votes" && request.method === "GET") return listVotes(request, env);
    return new Response("not found", { status: 404 });
  },
};
