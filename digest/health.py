"""Здоровье запусков: каждый вызов Gemini, расход квоты по моделям и сбои источников — для страницы site/health.html.

Сборщик RUN живёт в памяти процесса: llm.generate и fetch.fetch_all пишут в него по ходу запуска, а __main__ в конце
(и при падении тоже) сохраняет запуск в state/health/<команда>.json. У digest.yml и alert.yml свои файлы: они
пушат параллельно, и общий файл конфликтовал бы при pull --rebase."""
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

RETENTION_DAYS = 30
# Квота бесплатного ключа сбрасывается в полночь по Калифорнии: 12:00 по Ташкенту летом, 13:00 зимой
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
# Дневные лимиты бесплатного ключа по ролям моделей из sources.toml (проверено по AI Studio 21.10.2026)
DAILY_LIMITS = {"model": 20, "extras_model": 20, "lite_model": 500}
BROKEN_ANSWER = "ответ не разобран"


@dataclass
class Run:
    calls: list[dict] = field(default_factory=list)
    sources: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)
    slot: str = ""


RUN = Run()


def reset() -> None:
    global RUN
    RUN = Run()


def _stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_call(asked: str, served: str, status: int, seconds: float, thinking: int, error: str = "",
                started: datetime | None = None) -> None:
    """Одна попытка одной модели. error — только имя исключения или своя короткая причина: текст ответа Gemini
    сюда не попадает, страница публичная. started — начало запроса: квота списывается по нему, а таймаут
    в 6 минут может закончиться уже в следующих квотных сутках."""
    at = started or datetime.now(timezone.utc)
    RUN.calls.append({"at": _stamp(at), "call": "", "asked": asked, "served": served,
                      "status": status, "seconds": round(seconds, 1), "thinking": thinking, "error": error})


def label_calls(since: int, call: str) -> None:
    """Имя вызова (отбор, разборы…) знает generate_first, а попытки пишет generate — подписываем их задним числом."""
    for record in RUN.calls[since:]:
        record["call"] = call


def record_source(name: str, count: int, error: str = "") -> None:
    RUN.sources[name] = count
    if error:
        RUN.failed[name] = error


def record_events(sources: list[str]) -> None:
    RUN.events = dict(Counter(sources).most_common())


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def load_all(folder: Path, now: datetime) -> list[dict]:
    # Файл обрезается только своей командой: итог недели пишется раз в неделю, и без отсечки здесь его старая
    # запись жила бы на странице до восьми дней сверх срока
    cutoff = _stamp(now - timedelta(days=RETENTION_DAYS))
    runs = [run for path in sorted(folder.glob("*.json")) for run in load(path) if run["at"] >= cutoff]
    return sorted(runs, key=lambda r: r["at"])


def save(path: Path, kind: str, now: datetime, outcome: str, keep_sources: bool = True) -> None:
    run = {"at": _stamp(now), "kind": kind, "slot": RUN.slot, "outcome": outcome, "calls": RUN.calls,
           # Срочные проверяются каждый час: счётчики всех источников раздули бы файл, сбои — нет
           "failed": RUN.failed, "sources": RUN.sources if keep_sources else {}, "events": RUN.events}
    cutoff = _stamp(now - timedelta(days=RETENTION_DAYS))
    runs = [r for r in load(path) if r["at"] >= cutoff] + [run]
    path.parent.mkdir(parents=True, exist_ok=True)
    # Запуск на строку: дифф коммита бота показывает ровно добавленный запуск
    lines = ",\n".join(json.dumps(r, ensure_ascii=False) for r in runs)
    path.write_text(f"[\n{lines}\n]\n", encoding="utf-8")


def quota_day_start(now: datetime) -> datetime:
    local = now.astimezone(QUOTA_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def quota_day_end(now: datetime) -> datetime:
    """Следующая полночь по Калифорнии: в день перевода часов сутки длиннее или короче 24 часов."""
    day = now.astimezone(QUOTA_TZ).date() + timedelta(days=1)
    return datetime.combine(day, time(0), tzinfo=QUOTA_TZ).astimezone(timezone.utc)


def quota_usage(runs: list[dict], since: datetime) -> dict[str, dict]:
    """Попытки по запрошенной модели с начала квотных суток. 503 и таймауты считаются: AI Studio их списывает."""
    start = _stamp(since)
    usage: dict[str, dict] = {}
    for run in runs:
        for call in run.get("calls", []):
            if call["at"] < start:
                continue
            row = usage.setdefault(call["asked"], {"used": 0, "failed": 0, "served": ""})
            row["used"] += 1
            if call["status"] != 200 or call["error"]:
                row["failed"] += 1
            row["served"] = call["served"] or row["served"]
    return usage
