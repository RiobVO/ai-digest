"""Память между выпусками: архив, сюжетные линии и оценки читателя. Всё хранится JSON-файлами в state/."""
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import httpx

from digest.llm import Entry

log = logging.getLogger(__name__)

STORY_ACTIVE_DAYS = 7
STORY_RETENTION_DAYS = 30
STORY_PREV_SHOWN = 3
FEEDBACK_IN_PROMPT = 20


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

    def _day(self, day: date) -> dict | None:
        return next((i for i in self.issues if i["date"] == day.isoformat()), None)

    def next_index(self, day: date) -> int:
        """Номера записей за день не переиспользуются: голос за старую запись не должен перейти к новой."""
        issue = self._day(day)
        if issue is None:
            return 0
        used = [int(e["id"].rsplit("-", 1)[1]) for e in issue["entries"] + issue.get("replaced", [])]
        return max(used, default=-1) + 1

    def add(self, day: date, headline: str, page: str, entries: list[Entry]) -> None:
        previous = self._day(day)
        issue = {"date": day.isoformat(), "headline": headline, "page": page,
                 "entries": [{"id": e.id, "title": e.title, "section": e.section, "source": e.item.source,
                              "url": e.item.url, "story": e.story} for e in entries]}
        if previous:
            # Повторный запуск в тот же день заменяет выпуск, но записи прежней версии остаются для разбора старых голосов
            issue["replaced"] = previous.get("replaced", []) + previous["entries"]
        self.issues = [issue] + [i for i in self.issues if i["date"] != issue["date"]]
        self.issues.sort(key=lambda i: i["date"], reverse=True)

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

    def apply(self, entries: list[Entry], today: date) -> None:
        day = today.isoformat()
        # Пересборка дня заменяет его события целиком, иначе в хронологии остаются новости заменённого выпуска
        for sid in list(self.stories):
            events = [ev for ev in self.stories[sid]["events"] if ev["date"] != day]
            if events:
                self.stories[sid]["events"] = events
            else:
                del self.stories[sid]
        for e in entries:
            if e.story not in self.stories:
                # Пустой или выдуманный моделью id — значит, сюжет новый
                e.story = f"s{self.next}"
                self.next += 1
                self.stories[e.story] = {"name": (e.story_name or e.title)[:60], "events": []}
            story = self.stories[e.story]
            e.story_name = story["name"]
            earlier = [ev for ev in story["events"] if ev["date"] < day]
            e.story_prev = [(ev["date"], ev["title"]) for ev in earlier[-STORY_PREV_SHOWN:]]
            story["events"].append({"date": day, "title": e.title, "entry": e.id})
            e.story_day = len({ev["date"] for ev in story["events"]})

    def save(self, today: date | None = None) -> None:
        if today is not None:
            cutoff = (today - timedelta(days=STORY_RETENTION_DAYS)).isoformat()
            self.stories = {k: v for k, v in self.stories.items() if v["events"][-1]["date"] >= cutoff}
        _dump(self.path, {"next": self.next, "stories": self.stories})


def record_issue(archive: Archive, stories: Stories, day: date, headline: str, page: str, entries: list[Entry]) -> None:
    """Порядок важен: id записей → сюжеты (им нужны id) → архив (ему нужен уже назначенный сюжет)."""
    start = archive.next_index(day)
    for n, e in enumerate(entries):
        e.id = f"{day:%Y%m%d}-{start + n}"
    stories.apply(entries, day)
    archive.add(day, headline, page, entries)


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
    parts = ["Оценки читателя по прошлым выпускам — учитывай их при отборе и оценке важности."]
    if liked:
        parts.append("Зашло:\n" + "\n".join(liked))
    if disliked:
        parts.append("Не зашло:\n" + "\n".join(disliked))
    return "\n".join(parts)


def fetch_votes(feedback_url: str, token: str) -> list[dict]:
    """Оценки из воркера обратной связи. Недоступный воркер не должен срывать выпуск — тогда отбор идёт без них."""
    if not feedback_url or not token:
        return []
    try:
        response = httpx.get(f"{feedback_url.rstrip('/')}/votes", headers={"Authorization": f"Bearer {token}"}, timeout=20)
        response.raise_for_status()
        return list(response.json().get("votes", []))
    except (httpx.HTTPError, ValueError) as exc:
        log.error("оценки из воркера не получены: %s", exc)
        return []
