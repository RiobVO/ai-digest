// Приём оценок 👍/👎 для ИИ-дайджеста: кнопки в Telegram (вебхук) и на странице GitHub Pages.
// Сборщик забирает оценки через GET /votes с токеном. Хранилище — KV: последняя оценка по записи перезаписывает прежнюю.

const ID_RE = /^\d{8}-\d{1,3}$/;
const VOTE_PREFIX = "vote:";
const READ_PREFIX = "read:";
// Отметка «попробовал» у фишки в арсенале: бессрочно, снимается повторным нажатием
const TRIED_PREFIX = "tried:";
const ANSWERS = { up: "👍 Учту — больше такого", down: "👎 Понял — меньше такого" };
// «Прочитано» нужно только вечернему выпуску того же дня — неделя с запасом, дальше ключ удаляет сам KV.
// Оценки без срока: промпт берёт последние 20, как бы редко читатель ни голосовал
const READ_TTL_SECONDS = 7 * 24 * 3600;

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

// Запись дублируется в metadata ключа: list отдаёт её сразу, без get на каждый ключ — у KV лимит 1000 операций
// на вызов, и с get на ключ /votes упал бы через пару месяцев отметок. Запись короткая, лимит metadata — 1024 байта
async function putRecord(env, key, record, options = {}) {
  await env.VOTES.put(key, JSON.stringify(record), { metadata: record, ...options });
}

async function saveVote(env, id, v, via) {
  await putRecord(env, VOTE_PREFIX + id, { id, v, via, at: new Date().toISOString() });
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
  return { "Access-Control-Allow-Origin": env.PAGE_ORIGIN, "Access-Control-Allow-Methods": "GET, POST", Vary: "Origin" };
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

// Отметка «прочитано»: раскрытый разбор или переход к первоисточнику; вечерний выпуск по ним находит пропущенное
async function pageRead(request, url, env) {
  if (request.headers.get("Origin") !== env.PAGE_ORIGIN) return new Response("forbidden", { status: 403 });
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(env) });
  if (request.method !== "POST") return new Response("method not allowed", { status: 405, headers: cors(env) });
  const id = url.searchParams.get("e") ?? "";
  if (!ID_RE.test(id)) return new Response("bad request", { status: 400, headers: cors(env) });
  await putRecord(env, READ_PREFIX + id, { id, at: new Date().toISOString() }, { expirationTtl: READ_TTL_SECONDS });
  return Response.json({ ok: true }, { headers: cors(env) });
}

// Арсенал фишек: POST ставит или снимает галочку (v=1/0), GET отдаёт отмеченные — страница статичная и узнаёт
// свои галочки только отсюда. Оба только с нашего сайта, как /vote и /read
async function pageTried(request, url, env) {
  if (request.headers.get("Origin") !== env.PAGE_ORIGIN) return new Response("forbidden", { status: 403 });
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(env) });
  if (request.method === "GET") {
    const ids = (await listPrefix(env, TRIED_PREFIX)).map((r) => r.id);
    return Response.json({ ids }, { headers: cors(env) });
  }
  if (request.method !== "POST") return new Response("method not allowed", { status: 405, headers: cors(env) });
  const id = url.searchParams.get("e") ?? "";
  const v = url.searchParams.get("v");
  if (!ID_RE.test(id) || (v !== "1" && v !== "0")) return new Response("bad request", { status: 400, headers: cors(env) });
  if (v === "1") await putRecord(env, TRIED_PREFIX + id, { id, at: new Date().toISOString() });
  else await env.VOTES.delete(TRIED_PREFIX + id);
  return Response.json({ ok: true }, { headers: cors(env) });
}

async function listPrefix(env, prefix) {
  const rows = [];
  let cursor;
  do {
    const page = await env.VOTES.list({ prefix, cursor });
    for (const { name, metadata } of page.keys) {
      if (metadata) {
        rows.push(metadata);
        continue;
      }
      // Ключи, записанные до переноса записи в metadata (октябрь 2026): их набор фиксирован, get нужен только им
      const raw = await env.VOTES.get(name);
      if (raw) rows.push(JSON.parse(raw));
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return rows;
}

async function listVotes(request, env) {
  const auth = request.headers.get("Authorization") ?? "";
  if (!safeEqual(auth, `Bearer ${env.READ_TOKEN}`)) return new Response("unauthorized", { status: 401 });
  return Response.json({ votes: await listPrefix(env, VOTE_PREFIX), reads: await listPrefix(env, READ_PREFIX) });
}

// Расписание GitHub Actions на новом репозитории часами не срабатывало и опаздывает в пиковые минуты,
// поэтому время держит cron Cloudflare, а GitHub только исполняет. Ключи — ровно строки из wrangler.jsonc.
const JOBS = {
  "7 3 * * *": { workflow: "digest.yml", slot: "morning", label: "утренний выпуск" },
  "7 13 * * *": { workflow: "digest.yml", slot: "evening", label: "вечерний выпуск" },
  "17 * * * *": { workflow: "alert.yml", label: "проверка срочных" },
};
const TASHKENT_OFFSET_MS = 5 * 3600 * 1000;
const SUNDAY = 0;

function dispatchBody(job, scheduledTime) {
  if (!job.slot) return { ref: "main" };
  // Итог недели — с утренним выпуском воскресенья по Ташкенту; день берём из запланированного времени, а не фактического
  const sunday = new Date(scheduledTime + TASHKENT_OFFSET_MS).getUTCDay() === SUNDAY;
  return { ref: "main", inputs: { daily: "true", slot: job.slot, weekly: String(job.slot === "morning" && sunday) } };
}

async function runSchedule(controller, env) {
  const job = JOBS[controller.cron];
  if (!job) throw new Error(`неизвестное расписание: ${controller.cron}`);
  // Повтор после сбоя мог бы запустить один выпуск дважды и прислать два сообщения
  controller.noRetry?.();
  const response = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${job.workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.SCHEDULER_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ai-digest-scheduler",
      "content-type": "application/json",
    },
    body: JSON.stringify(dispatchBody(job, controller.scheduledTime)),
  });
  if (response.ok) return;
  // Без запуска workflow его собственное оповещение о сбое не сработает — предупреждаем отсюда.
  // Часовую проверку срочных не дублируем в Telegram, иначе при истёкшем токене было бы сообщение каждый час
  if (job.slot) {
    await callTelegram(env, "sendMessage", {
      chat_id: env.OWNER_CHAT_ID,
      text: `⚠️ ИИ-дайджест: ${job.label} не запущен — GitHub ответил ${response.status}. Проверь токен SCHEDULER_TOKEN.`,
    });
  }
  throw new Error(`GitHub не запустил ${job.workflow}: ${response.status}`);
}

export default {
  async scheduled(controller, env) {
    await runSchedule(controller, env);
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/telegram" && request.method === "POST") return telegram(request, env);
    if (url.pathname === "/vote") return pageVote(request, url, env);
    if (url.pathname === "/read") return pageRead(request, url, env);
    if (url.pathname === "/tried") return pageTried(request, url, env);
    if (url.pathname === "/votes" && request.method === "GET") return listVotes(request, env);
    return new Response("not found", { status: 404 });
  },
};
