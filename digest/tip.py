"""Фишки в течение дня: каждая свежая фишка сегодняшних выпусков приходит отдельным сообщением в свой случайный час.
Без модели: текст фишки написан при сборке выпуска и лежит в архиве (memory._archived)."""
import json
import logging
import random
from collections.abc import Callable
from datetime import datetime
from html import escape
from pathlib import Path

from digest.alert import is_quiet
from digest.render import DISPLAY_TZ, _href
from digest.telegram import TG_LIMIT, _clip, _visible

log = logging.getLogger(__name__)

# Окна по Ташкенту: утренние фишки — после выпуска 8:07 и до вечернего, вечерние — после 18:07 и до тихих часов
# срочных (23:00). Что не успело уйти до тихих часов, остаётся в выпуске и арсенале
WINDOWS = {"morning": (10, 18), "evening": (19, 23)}


def tip_hours(issue: dict) -> dict[str, int]:
    """Час каждой фишки выпуска: окно делится на равные отрезки, фишка — в случайный час своего отрезка, поэтому
    они идут в течение дня, а не пачкой. Зерно — дата и выпуск: часовые запуски сходятся в тех же часах, ничего
    не храня; строковое зерно random не зависит от PYTHONHASHSEED, на любом раннере часы те же."""
    slot = issue.get("slot", "morning")
    start, end = WINDOWS.get(slot, WINDOWS["morning"])
    tips = [e["id"] for e in issue["entries"] if e.get("section") == "tips"]
    span = (end - start) / max(len(tips), 1)
    rng = random.Random(f"{issue['date']}-{slot}")
    return {tip: start + int(n * span) + rng.randrange(max(int(span), 1)) for n, tip in enumerate(tips)}


def due_tips(issues: list[dict], now: datetime, sent: list[str]) -> list[tuple[dict, dict]]:
    """Свежие фишки, чей час настал и которые ещё не отправлены, — по порядку часов. В тихие часы — ничего."""
    if is_quiet(now):
        return []
    local = now.astimezone(DISPLAY_TZ)
    due = []
    # Архив — новые сверху: идём с конца, чтобы при равных часах утренняя фишка шла раньше вечерней
    for issue in reversed([i for i in issues if i["date"] == local.date().isoformat()]):
        hours = tip_hours(issue)
        due += [(hours[e["id"]], issue, e) for e in issue["entries"]
                if e.get("id") in hours and hours[e["id"]] <= local.hour and e["id"] not in sent]
    return [(issue, entry) for _, issue, entry in sorted(due, key=lambda row: row[0])]


def build_tip_message(issue: dict, entry: dict, site_url: str) -> str:
    base = site_url.rstrip("/")
    head = ["💡 <b>Фишка</b>", "", f"<b>{escape(_clip(entry['title'], 200))}</b>"]
    if entry.get("summary"):
        head.append(escape(_clip(entry["summary"], 1200)))
    if entry.get("why"):
        head += ["", f"<i>{escape(_clip(entry['why'], 600))}</i>"]
    if entry.get("steps"):
        head += ["", "<b>Подробности:</b>", *(f"{n}. {escape(_clip(step, 300))}" for n, step in enumerate(entry["steps"][:5], 1))]
    card = f"{base}/{issue['page']}#e-{entry['id']}"
    tail = ["", f'<a href="{_href(card)}">Карточка в выпуске</a> · <a href="{_href(base + "/tips.html")}">Арсенал фишек</a>']
    # Команда или промпт уже сверены с текстом доверенного источника (llm.verify_copies) до записи в архив
    with_copy = "\n".join(head + ["", f"<code>{escape(entry['copy'])}</code>"] + tail) if entry.get("copy") else ""
    # Длинный промпт не обрезаем — обрезанный не сработает; он остаётся в карточке, а Telegram не отклонит сообщение
    # (ревью Codex: отказ Telegram повторялся бы каждый час, и фишка не ушла бы)
    if with_copy and _visible(with_copy) <= TG_LIMIT:
        return with_copy
    return "\n".join(head + tail)


def run(state_dir: Path, now: datetime, site_url: str, sender: Callable[[str, str], None]) -> bool:
    """Часовой запуск: отправляет одну созревшую фишку и запоминает её. True — отправлено. Одна за запуск, даже если
    созрело несколько (запуски срывались): пачкой они пришли бы так же, как в выпуске."""
    path = state_dir / "tip.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    today = now.astimezone(DISPLAY_TZ).date().isoformat()
    sent = state.get("sent", []) if state.get("day") == today else []
    due = due_tips(json.loads((state_dir / "issues.json").read_text(encoding="utf-8")), now, sent)
    if not due:
        return False
    issue, entry = due[0]
    sender(build_tip_message(issue, entry, site_url), f"{site_url.rstrip('/')}/{issue['page']}#e-{entry['id']}")
    path.write_text(json.dumps({"day": today, "sent": [*sent, entry["id"]]}, ensure_ascii=False), encoding="utf-8")
    log.info("фишка отправлена: %s, осталось созревших %d", entry["title"], len(due) - 1)
    return True
