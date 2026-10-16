"""Итог недели: главные сюжетные линии за 7 дней. Хронологию строим из архива, модель только отбирает и пересказывает."""
from datetime import date, timedelta

from digest.llm import Details, Digest, Entry, generate
from digest.models import Item
from digest.render import short_day

WEEK_DAYS = 7
WEEK_TOP = 5

WEEK_PROMPT = """Ты редактор недельного итога ИИ-дайджеста. Читатель: {audience}

Ниже сюжетные линии последних 7 дней: у каждой номер, имя и события по датам из ежедневных выпусков.
Выбери до {top} линий, которые важнее всего для читателя за неделю, и расскажи о каждой.

Поля:
- group: номер линии.
- importance: 0–10.
- title: по-русски, до 90 символов — итог линии за неделю, а не первое событие.
- summary: 1–2 предложения — что изменилось за неделю.
- narrative: 3–5 предложений — как развивался сюжет по дням и к чему пришёл.
- why: одно предложение «зачем это читателю».
- headline: тема недели до 70 символов. lead: 1–2 предложения о неделе.

Используй только факты из событий ниже, ничего не добавляй.

Линии:
{groups}"""

WEEK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "lead": {"type": "STRING"},
        "items": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "group": {"type": "INTEGER"},
                "importance": {"type": "NUMBER"},
                "title": {"type": "STRING"},
                "summary": {"type": "STRING"},
                "narrative": {"type": "STRING"},
                "why": {"type": "STRING"},
            },
            "required": ["group", "importance", "title", "summary", "narrative", "why"],
        }},
    },
    "required": ["headline", "lead", "items"],
}


def collect_week(issues: list[dict], stories: dict[str, dict], today: date) -> list[dict]:
    cutoff = (today - timedelta(days=WEEK_DAYS - 1)).isoformat()
    groups: dict[str, dict] = {}
    for issue in sorted(issues, key=lambda i: i["date"]):
        if issue["date"] < cutoff:
            continue
        for e in issue["entries"]:
            key = e.get("story") or e["id"]
            name = stories.get(e.get("story", ""), {}).get("name") or e["title"]
            group = groups.setdefault(key, {"name": name, "events": []})
            group["events"].append({**e, "date": issue["date"]})
    return list(groups.values())


def build_week_prompt(groups: list[dict], audience: str) -> str:
    blocks = []
    for n, g in enumerate(groups):
        lines = "\n".join(f"  {e['date']}: {e['title']} ({e['section']}, {e['source']})" for e in g["events"])
        blocks.append(f"[{n}] {g['name']}\n{lines}")
    return WEEK_PROMPT.format(audience=audience, top=WEEK_TOP, groups="\n\n".join(blocks))

# Выпуски до смены разделов (16.10.2026) хранят старые имена; когда они выпадут из окна недели (после 23.10.2026),
# сопоставление можно удалить
LEGACY_SECTIONS = {"release": "dev", "industry": "business"}


def parse_week(raw: dict, groups: list[dict]) -> Digest:
    entries = []
    for row in raw.get("items", [])[:WEEK_TOP]:
        n = row.get("group")
        if not isinstance(n, int) or not 0 <= n < len(groups):
            continue
        g = groups[n]
        last = g["events"][-1]
        section = LEGACY_SECTIONS.get(last["section"], last["section"])
        item = Item(source=last["source"], title=last["title"], url=last["url"], category=section)
        entry = Entry(item, section, float(row.get("importance", 0)), row["title"], row["summary"], row.get("why", ""))
        entry.details = Details(row.get("narrative", ""), [f"{short_day(e['date'])} — {e['title']}" for e in g["events"]], "", "")
        entry.story_name = g["name"]
        entry.story_day = len({e["date"] for e in g["events"]})
        entries.append(entry)
    entries.sort(key=lambda e: e.importance, reverse=True)
    return Digest(raw.get("headline", ""), raw.get("lead", ""), entries)


def summarize_week(issues: list[dict], stories: dict[str, dict], today: date, audience: str, model: str,
                   api_key: str) -> tuple[Digest, dict]:
    groups = collect_week(issues, stories, today)
    raw = generate(build_week_prompt(groups, audience), WEEK_SCHEMA, model, api_key)
    week_issues = [i for i in issues if i["date"] >= (today - timedelta(days=WEEK_DAYS - 1)).isoformat()]
    stats = {"issues": len(week_issues), "events": sum(len(i["entries"]) for i in week_issues)}
    return parse_week(raw, groups), stats
