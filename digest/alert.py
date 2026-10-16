"""Срочные новости между утренними выпусками: громкое событие уходит в Telegram сразу, а не на следующее утро."""
import json
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from digest.dedup import canonical_key, dedupe, norm_title
from digest.models import Item
from digest.render import DISPLAY_TZ, _href, plural

ALERT_HOURS = 6
MIN_PUBLISHERS = 3
MIN_HN_POINTS = 300
DAILY_CAP = 2
MAX_CANDIDATES = 5
SENT_DAYS = 3
REJECT_COOLDOWN = timedelta(hours=6)
# Отказ модели пересматриваем раньше срока, только если о событии заметно больше пишут или спорят
REJECT_RECHECK_GAIN = 2
UNDATED_DAYS = 30
QUIET_FROM, QUIET_TO = 23, 8
# Агрегаторы повторяют чужие статьи: Techmeme + HN + само издание — это одна статья, а не три подтверждения
AGGREGATORS = {"techmeme", "hn", "hn_top", "gnews_ai"}

ALERT_PROMPT = """Ты дежурный редактор срочных новостей об ИИ и технологиях для одного читателя.
Профиль читателя: {audience}

Ниже кандидаты за последние часы. Срочно — только то, о чём завтра будут говорить все: крупный релиз от ведущей
лаборатории или компании, большая сделка или поглощение, решение регулятора или суда, громкий инцидент, резкая смена
цен или доступа к популярному сервису. Обычные посты блогов, мелкие обновления, мнения и кейсы — не срочно.
Если событие то же, что уже было отправлено (список ниже), — не срочно.

Для каждого кандидата верни index и urgent. Для срочного напиши title (по-русски, до 90 символов) и text
(1–2 предложения: что случилось и почему это важно) — живо, на «ты», без канцелярита; факты и цифры точно.

Уже отправлено за последние дни:
{sent}

Кандидаты:
{candidates}"""

ALERT_SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "index": {"type": "INTEGER"},
            "urgent": {"type": "BOOLEAN"},
            "title": {"type": "STRING"},
            "text": {"type": "STRING"},
        },
        "required": ["index", "urgent", "title", "text"],
    }}},
    "required": ["items"],
}


def publishers(item: Item) -> int:
    return len({item.source, *(source for source, _ in item.also)} - AGGREGATORS)


def strength(item: Item) -> int:
    return publishers(item) + item.hn_points // 100


def item_keys(item: Item) -> list[str]:
    urls = [item.url, *(url for _, url in item.also)]
    return sorted({canonical_key(u) for u in urls} | {f"title:{norm_title(item.title)}"})


def is_quiet(now: datetime) -> bool:
    hour = now.astimezone(DISPLAY_TZ).hour
    return hour >= QUIET_FROM or hour < QUIET_TO


class AlertState:
    """Что уже отправлено, что модель отклонила и какие недатированные ссылки уже встречались."""

    def __init__(self, data: dict, path: Path | None = None):
        self.path = path
        self.sent: list[dict] = data.get("sent", [])
        self.rejected: list[dict] = data.get("rejected", [])
        self.undated: dict[str, str] = data.get("undated", {})
        self.primed: bool = data.get("primed", False)

    @classmethod
    def load(cls, path: Path) -> "AlertState":
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(data, path)

    def is_sent(self, keys: list[str]) -> bool:
        return any(set(keys) & set(row["keys"]) for row in self.sent)

    def is_rejected(self, keys: list[str], power: int, now: datetime) -> bool:
        for row in self.rejected:
            if set(keys) & set(row["keys"]):
                cooling = now - datetime.fromisoformat(row["at"]) < REJECT_COOLDOWN
                return cooling and power < row["strength"] + REJECT_RECHECK_GAIN
        return False

    def mark_sent(self, item: Item, title: str, now: datetime) -> None:
        self.sent.append({"at": now.isoformat(), "day": now.astimezone(DISPLAY_TZ).date().isoformat(),
                          "title": title, "keys": item_keys(item)})

    def mark_rejected(self, item: Item, now: datetime) -> None:
        keys = item_keys(item)
        self.rejected = [r for r in self.rejected if not set(keys) & set(r["keys"])]
        self.rejected.append({"at": now.isoformat(), "keys": keys, "strength": strength(item)})

    def sent_today(self, now: datetime) -> int:
        today = now.astimezone(DISPLAY_TZ).date().isoformat()
        return sum(row["day"] == today for row in self.sent)

    def recent_titles(self) -> list[str]:
        return [row["title"] for row in self.sent]

    def save(self, now: datetime) -> None:
        def fresh(at: str, days: float) -> bool:
            return now - datetime.fromisoformat(at) < timedelta(days=days)

        data = {
            "sent": [r for r in self.sent if fresh(r["at"], SENT_DAYS)],
            "rejected": [r for r in self.rejected if now - datetime.fromisoformat(r["at"]) < REJECT_COOLDOWN * 4],
            "undated": {u: at for u, at in self.undated.items() if fresh(at, UNDATED_DAYS)},
            "primed": True,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def split_undated(items: list[Item], state: AlertState, now: datetime) -> list[Item]:
    """Блоги без дат (Anthropic, Meta): срочной считается только ссылка, которой раньше не было.
    Первый запуск лишь запоминает уже опубликованное — иначе старые посты разом стали бы «новостями»."""
    fresh = []
    for item in items:
        if item.published is not None:
            fresh.append(item)
            continue
        if item.url not in state.undated:
            state.undated[item.url] = now.isoformat()
            if state.primed:
                fresh.append(item)
    state.primed = True
    return fresh


def find_candidates(items: list[Item], state: AlertState, now: datetime) -> list[Item]:
    found = []
    for item in dedupe(items):
        keys = item_keys(item)
        official = item.category == "lab"
        loud = publishers(item) >= MIN_PUBLISHERS or item.hn_points >= MIN_HN_POINTS
        if not (official or loud) or state.is_sent(keys) or state.is_rejected(keys, strength(item), now):
            continue
        found.append(item)
    found.sort(key=strength, reverse=True)
    return found[:MAX_CANDIDATES]


def build_prompt(candidates: list[Item], state: AlertState, audience: str) -> str:
    lines = [f"[{n}] ({c.source}, изданий={publishers(c)}, HN={c.hn_points}) {c.title}\n    {c.summary[:400]}"
             for n, c in enumerate(candidates)]
    return ALERT_PROMPT.format(audience=audience, sent="\n".join(f"- {t}" for t in state.recent_titles()) or "(ничего)",
                               candidates="\n".join(lines))


def choose(raw: dict, candidates: list[Item]) -> tuple[tuple[Item, str, str] | None, list[Item]]:
    """Первый срочный — в отправку; явно оценённые как несрочные — в отказ. Остальное модель не решала."""
    picked, rejected = None, []
    for row in raw.get("items", []):
        n = row.get("index")
        if not isinstance(n, int) or not 0 <= n < len(candidates):
            continue
        if row.get("urgent") and picked is None and row.get("title"):
            picked = (candidates[n], row["title"], row.get("text", ""))
        elif not row.get("urgent"):
            rejected.append(candidates[n])
    return picked, rejected


def build_alert(item: Item, title: str, text: str) -> str:
    buzz = []
    if publishers(item) > 1:
        buzz.append(plural(publishers(item), "издание", "издания", "изданий"))
    if item.hn_points:
        buzz.append(f"HN {item.hn_points} ▲ · {item.hn_comments} комм.")
    links = [f'<a href="{_href(item.url)}">Первоисточник</a>']
    if item.hn_url:
        links.append(f'<a href="{_href(item.hn_url)}">Обсуждение на HN</a>')
    parts = ["⚡ <b>Срочно</b>", "", f"<b>{escape(title)}</b>", escape(text)]
    if buzz:
        parts += ["", f"<i>{' · '.join(buzz)}</i>"]
    return "\n".join(parts + ["", " · ".join(links)])
