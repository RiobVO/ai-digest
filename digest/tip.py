"""Фишка дня: первая свежая фишка сегодняшних выпусков приходит отдельным сообщением в случайный час дня.
Без модели: текст фишки написан при сборке выпуска и лежит в архиве (memory._archived)."""
import json
import logging
import random
from collections.abc import Callable
from datetime import date, datetime
from html import escape
from pathlib import Path

from digest.alert import is_quiet
from digest.render import DISPLAY_TZ, _href
from digest.telegram import TG_LIMIT, _clip, _visible

log = logging.getLogger(__name__)

# Окно случайного часа по Ташкенту: после утреннего выпуска (8:07) и с запасом до тихих часов срочных (23:00),
# чтобы сорвавшийся запуск успел уйти в следующий час
TIP_FROM, TIP_TO = 10, 21


def tip_hour(day: date) -> int:
    """Час случайный, но один на весь день: часовые запуски сходятся в нём, ничего не храня.
    Строковое зерно random не зависит от PYTHONHASHSEED, поэтому на любом раннере час тот же."""
    return random.Random(day.isoformat()).randrange(TIP_FROM, TIP_TO)


def due_tip(now: datetime, state: dict) -> bool:
    """Пора слать: настал час этого дня, сегодня ещё не отправляли и не тихие часы."""
    local = now.astimezone(DISPLAY_TZ)
    return not is_quiet(now) and local.hour >= tip_hour(local.date()) and state.get("day") != local.date().isoformat()


def pick_tip(issues: list[dict], day: date) -> tuple[dict, dict] | None:
    """Только свежие: фишки выпусков этого дня. Архив — новые сверху, поэтому идём с конца: утренний выпуск вышел
    раньше вечернего. Внутри выпуска фишки стоят по важности — берём первую."""
    for issue in reversed([i for i in issues if i["date"] == day.isoformat()]):
        for entry in issue["entries"]:
            if entry.get("section") == "tips":
                return issue, entry
    return None


def build_tip_message(issue: dict, entry: dict, site_url: str) -> str:
    base = site_url.rstrip("/")
    head = ["💡 <b>Фишка дня</b>", "", f"<b>{escape(_clip(entry['title'], 200))}</b>"]
    if entry.get("summary"):
        head.append(escape(_clip(entry["summary"], 1200)))
    if entry.get("why"):
        head += ["", f"<i>{escape(_clip(entry['why'], 600))}</i>"]
    card = f"{base}/{issue['page']}#e-{entry['id']}"
    tail = ["", f'<a href="{_href(card)}">Карточка в выпуске</a> · <a href="{_href(base + "/tips.html")}">Арсенал фишек</a>']
    # Команда или промпт уже сверены с текстом доверенного источника (llm.verify_copies) до записи в архив
    with_copy = "\n".join(head + ["", f"<code>{escape(entry['copy'])}</code>"] + tail) if entry.get("copy") else ""
    # Длинный промпт не обрезаем — обрезанный не сработает; он остаётся в карточке, а Telegram не отклонит сообщение
    # (ревью Codex: отказ Telegram повторялся бы каждый час, и фишка не ушла бы весь день)
    if with_copy and _visible(with_copy) <= TG_LIMIT:
        return with_copy
    return "\n".join(head + tail)


def run(state_dir: Path, now: datetime, site_url: str, sender: Callable[[str, str], None]) -> bool:
    """Часовой запуск: отправляет фишку дня, если пора, и запоминает день. True — отправлено."""
    path = state_dir / "tip.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not due_tip(now, state):
        return False
    day = now.astimezone(DISPLAY_TZ).date()
    issues = json.loads((state_dir / "issues.json").read_text(encoding="utf-8"))
    picked = pick_tip(issues, day)
    if picked is None:
        # День не помечаем: утром фишек не было — ещё может принести вечерний выпуск
        log.info("свежих фишек на %s пока нет", day)
        return False
    issue, entry = picked
    sender(build_tip_message(issue, entry, site_url), f"{site_url.rstrip('/')}/{issue['page']}#e-{entry['id']}")
    path.write_text(json.dumps({"day": day.isoformat(), "id": entry["id"]}, ensure_ascii=False), encoding="utf-8")
    log.info("фишка дня отправлена: %s", entry["title"])
    return True
