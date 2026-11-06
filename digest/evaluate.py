"""Проверка промптов на замороженном срезе новостей: один и тот же вход, разные версии промпта — сравнимые цифры.

Метрики грубые и не заменяют чтение глазами, но ловят то, что читатель видит как «ИИ-слоп»: выдуманные цифры,
пустые оценки вместо фактов, «почему важно», пересказывающее карточку, и инфляцию важности."""
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from digest.llm import DATA_NOTE, Digest, Entry, LlmError, fence, generate_first
from digest.models import Item

log = logging.getLogger(__name__)

# Оценки без содержания: такую фразу можно приклеить к любой новости, и она ничего о ней не сообщит
SLOP_PATTERNS = [
    r"ключев\w*", r"важн\w* шаг\w*", r"правила игры", r"революци\w*", r"беспрецедентн\w*", r"новые возможности",
    r"для (?:всей )?индустрии", r"уникальн\w*", r"инновацион\w*",
]
# Канцелярит: смысл не портит, но текст звучит как пресс-релиз; считается отдельно, чтобы не смешивать с пустыми оценками
OFFICIALESE_PATTERNS = [
    r"являет\w*", r"представил\w*", r"позволя\w*", r"осуществл\w*", r"данн(?:ый|ая|ое|ого|ой|ом|ому)", r"в рамках",
    r"оптимизирован\w* для",
]
# Прогнозы без опоры на источник: модель достраивает последствия, которых в новости нет («придётся выключить»)
FORECAST_PATTERNS = [
    r"придётся", r"придется", r"будут вынуждены", r"будет вынужден\w*", r"на годы вперёд", r"на годы вперед",
    r"станет (?:заметно |гораздо |намного |значительно )?(?:проще|дешевле|доступнее|быстрее|труднее|сложнее)",
    r"станут (?:заметно |гораздо |намного |значительно )?(?:проще|дешевле|доступнее|быстрее)", r"всё чаще", r"все чаще",
]

# «Почему важно» по шаблону и догадки о мотивах: фраза подходит к любой новости своего типа («случай показывает
# риски…») или приписывает компании цель, которой нет в записи («шаг направлен на защиту от критики»)
TEMPLATE_PATTERNS = [
    r"показыва\w*", r"(?:про)?демонстрир\w*", r"наглядн\w*", r"подч[её]ркива\w*", r"свидетельству\w*",
    r"иллюстрир\w*", r"направлен\w* на", r"стремит\w*", r"стремлени\w*", r"в попытке", r"призван\w*",
    r"пытает\w*", r"очередн\w*", r"доказыва\w*",
]


def _words(patterns: list[str]) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)(?:" + "|".join(patterns) + r")(?!\w)", re.I)


SLOP_RE, OFFICIALESE_RE, FORECAST_RE = _words(SLOP_PATTERNS), _words(OFFICIALESE_PATTERNS), _words(FORECAST_PATTERNS)
TEMPLATE_RE = _words(TEMPLATE_PATTERNS)
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


def officialese_hits(text: str) -> list[str]:
    return [m.group(0).lower() for m in OFFICIALESE_RE.finditer(text)]


def forecast_hits(text: str) -> list[str]:
    return [m.group(0).lower() for m in FORECAST_RE.finditer(text)]


def template_hits(text: str) -> list[str]:
    return [m.group(0).lower() for m in TEMPLATE_RE.finditer(text)]


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


SENTENCE = re.compile(r"(?<=[.!?…])\s+")
# Текста первоисточника в запросе на переписывание хватает на проверку фактов и не раздувает промпт
REWRITE_TEXT_CHARS = 3000

# Lite дописывает в карточки правдоподобные детали из памяти: на срезе 05.11.2026 «$6,34 млрд» при $6.3B в источнике,
# «за 30 часов», «770 млрд параметров», «на 2027 год» — в 12 карточках из 28, одни и те же в каждом прогоне. Правило
# «цифры только из записи» в промпте отбора он не держит (как и правило про корпоративные деньги), поэтому проверка
# в коде той же метрикой, что в eval. Предложение с чужим числом убираем, если описание без него не пустеет;
# одиночное предложение и заголовок резать нельзя — их переписывает отдельный узкий вызов Lite (квота 500 в день)
NUMBERS_PROMPT = """Ты проверяешь карточки личного дайджеста новостей на числа, которых нет в источнике.
Карточки написаны по записям из лент. В каждой есть числа, даты или суммы, которых нет ни в записи, ни в тексте
первоисточника: их дописали из памяти, и читатель получит неверный факт. Эти числа перечислены у карточки.
Перепиши title, summary и why каждой карточки так, чтобы в них не осталось ни одного числа, даты или суммы, которых
нет в записи или тексте первоисточника. Числа, которые в записи есть, оставь, формат можно оставить русским
(«$6,3 млрд»). Деталь без подтверждения просто убери: не заменяй её оценкой («огромная», «многомиллиардная») и ничего
нового не добавляй. Убрав деталь, перестрой фразу, чтобы она читалась гладко: обрывков вроде «за работы» или «на год»
остаться не должно. Смысл и стиль карточки сохрани. Поле без проблемных чисел верни без изменений; why может быть пустым.

""" + DATA_NOTE + """
{cards}

Верни JSON строго по схеме: для каждой карточки её id и три поля."""

NUMBERS_SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "INTEGER"}, "title": {"type": "STRING"}, "summary": {"type": "STRING"},
                       "why": {"type": "STRING"}},
        "propertyOrdering": ["id", "title", "summary", "why"],
        "required": ["id", "title", "summary", "why"],
    }}},
    "required": ["items"],
}


def _known_text(item: Item, texts: dict[str, str], records: dict[str, str]) -> str:
    """Всё, откуда модель могла законно взять число: запись, текст первоисточника и записи склеенных дублей.
    Один эталон у защиты и у метрики report: иначе защита считала бы карточку чистой, а eval — нет."""
    parts = [item.title, item.summary, texts.get(item.url, ""), records.get(item.url, "")]
    parts += [records.get(url, "") for _, url in item.also]
    return " ".join(p for p in parts if p)


def _cut(text: str, source: str) -> str | None:
    """Текст без предложений с числами не из источника; None — не осталось ни одного предложения."""
    kept = [s for s in SENTENCE.split(text) if s and not invented_numbers(s, source)]
    return " ".join(kept) if kept else None


def _card_block(n: int, entry: Entry, left: list[str], texts: dict[str, str], records: dict[str, str]) -> str:
    item = entry.item
    others = [records[url] for _, url in item.also if records.get(url)]
    text = texts.get(item.url, "")[:REWRITE_TEXT_CHARS]
    return (f"--- карточка id={n} ---\nЧисла не из записи: {', '.join(left)}\n"
            f"Запись: {item.title}. {item.summary}\n"
            + (f"Другие записи события: {' | '.join(others)}\n" if others else "")
            + f"Текст первоисточника:\n{fence(text) if text else '(недоступен)'}\n"
            f"title: {entry.title}\nsummary: {entry.summary}\nwhy: {entry.why}")


def verify_numbers(entries: list[Entry], texts: dict[str, str], records: dict[str, str], models: list[str],
                   api_key: str) -> None:
    """Убирает из карточек числа, которых нет в записи и тексте первоисточника (см. NUMBERS_PROMPT).
    records — «заголовок описание» каждой записи пула по адресу: число из описания склеенного дубля тоже из записи.
    Не исправленная карточка (переписать не удалось или модель недоступна) остаётся как есть с предупреждением:
    выпуск с одной лишней цифрой лучше выпуска без карточки."""
    pending: list[tuple[int, Entry, str, list[str]]] = []
    for n, entry in enumerate(entries):
        source = _known_text(entry.item, texts, records)
        if not invented_numbers(f"{entry.title} {entry.summary} {entry.why}", source):
            continue
        if entry.why and invented_numbers(entry.why, source):
            # why — одно предложение-вывод; без него карточка живёт («лучше без why, чем с общей фразой»)
            entry.why = ""
        cut = _cut(entry.summary, source)
        if cut is not None:
            entry.summary = cut
        left = invented_numbers(f"{entry.title} {entry.summary}", source)
        if left:
            pending.append((n, entry, source, left))
        else:
            log.warning("числа не из записи убраны из карточки: %s", entry.title)
    if not pending:
        return
    cards = "\n\n".join(_card_block(n, e, left, texts, records) for n, e, _, left in pending)
    try:
        raw = generate_first(NUMBERS_PROMPT.format(cards=cards), NUMBERS_SCHEMA, models, api_key, call="numbers")
    except LlmError as exc:
        log.warning("карточки с числами не из записи не переписаны (%d): %s", len(pending), exc)
        return
    rows = {row["id"]: row for row in raw.get("items", []) if isinstance(row.get("id"), int)}
    for n, entry, source, left in pending:
        row = rows.get(n, {})
        title, summary, why = (str(row.get(f, "") or "").strip() for f in ("title", "summary", "why"))
        # Переписанный текст проверяем той же метрикой: модель могла убрать одно число и дописать другое
        if not title or not summary or invented_numbers(f"{title} {summary} {why}", source):
            log.warning("в карточке осталось число не из записи %s, оставляю как есть: %s", left, entry.title)
            continue
        entry.title, entry.summary, entry.why = title, summary, why


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


def report(digest: Digest, texts: dict[str, str], records: dict[str, str] | None = None) -> dict:
    """records — «заголовок описание» записей пула по адресу, как у verify_numbers; без них эталон — только запись."""
    records = records or {}
    rows: list[dict[str, Any]] = []
    for entry in digest.entries:
        written = " ".join(_entry_texts(entry))
        rows.append({
            "title": entry.title,
            "importance": entry.importance,
            "slop": slop_hits(written),
            "officialese": officialese_hits(written),
            "forecasts": forecast_hits(written),
            "invented": invented_numbers(written, _known_text(entry.item, texts, records)),
            "why_repeats": bool(entry.why) and repeats(entry.why, entry.summary),
            "templates": template_hits(entry.why),
        })
    # Строка «Ещё за день» — тоже текст для читателя: без проверки сбой второго вызова выглядел бы в eval чистым.
    # Важности и «почему» у неё нет — в те метрики она не входит
    for mention in digest.more:
        item = mention.item
        rows.append({
            "title": mention.title,
            "importance": 0,
            "slop": slop_hits(mention.title),
            "officialese": officialese_hits(mention.title),
            "forecasts": forecast_hits(mention.title),
            "invented": invented_numbers(mention.title, _known_text(item, texts, records)),
            "why_repeats": False,
            "templates": [],
        })
    return {
        "entries": len(digest.entries),
        "more": len(digest.more),
        "tips": sum(e.section == "tips" for e in digest.entries),
        "high_importance": sum(r["importance"] >= HIGH_IMPORTANCE for r in rows),
        "slop": sum(len(r["slop"]) for r in rows) + len(slop_hits(f"{digest.headline} {digest.lead}")),
        "invented_numbers": sum(len(r["invented"]) for r in rows),
        "officialese": sum(len(r["officialese"]) for r in rows)
        + len(officialese_hits(f"{digest.headline} {digest.lead}")),
        "forecasts": sum(len(r["forecasts"]) for r in rows),
        "why_repeats": sum(r["why_repeats"] for r in rows),
        "why_templates": sum(len(r["templates"]) for r in rows),
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
