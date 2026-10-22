from datetime import date, datetime, timezone

from digest.tip import TIP_FROM, TIP_TO, build_tip_message, due_tip, pick_tip, tip_hour

# Архив — новые выпуски сверху, как в state/issues.json
ISSUES = [
    {"date": "2026-10-22", "slot": "evening", "page": "2026-10-22-evening.html", "entries": [
        {"id": "20261022-15", "title": "Вечерняя фишка", "section": "tips", "summary": "s", "why": "w", "copy": ""}]},
    {"date": "2026-10-22", "slot": "morning", "page": "2026-10-22.html", "entries": [
        {"id": "20261022-0", "title": "Событие", "section": "security"},
        {"id": "20261022-12", "title": "Песочница <Claude Code>", "section": "tips",
         "summary": "Добавь параметр в настройки.", "why": "Запустит Chromium в песочнице.",
         "copy": "sandbox.allowNestedUserNamespaces", "topic": "claude_code"},
        {"id": "20261022-13", "title": "Вторая фишка", "section": "tips", "summary": "s", "why": "w", "copy": ""}]},
    {"date": "2026-10-21", "slot": "evening", "page": "2026-10-21-evening.html", "entries": [
        {"id": "20261021-22", "title": "Вчерашняя фишка", "section": "tips", "summary": "s", "why": "w", "copy": ""}]},
]


def tashkent(hour: int, day: int = 22) -> datetime:
    return datetime(2026, 10, day, hour - 5, 7, tzinfo=timezone.utc)


def test_tip_hour_is_random_per_day_but_stable_and_inside_the_daytime_window():
    hours = [tip_hour(date(2026, 10, d)) for d in range(1, 29)]
    assert all(TIP_FROM <= h < TIP_TO for h in hours) and len(set(hours)) > 3
    assert tip_hour(date(2026, 10, 22)) == tip_hour(date(2026, 10, 22))


def test_tip_of_the_day_is_the_first_fresh_tip_of_the_morning_issue():
    issue, entry = pick_tip(ISSUES, date(2026, 10, 22))
    assert entry["id"] == "20261022-12" and issue["page"] == "2026-10-22.html"


def test_no_fresh_tip_means_no_tip_of_the_day():
    # «Только свежие»: вчерашняя фишка не подменяет сегодняшнюю
    assert pick_tip(ISSUES[2:], date(2026, 10, 22)) is None


def test_tip_is_due_once_a_day_from_its_hour_until_quiet_hours():
    day = date(2026, 10, 22)
    hour = tip_hour(day)
    assert not due_tip(tashkent(hour - 1), {})
    assert due_tip(tashkent(hour), {})
    # Запуск в нужный час сорвался — уходит в следующий, но не в тихие часы
    assert due_tip(tashkent(22), {}) and not due_tip(tashkent(23), {})
    assert not due_tip(tashkent(hour), {"day": "2026-10-22"})
    assert due_tip(tashkent(hour), {"day": "2026-10-21"})


def test_tip_message_is_escaped_and_links_the_card_and_the_arsenal():
    issue, entry = pick_tip(ISSUES, date(2026, 10, 22))
    text = build_tip_message(issue, entry, "https://x.github.io/ai-digest/")
    assert text.startswith("💡 <b>Фишка дня</b>")
    assert "Песочница &lt;Claude Code&gt;" in text and "<Claude Code>" not in text
    assert "Добавь параметр в настройки." in text and "Запустит Chromium в песочнице." in text
    assert "<code>sandbox.allowNestedUserNamespaces</code>" in text
    assert '<a href="https://x.github.io/ai-digest/2026-10-22.html#e-20261022-12">' in text
    assert '<a href="https://x.github.io/ai-digest/tips.html">' in text


def test_tip_run_sends_once_and_remembers_the_day(tmp_path):
    import json

    from digest.tip import run
    (tmp_path / "issues.json").write_text(json.dumps(ISSUES, ensure_ascii=False), encoding="utf-8")
    sent = []
    hour = tip_hour(date(2026, 10, 22))
    assert not run(tmp_path, tashkent(hour - 1), "https://x.io", lambda text, url: sent.append(url))
    assert run(tmp_path, tashkent(hour), "https://x.io", lambda text, url: sent.append(url))
    # Следующий часовой запуск того же дня фишку не повторяет
    assert not run(tmp_path, tashkent(hour + 1), "https://x.io", lambda text, url: sent.append(url))
    assert sent == ["https://x.io/2026-10-22.html#e-20261022-12"]
    assert json.loads((tmp_path / "tip.json").read_text()) == {"day": "2026-10-22", "id": "20261022-12"}


def test_tip_run_waits_for_the_evening_issue_when_the_morning_had_no_tips(tmp_path):
    import json

    from digest.tip import run
    morning = {**ISSUES[1], "entries": ISSUES[1]["entries"][:1]}
    (tmp_path / "issues.json").write_text(json.dumps([morning], ensure_ascii=False), encoding="utf-8")
    hour = tip_hour(date(2026, 10, 22))
    assert not run(tmp_path, tashkent(hour), "https://x.io", lambda text, url: None)
    # Ничего не отправили — день не помечен, и вечерняя фишка ещё сможет уйти
    assert not (tmp_path / "tip.json").exists()


def test_oversized_tip_still_fits_telegram_and_keeps_the_card_link():
    from digest.telegram import TG_LIMIT, _visible
    issue, entry = pick_tip(ISSUES, date(2026, 10, 22))
    # Ревью Codex: длинный промпт в «copy» дал бы отказ Telegram, и фишка не ушла бы весь день
    long = {**entry, "copy": "промпт " * 1500, "summary": "суть " * 500, "why": "польза " * 500}
    text = build_tip_message(issue, long, "https://x.io")
    assert _visible(text) <= TG_LIMIT
    assert "<code>" not in text and "#e-20261022-12" in text and "Карточка в выпуске" in text
