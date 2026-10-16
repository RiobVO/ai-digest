import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import worker from "../src/index.js";

function memoryKV() {
  const store = new Map();
  return {
    store,
    async put(key, value) { store.set(key, value); },
    async get(key) { return store.get(key) ?? null; },
    async list({ prefix }) {
      return { keys: [...store.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })), list_complete: true };
    },
  };
}

let env;
let telegramCalls;

beforeEach(() => {
  env = {
    VOTES: memoryKV(),
    WEBHOOK_SECRET: "hook",
    READ_TOKEN: "read",
    TELEGRAM_BOT_TOKEN: "bot",
    OWNER_CHAT_ID: "42",
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
  const saved = JSON.parse(env.VOTES.store.get("vote:20261016-0"));
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
  assert.equal(JSON.parse(env.VOTES.store.get("vote:20261016-1")).v, "down");
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
