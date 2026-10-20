"""Память между выпусками: архив, сюжетные линии и оценки читателя. Всё хранится JSON-файлами в state/."""
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from textwrap import shorten

import httpx

from digest.llm import Entry
from digest.render import DISPLAY_TZ

log = logging.getLogger(__name__)

STORY_ACTIVE_DAYS = 7
STORY_RETENTION_DAYS = 30
STORY_PREV_SHOWN = 3
STORY_NAME_MAX = 60
FEEDBACK_IN_PROMPT = 20
MISSED_SHOWN = 3
# Сборка до этого часа по Ташкенту — утренний выпуск, после — вечерний
EVENING_FROM_HOUR = 13


SLOTS = ("morning", "evening")


def issue_slot(now: datetime, forced: str = "") -> str:
    """Выпуск задаёт планировщик по запланированному времени; по часам сборки — только при ручном запуске."""
    if forced:
        if forced not in SLOTS:
            raise ValueError(f"неизвестный выпуск {forced!r}, ожидается одно из {SLOTS}")
        return forced
    return "evening" if now.astimezone(DISPLAY_TZ).hour >= EVENING_FROM_HOUR else "morning"


def _issue_of(event: dict) -> str:
    # События до появления вечерних выпусков не хранят страницу — тогда это утренний выпуск своего дня
    return event.get("issue") or f"{event['date']}.html"


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


class Archive:
    """Список выпусков (новые сверху); через него оценки по id превращаются обратно в заголовки."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.issues: list[dict] = _load(self.path, [])

    def next_index(self, day: date) -> int:
        """Номера записей сквозные на весь день (утро и вечер) и не переиспользуются:
        голос за старую запись не должен перейти к новой."""
        used = [int(e["id"].rsplit("-", 1)[1]) for i in self.issues if i["date"] == day.isoformat()
                for e in i["entries"] + i.get("replaced", [])]
        return max(used, default=-1) + 1

    def add(self, day: date, headline: str, page: str, entries: list[Entry], slot: str = "morning") -> None:
        # Выпуск определяется страницей, а не датой: в один день их два — утренний и вечерний
        previous = next((i for i in self.issues if i["page"] == page), None)
        issue = {"date": day.isoformat(), "slot": slot, "headline": headline, "page": page,
                 "entries": [{"id": e.id, "title": e.title, "section": e.section, "source": e.item.source,
                              "url": e.item.url, "story": e.story} for e in entries]}
        if previous:
            # Пересборка заменяет выпуск, но записи прежней версии остаются для разбора старых голосов
            issue["replaced"] = previous.get("replaced", []) + previous["entries"]
        self.issues = [issue] + [i for i in self.issues if i["page"] != page]
        self.issues.sort(key=lambda i: (i["date"], i.get("slot") == "evening"), reverse=True)

    def entry(self, entry_id: str) -> dict | None:
        for issue in self.issues:
            for e in issue["entries"] + issue.get("replaced", []):
                if e["id"] == entry_id:
                    return e
        return None

    def save(self) -> None:
        _dump(self.path, self.issues)


class Stories:
    """Сюжетные линии: одно событие, растянутое на несколько дней (релиз → поддержка → бенчмарки)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        data = _load(self.path, {"next": 1, "stories": {}})
        self.next: int = data["next"]
        self.stories: dict[str, dict] = data["stories"]

    def active(self, today: date) -> list[dict]:
        cutoff = (today - timedelta(days=STORY_ACTIVE_DAYS)).isoformat()
        rows = []
        for sid, story in self.stories.items():
            last = story["events"][-1]
            if last["date"] >= cutoff:
                rows.append({"id": sid, "name": story["name"], "last_date": last["date"], "last_title": last["title"],
                             "days": len({ev["date"] for ev in story["events"]})})
        return sorted(rows, key=lambda r: r["last_date"], reverse=True)

    def apply(self, entries: list[Entry], today: date, issue: str) -> None:
        day = today.isoformat()
        # Пересборка выпуска заменяет его события целиком, иначе в хронологии остаются новости заменённой версии;
        # события другого выпуска того же дня (утро/вечер) не трогаем
        for sid in list(self.stories):
            events = [ev for ev in self.stories[sid]["events"] if _issue_of(ev) != issue]
            if events:
                self.stories[sid]["events"] = events
            else:
                del self.stories[sid]
        for e in entries:
            if e.story not in self.stories:
                # Пустой или выдуманный моделью id — значит, сюжет новый
                e.story = f"s{self.next}"
                self.next += 1
                # Без имени от модели сюжет называется заголовком новости — режем по слову, а не посреди «млн»
                name = shorten(e.story_name or e.title, STORY_NAME_MAX, placeholder="…")
                self.stories[e.story] = {"name": name, "events": []}
            story = self.stories[e.story]
            e.story_name = story["name"]
            earlier = [ev for ev in story["events"] if _issue_of(ev) != issue]
            e.story_prev = [(ev["date"], ev["title"]) for ev in earlier[-STORY_PREV_SHOWN:]]
            story["events"].append({"date": day, "title": e.title, "entry": e.id, "issue": issue})
            e.story_day = len({ev["date"] for ev in story["events"]})

    def save(self, today: date | None = None) -> None:
        if today is not None:
            cutoff = (today - timedelta(days=STORY_RETENTION_DAYS)).isoformat()
            self.stories = {k: v for k, v in self.stories.items() if v["events"][-1]["date"] >= cutoff}
        _dump(self.path, {"next": self.next, "stories": self.stories})


def record_issue(archive: Archive, stories: Stories, day: date, headline: str, page: str, entries: list[Entry],
                 slot: str = "morning") -> None:
    """Порядок важен: id записей → сюжеты (им нужны id) → архив (ему нужен уже назначенный сюжет)."""
    start = archive.next_index(day)
    for n, e in enumerate(entries):
        e.id = f"{day:%Y%m%d}-{start + n}"
    stories.apply(entries, day, page)
    archive.add(day, headline, page, entries, slot)


def resolve_votes(votes: list[dict], archive: Archive) -> list[dict]:
    """Последняя оценка по каждой записи, с заголовком из архива; оценки неизвестных записей отбрасываются."""
    latest: dict[str, dict] = {}
    for vote in sorted(votes, key=lambda v: v["at"]):
        latest[vote["id"]] = vote
    resolved = []
    for entry_id, vote in latest.items():
        known = archive.entry(entry_id)
        if known:
            resolved.append({**known, "v": vote["v"], "at": vote["at"]})
    return sorted(resolved, key=lambda r: r["at"])


def preferences_prompt(feedback: list[dict]) -> str:
    recent = feedback[-FEEDBACK_IN_PROMPT:]
    liked = [f"- {r['title']} ({r['section']}, {r['source']})" for r in recent if r["v"] == "up"]
    disliked = [f"- {r['title']} ({r['section']}, {r['source']})" for r in recent if r["v"] == "down"]
    if not liked and not disliked:
        return ""
    # Один 👎 статье не должен выключать целую тему или топить крупное событие дня
    parts = ["### Оценки читателя по прошлым выпускам\nЭто подсказка, а не правило: учитывай их, когда выбираешь между "
             "похожими по значимости событиями. Не исключай из-за одной оценки целую тему и не снижай важность крупных "
             "событий."]
    if liked:
        parts.append("Зашло:\n" + "\n".join(liked))
    if disliked:
        parts.append("Не зашло:\n" + "\n".join(disliked))
    return "\n".join(parts)


def fetch_feedback(feedback_url: str, token: str) -> tuple[list[dict], set[str]] | None:
    """Оценки и отметки «прочитано» из воркера. Недоступный воркер не должен срывать выпуск — тогда идём без них.
    None — «неизвестно»: пустой ответ означал бы «ничего не прочитано», и вечер объявил бы пропущенным всё утро."""
    if not feedback_url or not token:
        return None
    try:
        response = httpx.get(f"{feedback_url.rstrip('/')}/votes", headers={"Authorization": f"Bearer {token}"}, timeout=20)
        response.raise_for_status()
        data = response.json()
        return list(data.get("votes", [])), {r["id"] for r in data.get("reads", [])}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.error("обратная связь из воркера не получена: %s", exc)
        return None


def missed_in_morning(archive: Archive, day: date, opened: set[str] | None) -> list[dict]:
    """Что из утреннего выпуска читатель не открыл и не оценил. Записи в выпуске уже идут по убыванию важности.
    opened=None — отметки неизвестны (воркер недоступен): тогда не утверждаем, что что-то пропущено."""
    morning = next((i for i in archive.issues if i["page"] == f"{day.isoformat()}.html"), None)
    if morning is None or opened is None:
        return []
    return [e for e in morning["entries"] if e["id"] not in opened][:MISSED_SHOWN]
