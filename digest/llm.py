import json
import logging
import time
from dataclasses import dataclass, field

import httpx

from digest.dedup import absorb
from digest.models import Item

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {429, 500, 503}
RETRY_DELAYS = (15, 45)
SECTIONS = ["model", "release", "paper", "industry"]

PROMPT = """Ты редактор ежедневного ИИ-дайджеста для одного читателя.
Профиль читателя: {audience}

Ниже пронумерованные записи, собранные за сутки из блогов лабораторий, GitHub, HF Papers, HN, Reddit и медиа.
Отбери от 5 до 10 событий, которые читателю действительно стоит знать. Всё остальное — шум, его не возвращай.

Что важно: новые модели и открытые веса; релизы инструментов с заметными изменениями; статьи с высоким числом голосов
(score у hf_papers) или с практической пользой; крупные события индустрии (цены, лимиты API, сделки, регулирование).
Что шум: реклама и билеты на конференции; маркетинговые кейсы «как компания X использует Y»; новости не про ИИ;
пересказы того, что уже есть в другой записи; патч-релизы без содержательных изменений.

Правила:
- Если несколько записей про одно событие, верни одно событие и перечисли в ids все их номера; первым ставь первоисточник.
- section: model — новые модели и веса; release — версии инструментов; paper — научные статьи; industry — остальное.
- importance от 0 до 10: 9–10 только для событий, которые меняют то, чем читатель пользуется.
- title: по-русски, до 90 символов, суть без кликбейта. Названия продуктов и моделей не переводи.
- summary: 1–3 предложения по-русски, только факты из текста записи. Если описания нет, не додумывай — опиши по заголовку.
- why: одно предложение «зачем это читателю» с учётом профиля, только при importance >= 8, иначе пустая строка.
- story: если событие продолжает один из активных сюжетов ниже — его id (например, "s4"), иначе пустая строка.
- story_name: для нового сюжета — короткое имя линии до 40 символов (например, «Nemotron 3.5»); для продолжения — пустая строка.
- headline: общая тема дня по-русски, до 70 символов; не пересказывай заголовок главного события, а назови, что объединяет выпуск. lead: 1–2 предложения, о чём выпуск.

{preferences}

Активные сюжеты последних дней (id, имя, последнее событие):
{stories}

Записи:
{items}"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "lead": {"type": "STRING"},
        "items": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                "section": {"type": "STRING", "enum": SECTIONS},
                "importance": {"type": "NUMBER"},
                "title": {"type": "STRING"},
                "summary": {"type": "STRING"},
                "why": {"type": "STRING"},
                "story": {"type": "STRING"},
                "story_name": {"type": "STRING"},
            },
            "required": ["ids", "section", "importance", "title", "summary", "why", "story", "story_name"],
        }},
    },
    "required": ["headline", "lead", "items"],
}

EXPLAIN_PROMPT = """Ты пишешь подробные разборы для ИИ-дайджеста. Читатель: {audience}

Для каждого события ниже есть короткая карточка и текст первоисточника (может быть пустым или обрезанным).
Напиши разбор по-русски, простым языком, как знающий друг, без воды и канцелярита.

Поля:
- what: 2–4 предложения — что произошло и почему это заметно.
- points: 3–5 коротких пунктов с конкретикой из текста: цифры, размеры, лицензия, версии, сравнения.
- try_it: как попробовать самому — команда, ссылка на веса, где доступно. Если в тексте нет данных — пустая строка.
- caveats: ограничения и оговорки, которые есть в тексте (железо, лицензия, только английский, бета). Нет данных — пустая строка.

Жёсткое правило: используй только факты из карточки и текста первоисточника. Не придумывай цифры, команды и ссылки.
Если текст первоисточника пустой, сделай короткий разбор по карточке, а points — только то, что в ней есть.
Верни для каждого события его id.

События:
{events}"""

EXPLAIN_SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "what": {"type": "STRING"},
            "points": {"type": "ARRAY", "items": {"type": "STRING"}},
            "try_it": {"type": "STRING"},
            "caveats": {"type": "STRING"},
        },
        "required": ["id", "what", "points", "try_it", "caveats"],
    }}},
    "required": ["items"],
}


class LlmError(RuntimeError):
    pass


@dataclass
class Details:
    what: str
    points: list[str]
    try_it: str
    caveats: str


@dataclass
class Entry:
    item: Item
    section: str
    importance: float
    title: str
    summary: str
    why: str
    details: Details | None = None
    # Заполняются памятью (digest.memory): id для оценок, сюжетная линия и её история
    id: str = ""
    story: str = ""
    story_name: str = ""
    story_day: int = 0
    story_prev: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Digest:
    headline: str
    lead: str
    entries: list[Entry]
    noise: list[Item] = field(default_factory=list)


def build_prompt(items: list[Item], audience: str, preferences: str = "", stories: list[dict] | None = None) -> str:
    lines = []
    for n, item in enumerate(items):
        score = f" score={item.score}" if item.score is not None else ""
        summary = f"\n    {item.summary[:400]}" if item.summary else ""
        lines.append(f"[{n}] ({item.source}, {item.category}{score}) {item.title}{summary}")
    story_lines = [f"[{s['id']}] {s['name']} — {s['last_date']}: {s['last_title']} (дней: {s['days']})"
                   for s in stories or []] or ["(нет)"]
    return PROMPT.format(audience=audience, preferences=preferences or "Оценок читателя пока нет.",
                         stories="\n".join(story_lines), items="\n".join(lines))


def parse_selection(raw: dict, items: list[Item]) -> Digest:
    used: set[int] = set()
    entries = []
    for row in raw.get("items", []):
        ids = [i for i in row.get("ids", []) if 0 <= i < len(items) and i not in used]
        if not ids:
            log.warning("модель вернула событие без валидных ids: %s", row.get("title"))
            continue
        used.update(ids)
        primary = items[ids[0]]
        for extra in ids[1:]:
            absorb(primary, items[extra])
        section = row["section"] if row.get("section") in SECTIONS else "industry"
        entry = Entry(primary, section, float(row.get("importance", 0)), row["title"], row["summary"], row.get("why", ""))
        entry.story, entry.story_name = row.get("story", ""), row.get("story_name", "")
        entries.append(entry)
    entries.sort(key=lambda e: e.importance, reverse=True)
    noise = [item for n, item in enumerate(items) if n not in used]
    return Digest(raw.get("headline", ""), raw.get("lead", ""), entries, noise)


def attach_explanations(entries: list[Entry], raw: dict) -> None:
    for row in raw.get("items", []):
        n = row.get("id")
        if not isinstance(n, int) or not 0 <= n < len(entries):
            log.warning("разбор с неизвестным id %r пропущен", n)
            continue
        entries[n].details = Details(row["what"], list(row.get("points", [])), row.get("try_it", ""), row.get("caveats", ""))


def generate(prompt: str, schema: dict, model: str, api_key: str) -> dict:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0.3},
    }
    url = API_URL.format(model=model)
    for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
        try:
            response = httpx.post(url, headers={"x-goog-api-key": api_key}, json=body, timeout=180)
        except httpx.HTTPError as exc:
            if delay is None:
                raise LlmError(f"Gemini {model} недоступен: {type(exc).__name__}") from exc
            log.warning("Gemini %s, попытка %d: %s, повтор через %d с", model, attempt, type(exc).__name__, delay)
            time.sleep(delay)
            continue
        if response.status_code == 200:
            break
        if response.status_code not in RETRY_STATUS or delay is None:
            raise LlmError(f"Gemini {model} ответил {response.status_code}: {response.text[:300]}")
        log.warning("Gemini %s, попытка %d: HTTP %d, повтор через %d с", model, attempt, response.status_code, delay)
        time.sleep(delay)
    payload = response.json()
    try:
        raw = json.loads(payload["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise LlmError(f"не удалось разобрать ответ Gemini: {str(payload)[:300]}") from exc
    log.info("Gemini %s: токенов %s", payload.get("modelVersion"), payload.get("usageMetadata", {}).get("totalTokenCount"))
    return raw


def select(items: list[Item], audience: str, model: str, api_key: str,
           preferences: str = "", stories: list[dict] | None = None) -> Digest:
    prompt = build_prompt(items, audience, preferences, stories)
    return parse_selection(generate(prompt, SCHEMA, model, api_key), items)


def explain(entries: list[Entry], texts: dict[str, str], audience: str, model: str, api_key: str) -> None:
    events = []
    for n, entry in enumerate(entries):
        source = texts.get(entry.item.url, "") or "(текст недоступен)"
        events.append(f"### id={n}\nКарточка: {entry.title}. {entry.summary}\n"
                      f"Описание из ленты: {entry.item.summary[:600]}\nТекст первоисточника:\n{source}")
    raw = generate(EXPLAIN_PROMPT.format(audience=audience, events="\n\n".join(events)), EXPLAIN_SCHEMA, model, api_key)
    attach_explanations(entries, raw)
