import json
import logging
import time
from dataclasses import dataclass, field

import httpx

from digest.dedup import absorb, coverage
from digest.models import Item

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {429, 500, 503}
RETRY_DELAYS = (15, 45)
# product — компании и продукты, model — модели и исследования, business — деньги, законы, люди,
# dev — инструменты под стек читателя, paper — научные статьи
SECTIONS = ["product", "model", "business", "dev", "paper"]
FALLBACK_SECTION = "product"

# Общий голос для отбора и разборов: читатель жаловался, что тексты звучат как пресс-релиз
VOICE = """Голос: знающий друг-разработчик пишет тебе в личку. Обращайся на «ты», пиши живо и коротко, как говорят люди.
Лёгкий айтишный сленг уместен, если он к месту: «выкатили», «завезли», «зарелизили», «локалка», «влезает в 24 ГБ», «гонять агентов».
Без мата, мемов, восклицаний и натужных шуток. Активные глаголы, конкретика вместо общих слов.
Канцелярит запрещён: «представила», «является», «осуществляет», «данный», «в рамках», «оптимизирована для», «позволяет».
Факты, цифры, версии и названия передавай точно — меняется только подача, не содержание."""

PROMPT = """Ты редактор ежедневного дайджеста о мире ИИ и технологий для одного читателя.
Профиль читателя: {audience}

Ниже пронумерованные записи за сутки: блоги лабораторий и компаний, Techmeme, Google News, профильные медиа, HN, Reddit,
HF Papers, релизы инструментов. У записи может быть резонанс: источников=N — сколько разных изданий написали
об этом событии; HN=баллы/комментарии — обсуждение на Hacker News; score — голоса за статью на HF Papers.

Задача: чтобы читатель за пару минут знал всё главное, что сегодня происходит в ИИ и технологиях, и узнавал это одним
из первых. Отбери от 6 до 12 событий. Всё остальное — шум, его не возвращай.

Как отбирать: главный вопрос — «будут ли об этом говорить завтра». Важны крупные релизы и продукты, сделки и деньги,
законы и суды, громкие инциденты, сильные исследования, смена лидеров рынка. При прочих равных выше то, у чего больше
резонанс: много источников и живое обсуждение на HN. Одна-две позиции — для профиля читателя (его стек и инструменты),
даже если резонанс у них меньше.
Разнообразие: выпуск — картина всего дня, а не одной компании. Не больше двух событий про одну компанию;
третье — только если день действительно про неё. Смотри шире OpenAI и Anthropic: Google, Meta, Microsoft, Apple, NVIDIA,
китайские лаборатории, чипы, роботы, стартапы, наука, законы разных стран.
Шум: реклама, конференции, маркетинговые кейсы «как компания X использует Y», мелкие патч-релизы, мнения без новости,
пересказы того, что уже есть в другой записи.

Правила:
- Если несколько записей про одно событие, верни одно событие и перечисли в ids все их номера; первым ставь первоисточник
  (официальный блог компании или самое авторитетное издание).
- section: product — компании и продукты; model — модели и исследования; business — деньги, законы, суды, люди;
  dev — инструменты и релизы, которые касаются стека читателя; paper — научные статьи с HF Papers.
- importance от 0 до 10 — насколько событие важно для мира ИИ и технологий: 9–10 только для того, о чём говорят все.
- title: по-русски, до 90 символов, суть без кликбейта. Названия продуктов, компаний и моделей не переводи.
- summary: 1–3 предложения по-русски, только факты из записи. Если описания нет, не додумывай — опиши по заголовку.
- why: одно предложение «почему это важно» — что это меняет; если касается стека читателя, скажи как.
  Только при importance >= 8, иначе пустая строка.
- story: если событие продолжает один из активных сюжетов ниже — его id (например, "s4"), иначе пустая строка.
- story_name: для нового сюжета — короткое имя линии до 40 символов (например, «Nemotron 3.5»); для продолжения — пустая строка.
- headline: общая тема дня по-русски, до 70 символов; не пересказывай заголовок главного события, а назови, что объединяет
  выпуск. lead: 1–2 предложения, о чём выпуск.

""" + VOICE + """

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
Напиши разбор по-русски, без воды.

""" + VOICE + """

Поля:
- what: 2–4 предложения — что произошло и почему это заметно.
- points: 3–5 коротких пунктов с конкретикой из текста: цифры, размеры, лицензия, версии, сравнения.
- try_it: где взять и как попробовать самому — веса, сервис, ссылка. Команды сюда не пиши. Нет данных — пустая строка.
- commands: команды терминала дословно из текста первоисточника, каждая отдельным элементом, без пояснений и без «$».
  Только то, что можно вставить в терминал как есть. Нет команд в тексте — пустой список.
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
            "commands": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["id", "what", "points", "try_it", "caveats", "commands"],
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
    # Отдельным полем, а не внутри try_it: модель вписывает команды прямо в текст, и вытащить их оттуда надёжно нельзя
    commands: list[str] = field(default_factory=list)


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
        reach = coverage(item)
        score = "".join([
            f" источников={reach}" if reach > 1 else "",
            f" HN={item.hn_points}/{item.hn_comments}" if item.hn_points else "",
            f" score={item.score}" if item.score is not None and not item.hn_points else "",
        ])
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
        section = row["section"] if row.get("section") in SECTIONS else FALLBACK_SECTION
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
        commands = [c.strip().removeprefix("$").strip() for c in row.get("commands", [])]
        entries[n].details = Details(row["what"], list(row.get("points", [])), row.get("try_it", ""),
                                     row.get("caveats", ""), [c for c in commands if c])


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
