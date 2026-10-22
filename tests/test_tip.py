import json
from datetime import datetime, timezone

from digest.tip import build_tip_message, due_tips, run, tip_hours

# Архив — новые выпуски сверху, как в state/issues.json
ISSUES = [
    {"date": "2026-10-22", "slot": "evening", "page": "2026-10-22-evening.html", "entries": [
        {"id": "20261022-15", "title": "Вечерняя фишка", "section": "tips", "summary": "s", "why": "w", "copy": ""}]},
    {"date": "2026-10-22", "slot": "morning", "page": "2026-10-22.html", "entries": [
        {"id": "20261022-0", "title": "Событие", "section": "security"},
        {"id": "20261022-12", "title": "Песочница <Claude Code>", "section": "tips",
         "summary": "Добавь параметр в настройки.", "why": "Запустит Chromium в песочнице.",
         "copy": "sandbox.allowNestedUserNamespaces", "topic": "claude_code"},
        {"id": "20261022-13", "title": "Вторая фишка", "section": "tips", "summary": "s", "why": "w", "copy": ""},
        {"id": "20261022-14", "title": "Третья фишка", "section": "tips", "summary": "s", "why": "w", "copy": ""}]},
    {"date": "2026-10-21", "slot": "evening", "page": "2026-10-21-evening.html", "entries": [
        {"id": "20261021-22", "title": "Вчерашняя фишка", "section": "tips", "summary": "s", "why": "w", "copy": ""}]},
]
SITE = "https://x.github.io/ai-digest/"


def tashkent(hour: int, day: int = 22) -> datetime:
    return datetime(2026, 10, day, hour - 5, 17, tzinfo=timezone.utc)


def test_morning_tips_spread_over_the_day_and_evening_tips_over_the_evening():
    hours = tip_hours(ISSUES[1])
    morning = [hours[i] for i in ("20261022-12", "20261022-13", "20261022-14")]
    # Каждая фишка — в свой отрезок окна 10–18: пачкой они не придут
    assert all(10 <= h < 18 for h in morning) and morning == sorted(morning) and len(set(morning)) == 3
    assert 19 <= tip_hours(ISSUES[0])["20261022-15"] < 23
    # Час случайный, но стабильный: часовые запуски должны сходиться, ничего не храня
    assert tip_hours(ISSUES[1]) == hours


def test_hours_differ_from_day_to_day():
    days = [{**ISSUES[1], "date": f"2026-10-{d:02d}"} for d in range(1, 15)]
    assert len({tuple(tip_hours(issue).values()) for issue in days}) > 3


def test_only_fresh_tips_whose_hour_came_are_due_in_order():
    hours = tip_hours(ISSUES[1])
    first, second = hours["20261022-12"], hours["20261022-13"]
    if first > 10:
        assert due_tips(ISSUES, tashkent(first - 1), []) == []
    due = due_tips(ISSUES, tashkent(second), [])
    assert [e["id"] for _, e in due][:2] == ["20261022-12", "20261022-13"]
    assert [e["id"] for _, e in due_tips(ISSUES, tashkent(second), ["20261022-12"])][0] == "20261022-13"
    # Вчерашняя фишка не приходит, и в тихие часы — ничего
    assert all(e["id"] != "20261021-22" for _, e in due_tips(ISSUES, tashkent(22), []))
    assert due_tips(ISSUES, tashkent(23), []) == []


def test_run_sends_one_tip_per_hourly_run_and_remembers_it(tmp_path):
    (tmp_path / "issues.json").write_text(json.dumps(ISSUES, ensure_ascii=False), encoding="utf-8")
    sent: list[str] = []

    def send(text: str, url: str) -> None:
        sent.append(url.rsplit("#e-", 1)[1])

    # Запуски сорвались весь день — к 22:00 созрели все четыре, но за раз уходит одна: пачкой не шлём
    assert run(tmp_path, tashkent(22), SITE, send)
    assert sent == ["20261022-12"]
    assert run(tmp_path, tashkent(22).replace(minute=59), SITE, send)
    assert sent == ["20261022-12", "20261022-13"]
    state = json.loads((tmp_path / "tip.json").read_text())
    assert state == {"day": "2026-10-22", "sent": ["20261022-12", "20261022-13"]}
    # Новый день — список отправленных начинается заново
    assert not run(tmp_path, tashkent(9, day=23), SITE, send)


def test_run_without_fresh_tips_sends_nothing(tmp_path):
    (tmp_path / "issues.json").write_text(json.dumps(ISSUES[2:], ensure_ascii=False), encoding="utf-8")
    assert not run(tmp_path, tashkent(22), SITE, lambda text, url: None)
    assert not (tmp_path / "tip.json").exists()


def test_tip_message_is_escaped_and_links_the_card_and_the_arsenal():
    text = build_tip_message(ISSUES[1], ISSUES[1]["entries"][1], SITE)
    assert text.startswith("💡 <b>Фишка</b>")
    assert "Песочница &lt;Claude Code&gt;" in text and "<Claude Code>" not in text
    assert "Добавь параметр в настройки." in text and "Запустит Chromium в песочнице." in text
    assert "<code>sandbox.allowNestedUserNamespaces</code>" in text
    assert '<a href="https://x.github.io/ai-digest/2026-10-22.html#e-20261022-12">' in text
    assert '<a href="https://x.github.io/ai-digest/tips.html">' in text


def test_oversized_tip_still_fits_telegram_and_keeps_the_card_link():
    from digest.telegram import TG_LIMIT, _visible
    # Ревью Codex: длинный промпт в «copy» дал бы отказ Telegram, и фишка повторялась бы каждый час
    long = {**ISSUES[1]["entries"][1], "copy": "промпт " * 1500, "summary": "суть " * 500, "why": "польза " * 500}
    text = build_tip_message(ISSUES[1], long, SITE)
    assert _visible(text) <= TG_LIMIT
    assert "<code>" not in text and "#e-20261022-12" in text and "Карточка в выпуске" in text


def test_tip_message_lists_the_steps_from_the_breakdown():
    tip = {**ISSUES[1]["entries"][2], "steps": ["Открой <settings.json>", "Добавь хук PreToolUse"]}
    text = build_tip_message(ISSUES[1], tip, SITE)
    assert "<b>Подробности:</b>\n1. Открой &lt;settings.json&gt;\n2. Добавь хук PreToolUse" in text
    # Старые фишки в архиве без шагов — без блока
    assert "Подробности" not in build_tip_message(ISSUES[1], ISSUES[1]["entries"][3], SITE)
