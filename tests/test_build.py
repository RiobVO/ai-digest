"""Сквозная сборка выпуска: сеть, Gemini и Chrome подменены, всё остальное — настоящий пайплайн build()."""
import argparse
import json
from datetime import datetime

import pytest

import digest.__main__ as main
from digest import llm
from digest.models import Item

CONFIG = """[digest]
model = "test-model"
lite_model = "lite-model"
audience = "разработчик"

[[source]]
name = "x"
type = "rss"
category = "news"
url = "https://x.io/feed"

[[source]]
name = "dead"
type = "rss"
category = "news"
url = "https://dead.io/feed"
"""


def raw_items(now: datetime) -> list[Item]:
    return [
        Item(source="openai", title="OpenAI releases a new reasoning model", url="https://openai.com/news/model",
             category="lab", published=now),
        Item(source="verge", title="Nvidia buys a chip startup for billions", url="https://theverge.com/nvidia",
             category="news", published=now),
        Item(source="techcrunch", title="Nvidia acquires chip startup in big deal", url="https://techcrunch.com/nv",
             category="news", published=now),
        Item(source="techcrunch", title="Save 50% on conference tickets today", url="https://techcrunch.com/ad",
             category="news", published=now),
        Item(source="Reuters", title="Ransomware takes a grocery chain offline", url="https://news.google.com/rss/articles/g",
             category="news", published=now),
    ]


SELECTION = {"headline": "День <чипов>", "lead": "Главное.", "items": [
    {"ids": [0], "section": "model", "importance": 8.5, "title": "OpenAI выкатила модель", "summary": "Факты.",
     "why": "Меняет рынок.", "story": "", "story_name": "Новая модель"},
    {"ids": [1, 2], "section": "business", "importance": 7, "title": "Nvidia купила стартап", "summary": "Сделка.",
     "why": "Консолидация.", "story": "", "story_name": ""},
]}
EXTRAS = {"more": [{"ids": [4], "title": "Сеть магазинов легла после атаки"}], "tips": []}


def fake_generate(explain_fails: bool = False, extras_fail: bool = False):
    def generate(prompt, schema, model, api_key):
        if schema is llm.SCHEMA:
            return SELECTION
        if schema is llm.EXTRAS_SCHEMA:
            if extras_fail:
                raise llm.LlmError("Gemini 503")
            return EXTRAS
        if explain_fails:
            raise llm.LlmError("Gemini 503")
        return {"items": [{"id": 0, "what": "Контекст.", "points": ["п1"], "try_it": "", "caveats": "", "commands": []}]}
    return generate


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = tmp_path / "sources.toml"
    config.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("SITE_URL", "https://me.github.io/ai-digest/")
    monkeypatch.setenv("ISSUE_SLOT", "morning")
    monkeypatch.setattr(main, "OUT", tmp_path / "out")
    monkeypatch.setattr(main, "fetch_all", lambda sources, now: (raw_items(now), ["dead: ConnectError"]))
    monkeypatch.setattr(main, "fetch_feedback", lambda url, token: None)
    monkeypatch.setattr(main, "resolve_gnews", lambda items: None)
    monkeypatch.setattr(main, "attach_discussions", lambda items: None)
    monkeypatch.setattr(main, "fetch_articles", lambda urls: {})
    monkeypatch.setattr(main, "make_cover", lambda *a: False)
    monkeypatch.setattr(llm, "generate", fake_generate())
    args = argparse.Namespace(config=config, state_dir=tmp_path / "state", site=tmp_path / "site", dry_run=False)
    return tmp_path, args


def test_build_ships_page_message_and_state_that_agree(env):
    tmp, args = env
    main.build(args)

    message = json.loads((tmp / "out" / "message.json").read_text(encoding="utf-8"))
    page_name = message["url"].rsplit("/", 1)[1]
    assert message["url"] == f"https://me.github.io/ai-digest/{page_name}"
    page = (args.site / page_name).read_text(encoding="utf-8")
    assert (args.site / "index.html").read_text(encoding="utf-8") == page
    assert page_name in (args.site / "archive.html").read_text(encoding="utf-8")
    assert "День &lt;чипов&gt;" in page and "Контекст." in page and "dead" in page

    issues = json.loads((args.state_dir / "issues.json").read_text(encoding="utf-8"))
    ids = [e["id"] for e in issues[0]["entries"]]
    assert issues[0]["page"] == page_name and len(ids) == 2
    # Один и тот же id должен дойти до страницы, кнопок Telegram и архива — по нему воркер принимает голос
    buttons = [b["callback_data"] for row in message["keyboard"]["inline_keyboard"] for b in row]
    for entry_id in ids:
        assert f'data-entry="{entry_id}"' in page
        assert f"u:{entry_id}" in buttons and f"d:{entry_id}" in buttons

    seen = json.loads((args.state_dir / "seen.json").read_text(encoding="utf-8"))
    assert any("openai.com/news/model" in key for key in seen["seen"])
    assert (args.state_dir / "stories.json").exists()


def test_also_today_gets_decoded_links_and_reaches_page_and_message(env, monkeypatch):
    tmp, args = env
    decoded = []

    def resolve(items):
        # Google News без JS не открывается: строка «Ещё за день» должна вести на саму статью
        for item in items:
            if "news.google.com" in item.url:
                item.url = "https://www.reuters.com/grocery"
                decoded.append(item.title)

    monkeypatch.setattr(main, "resolve_gnews", resolve)
    main.build(args)
    assert decoded == ["Ransomware takes a grocery chain offline"]
    page = (args.site / "index.html").read_text(encoding="utf-8")
    message = json.loads((tmp / "out" / "message.json").read_text(encoding="utf-8"))
    for text in (page, message["text"]):
        assert "Сеть магазинов легла после атаки" in text and "https://www.reuters.com/grocery" in text
    assert "Ransomware takes a grocery chain" not in page[page.index('id="noise"'):]


def test_second_run_does_not_show_seen_news_to_the_model(env, monkeypatch):
    tmp, args = env
    main.build(args)
    fresh = Item(source="ars_ai", title="Apple ships an on-device model", url="https://arstechnica.com/apple",
                 category="news", published=datetime.now().astimezone())
    monkeypatch.setattr(main, "fetch_all", lambda sources, now: ([*raw_items(now), fresh], []))
    prompts = []

    def recording(prompt, schema, model, api_key):
        prompts.append(prompt)
        return fake_generate()(prompt, schema, model, api_key)

    monkeypatch.setattr(llm, "generate", recording)
    main.build(args)
    # Сюжет «Nvidia» законно остаётся в блоке активных сюжетов — проверяем только список записей
    listed = prompts[0].split("### Записи", 1)[1].split("### Перед ответом", 1)[0]
    assert "Apple ships" in listed
    assert "OpenAI releases" not in listed and "Nvidia" not in listed


def _forbid_model(monkeypatch):
    def unexpected(prompt, schema, model, api_key):
        raise AssertionError("модель не должна вызываться без записей")
    monkeypatch.setattr(llm, "generate", unexpected)


def test_all_sources_failed_aborts_before_the_model(env, monkeypatch):
    tmp, args = env
    # Сеть раннера легла: без проверки ушёл бы пустой выпуск, а провал увидели бы только по пустому сообщению
    monkeypatch.setattr(main, "fetch_all", lambda sources, now: ([], ["x: ConnectError", "dead: ConnectError"]))
    _forbid_model(monkeypatch)
    with pytest.raises(SystemExit, match="ни один источник"):
        main.build(args)
    assert not args.state_dir.exists() and not (tmp / "out" / "message.json").exists()


def test_rerun_with_nothing_new_keeps_the_published_issue(env, monkeypatch):
    tmp, args = env
    main.build(args)
    message = json.loads((tmp / "out" / "message.json").read_text(encoding="utf-8"))
    page = args.site / message["url"].rsplit("/", 1)[1]
    published = page.read_text(encoding="utf-8")
    _forbid_model(monkeypatch)
    with pytest.raises(SystemExit, match="новых записей нет"):
        main.build(args)
    # Повтор того же выпуска не должен затирать вышедшую страницу пустой
    assert page.read_text(encoding="utf-8") == published


def test_model_choosing_nothing_aborts_instead_of_shipping_an_empty_issue(env, monkeypatch):
    tmp, args = env
    monkeypatch.setattr(llm, "generate", lambda prompt, schema, model, api_key: {"headline": "", "lead": "", "items": []})
    with pytest.raises(SystemExit, match="ни одного события"):
        main.build(args)
    assert not args.state_dir.exists() and not (tmp / "out" / "message.json").exists()


def test_failed_explain_still_ships_the_issue(env, monkeypatch):
    tmp, args = env
    monkeypatch.setattr(llm, "generate", fake_generate(explain_fails=True))
    main.build(args)
    assert (tmp / "out" / "message.json").exists()
    assert (args.state_dir / "seen.json").exists()


def test_failed_extras_still_ship_the_issue(env, monkeypatch):
    tmp, args = env
    monkeypatch.setattr(llm, "generate", fake_generate(extras_fail=True))
    main.build(args)
    message = json.loads((tmp / "out" / "message.json").read_text(encoding="utf-8"))
    assert "Nvidia купила стартап" in message["text"] and "Сеть магазинов" not in message["text"]
    assert (args.state_dir / "seen.json").exists()


def test_issue_is_three_calls_and_extras_skip_chosen_records(env, monkeypatch):
    tmp, args = env
    calls = []

    def recording(prompt, schema, model, api_key):
        calls.append((schema, prompt))
        return fake_generate()(prompt, schema, model, api_key)

    monkeypatch.setattr(llm, "generate", recording)
    main.build(args)
    assert [schema for schema, _ in calls] == [llm.SCHEMA, llm.EXTRAS_SCHEMA, llm.EXPLAIN_SCHEMA]
    listed = calls[1][1].split("### Записи", 1)[1]
    assert "Ransomware takes a grocery chain" in listed and "Nvidia" not in listed


def test_rate_limited_selection_falls_back_to_the_lite_model(env, monkeypatch):
    # Дневной лимит Flash кончился: выпуск похуже лучше, чем никакого
    tmp, args = env
    models = []

    def limited(prompt, schema, model, api_key):
        models.append(model)
        if model == "test-model":
            raise llm.LlmError("Gemini test-model ответил 429", status=429)
        return fake_generate()(prompt, schema, model, api_key)

    monkeypatch.setattr(llm, "generate", limited)
    main.build(args)
    assert (tmp / "out" / "message.json").exists()
    # Квота Flash уже исчерпана — второй вызов и разборы этого выпуска тоже идут на Lite
    assert models == ["test-model", "lite-model", "lite-model", "lite-model"]


def test_failed_selection_aborts_and_keeps_news_unseen(env, monkeypatch):
    tmp, args = env

    def broken(prompt, schema, model, api_key):
        raise llm.LlmError("Gemini 500")

    monkeypatch.setattr(llm, "generate", broken)
    with pytest.raises(llm.LlmError):
        main.build(args)
    assert not (args.state_dir / "seen.json").exists()
    assert not (tmp / "out" / "message.json").exists()


def test_dry_run_does_not_touch_state(env):
    tmp, args = env
    args.dry_run = True
    main.build(args)
    assert (tmp / "out" / "message.json").exists()
    assert not args.state_dir.exists()


def _alert_env(env, monkeypatch, posts):
    tmp, args = env
    monkeypatch.setattr(main, "fetch_all", lambda sources, now: (posts, []))
    monkeypatch.setattr(main, "fetch_articles", lambda urls: {u: f"article {u}" for u in urls})
    config = args.config.read_text(encoding="utf-8").replace('name = "x"', 'name = "x"\nalert = true')
    args.config.write_text(config, encoding="utf-8")
    args.state_dir.mkdir()
    return args


def _posts():
    now = datetime.now().astimezone()
    return [Item(source="openai", title="OpenAI ships GPT-6", url="https://openai.com/gpt-6", category="lab",
                 published=now),
            Item(source="openai", title="OpenAI updates its blog", url="https://openai.com/blog-update", category="lab",
                 published=now)]


def test_alert_lite_triage_rejects_without_spending_flash(env, monkeypatch):
    args = _alert_env(env, monkeypatch, _posts())
    calls = []

    def judge(prompt, schema, model, api_key):
        calls.append((model, prompt))
        return {"items": [{"index": 0, "urgent": False}, {"index": 1, "urgent": False}]}

    monkeypatch.setattr(main, "generate", judge)
    main.alert(args)
    # Без текста статьи у модели только заголовок, и «что это меняет» она додумывает
    assert [model for model, _ in calls] == ["lite-model"] and "article https://openai.com/gpt-6" in calls[0][1]
    state = json.loads((args.state_dir / "alerts.json").read_text(encoding="utf-8"))
    assert len(state["rejected"]) == 2


def test_alert_flash_rechecks_only_what_lite_flagged_and_may_refuse(env, monkeypatch):
    args = _alert_env(env, monkeypatch, _posts())
    calls = []
    sent = []

    def judge(prompt, schema, model, api_key):
        calls.append((model, prompt))
        if model == "lite-model":
            return {"items": [{"index": 0, "urgent": True}, {"index": 1, "urgent": False}]}
        return {"items": [{"index": 0, "urgent": False, "title": "", "text": ""}]}

    monkeypatch.setattr(main, "generate", judge)
    monkeypatch.setattr(main, "send", lambda *a, **kw: sent.append(a))
    main.alert(args)
    assert [model for model, _ in calls] == ["lite-model", "test-model"]
    assert "OpenAI ships GPT-6" in calls[1][1] and "OpenAI updates its blog" not in calls[1][1]
    assert sent == []
    state = json.loads((args.state_dir / "alerts.json").read_text(encoding="utf-8"))
    assert len(state["rejected"]) == 2 and state["sent"] == []


def test_alert_sends_what_flash_confirmed(env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    args = _alert_env(env, monkeypatch, _posts())
    sent = []

    def judge(prompt, schema, model, api_key):
        if model == "lite-model":
            return {"items": [{"index": 0, "urgent": True}, {"index": 1, "urgent": True}]}
        return {"items": [{"index": 1, "urgent": True, "title": "Блог обновили", "text": "Текст."}]}

    monkeypatch.setattr(main, "generate", judge)
    monkeypatch.setattr(main, "send", lambda text, url, *a, **kw: sent.append(url))
    main.alert(args)
    assert sent == ["https://openai.com/blog-update"]


def test_eval_runs_see_the_same_input_and_never_rewrite_the_snapshot(tmp_path, monkeypatch):
    # Отбор склеивает дубли прямо в объектах записей; без копии второй прогон видел уже склеенное,
    # а сохранение текстов записывало склейку в замороженный срез
    from digest import evaluate
    now = datetime(2026, 10, 20, 3).astimezone()
    snap = tmp_path / "snap.json"
    evaluate.save_snapshot(snap, raw_items(now)[1:3], now)
    pristine = snap.read_text(encoding="utf-8")
    config = tmp_path / "sources.toml"
    config.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(main, "OUT", tmp_path / "out")
    monkeypatch.setattr(main, "fetch_articles", lambda urls: {u: "" for u in urls})
    prompts = []

    def grouping(prompt, schema, model, api_key):
        prompts.append((schema, prompt))
        if schema is llm.SCHEMA:
            return {"headline": "", "lead": "", "items": [{"ids": [0, 1], "section": "business", "importance": 7,
                                                           "title": "Nvidia", "summary": "", "why": ""}]}
        return {"items": []}

    monkeypatch.setattr(llm, "generate", grouping)
    main.run_eval(argparse.Namespace(config=config, snapshots=[snap], runs=2))
    for schema in (llm.SCHEMA, llm.EXTRAS_SCHEMA):
        runs = [p for s, p in prompts if s is schema]
        assert len(runs) == 2 and runs[0] == runs[1]
    saved = json.loads(snap.read_text(encoding="utf-8"))
    assert saved["items"] == json.loads(pristine)["items"]
