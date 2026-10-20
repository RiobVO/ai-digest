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
    ]


SELECTION = {"headline": "День <чипов>", "lead": "Главное.", "items": [
    {"ids": [0], "section": "model", "importance": 8.5, "title": "OpenAI выкатила модель", "summary": "Факты.",
     "why": "Меняет рынок.", "story": "", "story_name": "Новая модель"},
    {"ids": [1, 2], "section": "business", "importance": 7, "title": "Nvidia купила стартап", "summary": "Сделка.",
     "why": "Консолидация.", "story": "", "story_name": ""},
]}


def fake_generate(explain_fails: bool = False):
    def generate(prompt, schema, model, api_key):
        if schema is llm.SCHEMA:
            return SELECTION
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
    listed = prompts[0].rsplit("Записи:", 1)[1]
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
