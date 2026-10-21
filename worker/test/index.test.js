import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import worker from "../src/index.js";

// Как настоящий KV: list отдаёт страницы по курсору и metadata ключа; emptyFirst — пустая незавершённая первая страница,
// которую KV вправе вернуть. Счётчики показывают, сколько операций ушло на один запрос
function memoryKV({ pageSize = 1000, emptyFirst = false } = {}) {
  const store = new Map();
  const calls = { get: 0, list: [] };
  return {
    store,
    calls,
    async put(key, value, options = {}) { store.set(key, { value, ...options }); },
    async get(key) { calls.get += 1; return store.get(key)?.value ?? null; },
    async delete(key) { store.delete(key); },
    async list({ prefix, cursor }) {
      calls.list.push({ prefix, cursor });
      if (emptyFirst && cursor === undefined) return { keys: [], list_complete: false, cursor: "0" };
      const names = [...store.keys()].filter((k) => k.startsWith(prefix)).sort();
      const start = Number(cursor ?? 0);
      const done = start + pageSize >= names.length;
      const keys = names.slice(start, start + pageSize).map((name) => {
        const { metadata } = store.get(name);
        return metadata ? { name, metadata } : { name };
      });
      return done ? { keys, list_complete: true } : { keys, list_complete: false, cursor: String(start + pageSize) };
    },
  };
}

const votesOf = async (env) =>
  (await worker.fetch(new Request("https://w.dev/votes", { headers: { Authorization: "Bearer read" } }), env)).json();
const pageVote = (id, v) =>
  new Request(`https://w.dev/vote?e=${id}&v=${v}`, { method: "POST", headers: { Origin: "https://riobvo.github.io" } });
const pageRead = (id) => new Request(`https://w.dev/read?e=${id}`, { method: "POST", headers: { Origin: "https://riobvo.github.io" } });

const tried = (id, v, origin = "https://riobvo.github.io") =>
  new Request(`https://w.dev/tried?e=${id}&v=${v}`, { method: "POST", headers: { Origin: origin } });
const triedList = async (origin = "https://riobvo.github.io") =>
  worker.fetch(new Request("https://w.dev/tried", { headers: { Origin: origin } }), env);

let env;
let telegramCalls;

beforeEach(() => {
  env = {
    VOTES: memoryKV(),
    WEBHOOK_SECRET: "hook",
    READ_TOKEN: "read",
    TELEGRAM_BOT_TOKEN: "bot",
    OWNER_CHAT_ID: "42",
    GITHUB_REPO: "RiobVO/ai-digest",
    PAGE_ORIGIN: "https://riobvo.github.io",
  };
  telegramCalls = [];
  globalThis.fetch = async (url, init) => {
    telegramCalls.push({ url: String(url), body: JSON.parse(init.body) });
    return new Response("{}");
  };
});

const callback = (data, from = 42) => ({ callback_query: { id: "cb1", from: { id: from }, data } });
const hook = (update, secret = "hook") => new Request("https://w.dev/telegram", {
  method: "POST",
  headers: { "X-Telegram-Bot-Api-Secret-Token": secret, "content-type": "application/json" },
  body: JSON.stringify(update),
});

test("кнопка в Telegram сохраняет оценку и сразу отвечает", async () => {
  const res = await worker.fetch(hook(callback("u:20261016-0")), env);
  assert.equal(res.status, 200);
  const saved = JSON.parse(env.VOTES.store.get("vote:20261016-0").value);
  assert.equal(saved.v, "up");
  assert.equal(saved.via, "telegram");
  assert.match(telegramCalls[0].url, /answerCallbackQuery$/);
  assert.equal(telegramCalls[0].body.callback_query_id, "cb1");
});

test("вебхук без секрета отклоняется", async () => {
  const res = await worker.fetch(hook(callback("u:20261016-0"), "wrong"), env);
  assert.equal(res.status, 403);
  assert.equal(env.VOTES.store.size, 0);
});

test("чужой пользователь бота не может голосовать", async () => {
  await worker.fetch(hook(callback("u:20261016-0", 777)), env);
  assert.equal(env.VOTES.store.size, 0);
  assert.equal(telegramCalls.length, 1);
});

test("мусорные данные кнопки игнорируются", async () => {
  const res = await worker.fetch(hook(callback("u:../../etc")), env);
  assert.equal(res.status, 200);
  assert.equal(env.VOTES.store.size, 0);
});

test("оценка со страницы принимается только с нашего сайта", async () => {
  const vote = (origin) => new Request("https://w.dev/vote?e=20261016-1&v=down", { method: "POST", headers: { Origin: origin } });
  const ok = await worker.fetch(vote("https://riobvo.github.io"), env);
  assert.equal(ok.status, 200);
  assert.equal(ok.headers.get("Access-Control-Allow-Origin"), "https://riobvo.github.io");
  assert.equal(JSON.parse(env.VOTES.store.get("vote:20261016-1").value).v, "down");
  const evil = await worker.fetch(vote("https://evil.example"), env);
  assert.equal(evil.status, 403);
});

test("список оценок отдаётся только с токеном", async () => {
  await worker.fetch(hook(callback("d:20261016-2")), env);
  const denied = await worker.fetch(new Request("https://w.dev/votes"), env);
  assert.equal(denied.status, 401);
  const res = await worker.fetch(new Request("https://w.dev/votes", { headers: { Authorization: "Bearer read" } }), env);
  const body = await res.json();
  assert.deepEqual(body.votes.map((v) => [v.id, v.v]), [["20261016-2", "down"]]);
});

test("отметка «прочитано» со страницы сохраняется и отдаётся вместе с оценками", async () => {
  const read = (origin, id = "20261017-2") => new Request(`https://w.dev/read?e=${id}`, { method: "POST", headers: { Origin: origin } });
  const ok = await worker.fetch(read("https://riobvo.github.io"), env);
  assert.equal(ok.status, 200);
  assert.equal((await worker.fetch(read("https://evil.example"), env)).status, 403);
  assert.equal((await worker.fetch(read("https://riobvo.github.io", "../x"), env)).status, 400);
  const res = await worker.fetch(new Request("https://w.dev/votes", { headers: { Authorization: "Bearer read" } }), env);
  const body = await res.json();
  assert.deepEqual(body.reads.map((r) => r.id), ["20261017-2"]);
  assert.deepEqual(body.votes, []);
});

test("после голоса кнопки под сообщением отмечают выбор, повторный выбор переносит отметку", async () => {
  const markup = { inline_keyboard: [
    [{ text: "👍 1", callback_data: "u:20261016-0" }, { text: "👍 2", callback_data: "u:20261016-1" }],
    [{ text: "👎 1", callback_data: "d:20261016-0" }, { text: "👎 2", callback_data: "d:20261016-1" }],
  ] };
  const withMessage = (data) => ({ callback_query: { id: "cb", from: { id: 42 }, data,
    message: { message_id: 7, chat: { id: 42 }, reply_markup: markup } } });

  await worker.fetch(hook(withMessage("u:20261016-0")), env);
  const edit = telegramCalls.find((c) => c.url.endsWith("/editMessageReplyMarkup"));
  assert.equal(edit.body.message_id, 7);
  const texts = edit.body.reply_markup.inline_keyboard.flat().map((b) => b.text);
  assert.deepEqual(texts, ["✅ 👍 1", "👍 2", "👎 1", "👎 2"]);

  telegramCalls = [];
  markup.inline_keyboard = edit.body.reply_markup.inline_keyboard;
  await worker.fetch(hook(withMessage("d:20261016-0")), env);
  const moved = telegramCalls.find((c) => c.url.endsWith("/editMessageReplyMarkup"));
  assert.deepEqual(moved.body.reply_markup.inline_keyboard.flat().map((b) => b.text), ["👍 1", "👍 2", "✅ 👎 1", "👎 2"]);
});

const tick = (cron, iso) => ({ cron, scheduledTime: Date.parse(iso) });
const dispatches = () => telegramCalls.filter((c) => c.url.includes("api.github.com"));

test("расписание запускает утренний и вечерний выпуски и часовую проверку срочных", async () => {
  env.SCHEDULER_TOKEN = "gh";
  await worker.scheduled(tick("7 3 * * *", "2026-10-17T03:07:00Z"), env);
  await worker.scheduled(tick("7 13 * * *", "2026-10-17T13:07:00Z"), env);
  await worker.scheduled(tick("17 * * * *", "2026-10-17T05:17:00Z"), env);
  const [morning, evening, alert] = dispatches();
  assert.match(morning.url, /\/actions\/workflows\/digest\.yml\/dispatches$/);
  assert.deepEqual(morning.body, { ref: "main", inputs: { daily: "true", slot: "morning", weekly: "false" } });
  assert.deepEqual(evening.body, { ref: "main", inputs: { daily: "true", slot: "evening", weekly: "false" } });
  assert.match(alert.url, /\/actions\/workflows\/alert\.yml\/dispatches$/);
  assert.deepEqual(alert.body, { ref: "main" });
});

test("итог недели — только с утренним выпуском воскресенья по Ташкенту", async () => {
  env.SCHEDULER_TOKEN = "gh";
  await worker.scheduled(tick("7 3 * * *", "2026-10-18T03:07:00Z"), env);   // воскресенье, 08:07
  await worker.scheduled(tick("7 13 * * *", "2026-10-18T13:07:00Z"), env);  // воскресенье, 18:07
  const [morning, evening] = dispatches();
  assert.equal(morning.body.inputs.weekly, "true");
  assert.equal(evening.body.inputs.weekly, "false");
});

test("отказ GitHub роняет запуск по расписанию, а токен не попадает в ошибку", async () => {
  env.SCHEDULER_TOKEN = "secret-gh-token";
  globalThis.fetch = async () => new Response("Bad credentials", { status: 401 });
  await assert.rejects(worker.scheduled(tick("17 * * * *", "2026-10-17T05:17:00Z"), env),
    (err) => /401/.test(err.message) && !err.message.includes("secret-gh-token"));
});

test("сбой запуска выпуска присылает владельцу предупреждение в Telegram", async () => {
  env.SCHEDULER_TOKEN = "secret-gh-token";
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), body: JSON.parse(init.body) });
    return String(url).includes("api.github.com") ? new Response("Bad credentials", { status: 401 }) : new Response("{}");
  };
  await assert.rejects(worker.scheduled(tick("7 3 * * *", "2026-10-17T03:07:00Z"), env));
  const warn = calls.find((c) => c.url.endsWith("/sendMessage"));
  assert.equal(warn.body.chat_id, "42");
  assert.match(warn.body.text, /утренний выпуск не запущен/);
  assert.ok(!warn.body.text.includes("secret-gh-token"));
});

test("неизвестное расписание — ошибка, а не тихий пропуск", async () => {
  env.SCHEDULER_TOKEN = "gh";
  await assert.rejects(worker.scheduled(tick("0 0 * * *", "2026-10-17T00:00:00Z"), env), /расписани/);
});

test("новые записи читаются из metadata без get на каждый ключ, старые — через get", async () => {
  // Ключ, записанный до переноса записи в metadata: значение есть, metadata нет
  env.VOTES.store.set("vote:20261016-0", { value: JSON.stringify({ id: "20261016-0", v: "up", via: "telegram", at: "2026-10-16T09:00:00Z" }) });
  await worker.fetch(pageVote("20261017-1", "down"), env);
  await worker.fetch(pageRead("20261017-2"), env);
  await worker.fetch(hook(callback("u:20261017-3")), env);
  env.VOTES.calls.get = 0;
  const body = await votesOf(env);
  assert.deepEqual(body.votes.map((v) => [v.id, v.v]).sort(), [["20261016-0", "up"], ["20261017-1", "down"], ["20261017-3", "up"]]);
  assert.deepEqual(body.reads.map((r) => r.id), ["20261017-2"]);
  assert.equal(env.VOTES.calls.get, 1);
});

test("отметка «прочитано» истекает через неделю, оценка хранится бессрочно", async () => {
  await worker.fetch(pageVote("20261017-1", "up"), env);
  await worker.fetch(pageRead("20261017-2"), env);
  const vote = env.VOTES.store.get("vote:20261017-1");
  const read = env.VOTES.store.get("read:20261017-2");
  assert.equal(vote.expirationTtl, undefined);
  assert.equal(read.expirationTtl, 7 * 24 * 3600);
  assert.deepEqual(vote.metadata, JSON.parse(vote.value));
  assert.deepEqual(read.metadata, JSON.parse(read.value));
});

test("список обходит все страницы, включая пустую незавершённую, и не теряет префикс", async () => {
  env.VOTES = memoryKV({ pageSize: 2, emptyFirst: true });
  for (const n of [1, 2, 3, 4, 5]) await worker.fetch(pageVote(`20261017-${n}`, "up"), env);
  const body = await votesOf(env);
  assert.deepEqual(body.votes.map((v) => v.id).sort(), ["20261017-1", "20261017-2", "20261017-3", "20261017-4", "20261017-5"]);
  assert.ok(env.VOTES.calls.list.every((c) => c.prefix === "vote:" || c.prefix === "read:"));
  assert.equal(env.VOTES.calls.list.filter((c) => c.prefix === "vote:").length, 4);
  assert.equal(env.VOTES.calls.get, 0);
});

test("повторный голос заменяет и значение, и metadata", async () => {
  await worker.fetch(pageVote("20261017-1", "up"), env);
  await worker.fetch(pageVote("20261017-1", "down"), env);
  const body = await votesOf(env);
  assert.deepEqual(body.votes.map((v) => [v.id, v.v]), [["20261017-1", "down"]]);
  assert.equal(env.VOTES.store.get("vote:20261017-1").metadata.v, "down");
});

test("галочка «попробовал» сохраняется, читается страницей и снимается", async () => {
  assert.equal((await worker.fetch(tried("20261021-22", 1), env)).status, 200);
  assert.equal((await worker.fetch(tried("20261021-21", 1), env)).status, 200);
  const res = await triedList();
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), "https://riobvo.github.io");
  assert.deepEqual((await res.json()).ids.sort(), ["20261021-21", "20261021-22"]);
  assert.equal((await worker.fetch(tried("20261021-22", 0), env)).status, 200);
  assert.deepEqual((await (await triedList()).json()).ids, ["20261021-21"]);
});

test("галочка с чужого сайта не принимается и не отдаётся", async () => {
  assert.equal((await worker.fetch(tried("20261021-22", 1, "https://evil.example"), env)).status, 403);
  assert.equal(env.VOTES.store.size, 0);
  assert.equal((await triedList("https://evil.example")).status, 403);
});

test("галочка с мусорным id или значением отклоняется", async () => {
  assert.equal((await worker.fetch(tried("../votes", 1), env)).status, 400);
  assert.equal((await worker.fetch(tried("20261021-22", "yes"), env)).status, 400);
  assert.equal(env.VOTES.store.size, 0);
});
