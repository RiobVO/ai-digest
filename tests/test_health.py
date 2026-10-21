import json
import re
from datetime import datetime, timezone

import httpx
import pytest

from digest import fetch, health, llm
from digest.render import render_health, source_label

SETTINGS = {"model": "gemini-flash-latest", "extras_model": "gemini-3.5-flash", "lite_model": "gemini-3.5-flash-lite"}
# Ключ, токен бота и URL с токеном: ни одно из этого не должно попасть ни в state/health, ни на страницу
SECRETS = re.compile(r"AIza|bot\d+:|key=|token|Bearer", re.I)


@pytest.fixture(autouse=True)
def fresh_run():
    health.reset()
    yield
    health.reset()


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def evening_2110() -> list[dict]:
    """Вечерний выпуск 21.10 по логу CI: 3.8 и 3.6 ответили 503, отбор и «Ещё за день» сделал Lite."""
    calls = [
        {"at": "2026-10-21T13:08:45Z", "call": "select", "asked": "gemini-flash-latest", "served": "", "status": 503,
         "seconds": 1.4, "thinking": 0, "error": ""},
        {"at": "2026-10-21T13:09:04Z", "call": "select", "asked": "gemini-3.5-flash", "served": "", "status": 503,
         "seconds": 19.0, "thinking": 0, "error": ""},
        {"at": "2026-10-21T13:09:11Z", "call": "select", "asked": "gemini-3.5-flash-lite",
         "served": "gemini-3.5-flash-lite", "status": 200, "seconds": 6.6, "thinking": 0, "error": ""},
        {"at": "2026-10-21T13:10:12Z", "call": "extras", "asked": "gemini-3.5-flash", "served": "", "status": 503,
         "seconds": 61.0, "thinking": 0, "error": ""},
        {"at": "2026-10-21T13:10:16Z", "call": "extras", "asked": "gemini-3.5-flash-lite",
         "served": "gemini-3.5-flash-lite", "status": 200, "seconds": 3.3, "thinking": 0, "error": ""},
        {"at": "2026-10-21T13:10:43Z", "call": "explain", "asked": "gemini-flash-latest", "served": "gemini-3.8-flash",
         "status": 200, "seconds": 20.0, "thinking": 1840, "error": ""},
    ]
    return [{"at": "2026-10-21T13:10:51Z", "kind": "build", "slot": "evening", "outcome": "ok", "calls": calls,
             "failed": {"bloomberg_tech": "HTTPStatusError"}, "sources": {"techmeme": 30, "bloomberg_tech": 0},
             "events": {"the_record": 3, "techmeme": 2}}]


def test_generate_records_every_attempt_without_the_error_text(monkeypatch):
    answers = iter([
        httpx.Response(503, text='{"error": "high demand", "key=AIzaFAKE"}'),
        httpx.Response(200, json={"modelVersion": "gemini-3.5-flash-lite",
                                  "usageMetadata": {"totalTokenCount": 900, "thoughtsTokenCount": 120},
                                  "candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}),
    ])
    monkeypatch.setattr(llm.httpx, "post", lambda url, **kw: next(answers))
    assert llm.generate_first("p", {}, ["gemini-flash-latest", "gemini-3.5-flash-lite"], "k", call="select") == {"ok": 1}
    calls = health.RUN.calls
    assert [(c["call"], c["asked"], c["served"], c["status"], c["thinking"]) for c in calls] == [
        ("select", "gemini-flash-latest", "", 503, 0), ("select", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite", 200, 120)]
    # Текст ответа Gemini не храним: в нём мог бы оказаться кусок запроса
    assert not SECRETS.search(json.dumps(calls))


def test_timeout_and_broken_answer_are_recorded_as_failed_attempts(monkeypatch):
    def post(url, **kw):
        if "/models/slow:" in url:
            raise httpx.ReadTimeout("queue")
        return httpx.Response(200, text="<html>oops</html>")
    monkeypatch.setattr(llm.httpx, "post", post)
    with pytest.raises(llm.LlmError):
        llm.generate_first("p", {}, ["slow", "broken"], "k", call="extras")
    assert [(c["asked"], c["status"], c["error"]) for c in health.RUN.calls] == [
        ("slow", 0, "ReadTimeout"), ("broken", 200, "ответ не разобран")]


def test_fetch_records_counts_and_failed_sources(monkeypatch):
    rss = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
           b'<item><title>Post</title><link>https://ok.io/post</link></item></channel></rss>')

    def handler(request):
        return httpx.Response(200, content=rss) if request.url.host == "ok.io" else httpx.Response(500)
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    sources = [{"name": "ok", "type": "rss", "category": "news", "url": "https://ok.io/feed"},
               {"name": "down", "type": "rss", "category": "news", "url": "https://down.io/feed?token=SECRET"}]
    fetch.fetch_all(sources, utc(2026, 10, 21))
    assert health.RUN.sources == {"ok": 1, "down": 0}
    assert health.RUN.failed == {"down": "HTTPStatusError"}


def test_saved_file_keeps_only_30_days(tmp_path):
    path = tmp_path / "health" / "build.json"
    health.record_call("m", "m", 200, 1.0, 0)
    health.save(path, "build", utc(2026, 9, 1, 3), "ok")
    health.reset()
    health.save(path, "build", utc(2026, 9, 30, 3), "ok")
    health.save(path, "build", utc(2026, 10, 2, 3), "сбой: LlmError")
    runs = health.load(path)
    # Запуск 1 сентября старше 30 дней от 2 октября — выпал; файл не растёт бесконечно
    assert [r["at"] for r in runs] == ["2026-09-30T03:00:00Z", "2026-10-02T03:00:00Z"]
    assert runs[-1]["outcome"] == "сбой: LlmError"


def test_alert_runs_keep_failures_but_not_the_counts_of_every_source(tmp_path):
    path = tmp_path / "alert.json"
    health.record_source("techmeme", 12)
    health.record_source("ft_tech", 0, "ConnectError")
    health.save(path, "alert", utc(2026, 10, 21, 10), "ok", keep_sources=False)
    run = health.load(path)[0]
    assert run["sources"] == {} and run["failed"] == {"ft_tech": "ConnectError"}


@pytest.mark.parametrize(("now", "start"), [
    # Летнее время Калифорнии: полночь там — 07:00 UTC, 12:00 в Ташкенте
    (utc(2026, 10, 21, 7, 30), utc(2026, 10, 21, 7)),
    (utc(2026, 10, 21, 6, 59), utc(2026, 10, 20, 7)),
    # С 1 ноября зимнее: полночь — 08:00 UTC, сброс квоты сдвигается на 13:00 по Ташкенту
    (utc(2026, 11, 2, 7, 30), utc(2026, 11, 1, 7)),
    (utc(2026, 11, 2, 8, 30), utc(2026, 11, 2, 8)),
])
def test_quota_day_starts_at_pacific_midnight(now, start):
    assert health.quota_day_start(now) == start


def test_quota_counts_every_attempt_of_the_current_quota_day():
    runs = evening_2110() + [{"at": "2026-10-20T13:10:00Z", "kind": "build", "outcome": "ok", "calls": [
        {"at": "2026-10-20T13:09:00Z", "call": "select", "asked": "gemini-flash-latest", "served": "gemini-3.8-flash",
         "status": 200, "seconds": 90, "thinking": 0, "error": ""}]}]
    usage = health.quota_usage(runs, utc(2026, 10, 21, 7))
    # 503 тоже съедает запрос: у 3.8 два вызова, оба считаются, вчерашний — нет
    assert usage["gemini-flash-latest"] == {"used": 2, "failed": 1, "served": "gemini-3.8-flash"}
    assert usage["gemini-3.5-flash"] == {"used": 2, "failed": 2, "served": ""}
    assert usage["gemini-3.5-flash-lite"]["used"] == 2


def test_health_page_shows_the_fallback_chain_and_no_secrets():
    page = render_health(evening_2110(), SETTINGS, utc(2026, 10, 21, 14))
    assert "21 октября" in page and "Отбор" in page
    # Цепочка читается слева направо: какую просили, что ответила, на какой модели закончилось
    start = page.index("<b>Отбор</b>")
    chain = page[start:page.index("<b>Ещё за день", start)]
    assert chain.index("503") < chain.index("3.5 Flash-Lite")
    # Неудачная попытка на алиасе не знает, какая модель за ним стояла: подписываем алиасом, не догадкой
    assert "Flash latest · 503" in chain and "3.8 Flash · 503" not in chain
    assert "3.8 Flash" in page and source_label("bloomberg_tech") in page
    assert "/20" in page and "/500" in page
    assert not SECRETS.search(page)


def test_health_page_without_runs_says_so_instead_of_inventing_data():
    page = render_health([], SETTINGS, utc(2026, 10, 21, 14))
    assert "Запусков пока нет" in page


def test_attempt_time_is_when_the_request_started():
    started = utc(2026, 10, 21, 6, 59, 30)
    health.record_call("m", "", 0, 120.0, 0, "ReadTimeout", started=started)
    # Таймаут закончился уже в новых квотных сутках, но списан запрос в старых
    assert health.RUN.calls[0]["at"] == "2026-10-21T06:59:30Z"


def test_reading_all_files_drops_runs_older_than_30_days(tmp_path):
    folder = tmp_path / "health"
    health.save(folder / "weekly.json", "weekly", utc(2026, 9, 20, 3), "ok")
    health.save(folder / "build.json", "build", utc(2026, 10, 21, 3), "ok")
    # Итог недели пишется раз в неделю: без обрезки при чтении его запись висела бы до следующего воскресенья
    assert [r["kind"] for r in health.load_all(folder, utc(2026, 10, 21, 3))] == ["build"]


@pytest.mark.parametrize(("now", "end"), [
    (utc(2026, 10, 21, 14), utc(2026, 10, 22, 7)),
    # 1 ноября — переход на зимнее время: следующая полночь в Калифорнии — 08:00 UTC, а не 07:00
    (utc(2026, 11, 1, 12), utc(2026, 11, 2, 8)),
])
def test_quota_day_ends_at_the_next_pacific_midnight(now, end):
    assert health.quota_day_end(now) == end


def test_health_page_lists_how_many_records_each_source_gave():
    runs = evening_2110()
    page = render_health(runs, SETTINGS, utc(2026, 10, 21, 14))
    assert "Techmeme 30" in page and "Записей из источников" in page
