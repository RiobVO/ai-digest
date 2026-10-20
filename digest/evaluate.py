"""Проверка промптов на замороженном срезе новостей: один и тот же вход, разные версии промпта — сравнимые цифры.

Метрики грубые и не заменяют чтение глазами, но ловят то, что читатель видит как «ИИ-слоп»: выдуманные цифры,
пустые оценки вместо фактов, «почему важно», пересказывающее карточку, и инфляцию важности."""
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from digest.llm import Digest, Entry
from digest.models import Item

# Оценки без содержания и канцелярит: такую фразу можно приклеить к любой новости, и она ничего не сообщит
SLOP_PATTERNS = [
    r"ключев\w*", r"являет\w*", r"представил\w*", r"позволя\w*", r"осуществл\w*",
    r"данн(?:ый|ая|ое|ого|ой|ом|ому)", r"в рамках", r"оптимизирован\w* для", r"важн\w* шаг\w*", r"правила игры",
    r"революци\w*", r"беспрецедентн\w*", r"новые возможности", r"для (?:всей )?индустрии", r"уникальн\w*",
    r"инновацион\w*", r"станет (?:гораздо |намного |значительно )?(?:проще|дешевле|доступнее)",
]
# Прогнозы без опоры на источник: модель достраивает последствия, которых в новости нет («придётся выключить»)
FORECAST_PATTERNS = [
    r"придётся", r"придется", r"будут вынуждены", r"будет вынужден\w*", r"на годы вперёд", r"на годы вперед",
    r"станет (?:заметно |гораздо |намного |значительно )?(?:проще|дешевле|доступнее|быстрее|труднее|сложнее)",
    r"станут (?:заметно |гораздо |намного |значительно )?(?:проще|дешевле|доступнее|быстрее)", r"всё чаще", r"все чаще",
]
FORECAST_RE = re.compile(r"(?<!\w)(?:" + "|".join(FORECAST_PATTERNS) + r")(?!\w)", re.I)
SLOP_RE = re.compile(r"(?<!\w)(?:" + "|".join(SLOP_PATTERNS) + r")(?!\w)", re.I)
# Тысячи через пробел («250 000», в том числе неразрывный) — одно число, а не «250» и «000»
NUMBER_RE = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)*")
# Тысячи не начинаются с нуля: «0,015» — дробь
THOUSANDS_RE = re.compile(r"[1-9]\d{0,2}(?:,\d{3})+")
# Доля основ «почему важно», уже встречавшихся в пересказе, после которой это повтор, а не новая мысль
REPEAT_SHARE = 0.6
STEM = 5
HIGH_IMPORTANCE = 8


def slop_hits(text: str) -> list[str]:
    return [m.group(0).lower() for m in SLOP_RE.finditer(text)]


def forecast_hits(text: str) -> list[str]:
    return [m.group(0).lower() for m in FORECAST_RE.finditer(text)]


def _number(token: str) -> str:
    # «2,000» в английском источнике — тысячи, «16,8» в русском тексте — дробь
    token = re.sub(r"[ \u00a0\u202f]", "", token)
    token = token.replace(",", "") if THOUSANDS_RE.fullmatch(token) else token.replace(",", ".")
    return token.lstrip("0") or "0"


def invented_numbers(text: str, source: str) -> list[str]:
    known = {_number(t) for t in NUMBER_RE.findall(source)}
    invented: list[str] = []
    for token in NUMBER_RE.findall(text):
        value = _number(token)
        if value not in known and value not in invented:
            invented.append(value)
    return invented


def _stems(text: str) -> set[str]:
    return {w[:STEM] for w in re.findall(r"\w{4,}", text.lower())}


def repeats(why: str, summary: str) -> bool:
    stems = _stems(why)
    return bool(stems) and len(stems & _stems(summary)) / len(stems) >= REPEAT_SHARE


def _entry_texts(entry: Entry) -> list[str]:
    texts = [entry.title, entry.summary, entry.why]
    if entry.details:
        d = entry.details
        texts += [d.what, *d.points, d.try_it, d.caveats]
    return [t for t in texts if t]


def _source_text(entry: Entry, texts: dict[str, str]) -> str:
    item = entry.item
    return " ".join([item.title, item.summary, texts.get(item.url, "")])


def report(digest: Digest, texts: dict[str, str]) -> dict:
    rows: list[dict[str, Any]] = []
    for entry in digest.entries:
        written = " ".join(_entry_texts(entry))
        rows.append({
            "title": entry.title,
            "importance": entry.importance,
            "slop": slop_hits(written),
            "forecasts": forecast_hits(written),
            "invented": invented_numbers(written, _source_text(entry, texts)),
            "why_repeats": bool(entry.why) and repeats(entry.why, entry.summary),
        })
    return {
        "entries": len(rows),
        "high_importance": sum(r["importance"] >= HIGH_IMPORTANCE for r in rows),
        "slop": sum(len(r["slop"]) for r in rows) + len(slop_hits(f"{digest.headline} {digest.lead}")),
        "invented_numbers": sum(len(r["invented"]) for r in rows),
        "forecasts": sum(len(r["forecasts"]) for r in rows),
        "why_repeats": sum(r["why_repeats"] for r in rows),
        "rows": rows,
    }


def save_snapshot(path: Path, items: list[Item], taken: datetime, texts: dict[str, str] | None = None) -> None:
    rows = [{**asdict(i), "published": i.published.isoformat() if i.published else None} for i in items]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"taken": taken.isoformat(), "items": rows, "texts": texts or {}},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def load_snapshot(path: Path) -> tuple[list[Item], datetime, dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for row in data["items"]:
        published = datetime.fromisoformat(row["published"]) if row["published"] else None
        items.append(Item(**{**row, "published": published, "also": [tuple(a) for a in row["also"]]}))
    return items, datetime.fromisoformat(data["taken"]), data["texts"]
