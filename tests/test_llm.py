from digest.llm import build_prompt, parse_selection
from digest.models import Item


def items():
    return [
        Item(source="hn", title="Meta launches open-weight model", url="https://a", category="community", score=300),
        Item(source="verge", title="Meta open model runs on Macs", url="https://b", category="news"),
        Item(source="gh_ollama", title="Ollama v0.40.5", url="https://c", category="release"),
        Item(source="techcrunch", title="Save 50% on Disrupt tickets", url="https://d", category="news"),
    ]


def test_prompt_numbers_every_item_and_includes_audience():
    prompt = build_prompt(items(), audience="Python-разработчик")
    assert "Python-разработчик" in prompt
    for n, title in enumerate(i.title for i in items()):
        assert f"[{n}]" in prompt and title in prompt


def test_selection_merges_ids_sorts_and_collects_noise():
    raw = {
        "headline": "Открытые веса на Mac",
        "lead": "Главное за день.",
        "items": [
            {"ids": [2], "section": "release", "importance": 6, "title": "Ollama 0.40.5", "summary": "Патч.", "why": ""},
            {"ids": [0, 1], "section": "industry", "importance": 9, "title": "Meta открыла веса",
             "summary": "Модель работает на Mac.", "why": "Можно запустить локально."},
        ],
    }
    digest = parse_selection(raw, items())
    assert [e.title for e in digest.entries] == ["Meta открыла веса", "Ollama 0.40.5"]
    top = digest.entries[0]
    assert top.item.url == "https://a"
    assert [s for s, _ in top.item.also] == ["verge"]
    assert [i.url for i in digest.noise] == ["https://d"]


def test_selection_ignores_unknown_ids_and_entries_without_valid_ids():
    raw = {"headline": "h", "lead": "l", "items": [
        {"ids": [99], "section": "paper", "importance": 8, "title": "x", "summary": "s", "why": ""},
        {"ids": [2, 42], "section": "release", "importance": 5, "title": "Ollama", "summary": "s", "why": ""},
    ]}
    digest = parse_selection(raw, items())
    assert [e.title for e in digest.entries] == ["Ollama"]
    assert digest.entries[0].item.url == "https://c"


def test_explanations_attach_to_entries_by_position_and_skip_unknown():
    from digest.llm import Entry, attach_explanations
    entries = [Entry(items()[0], "model", 9, "A", "s", ""), Entry(items()[2], "release", 6, "B", "s", "")]
    raw = {"items": [
        {"id": 1, "what": "Вышел Ollama.", "points": ["Новая версия"], "try_it": "ollama pull x", "caveats": ""},
        {"id": 7, "what": "мусор", "points": [], "try_it": "", "caveats": ""},
    ]}
    attach_explanations(entries, raw)
    assert entries[0].details is None
    assert entries[1].details.what == "Вышел Ollama."
    assert entries[1].details.points == ["Новая версия"]


def test_transport_errors_become_llm_errors(monkeypatch):
    import httpx
    import pytest
    from digest import llm

    def boom(*args, **kwargs):
        raise httpx.ConnectTimeout("timed out")
    monkeypatch.setattr(llm.httpx, "post", boom)
    monkeypatch.setattr(llm, "RETRY_DELAYS", ())
    with pytest.raises(llm.LlmError):
        llm.explain([], {}, "a", "m", "k")


def test_http_client_logs_do_not_expose_request_urls():
    import logging
    from digest.__main__ import configure_logging
    configure_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_prompt_carries_preferences_and_active_stories():
    stories = [{"id": "s4", "name": "Nemotron 3.5", "last_date": "2026-10-15", "last_title": "Вышел", "days": 1}]
    prompt = build_prompt(items(), audience="dev", preferences="Зашло:\n- Llama 5", stories=stories)
    assert "Зашло:\n- Llama 5" in prompt
    assert "[s4] Nemotron 3.5" in prompt


def test_selection_keeps_story_links():
    raw = {"headline": "h", "lead": "l", "items": [
        {"ids": [2], "section": "release", "importance": 6, "title": "Ollama", "summary": "s", "why": "",
         "story": "s4", "story_name": ""},
    ]}
    entry = parse_selection(raw, items()).entries[0]
    assert entry.story == "s4"
