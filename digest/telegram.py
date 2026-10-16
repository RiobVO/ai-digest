from datetime import datetime
from html import escape

import httpx

from digest.llm import Digest, Entry
from digest.dedup import coverage
from digest.render import human_date, plural

TG_LIMIT = 4096
SHOWN = 5
# С какого охвата помечать пункт: одно-два издания — обычная новость, три и больше — её обсуждают
LOUD_COVERAGE = 3


class TelegramError(RuntimeError):
    pass


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _line(n: int, entry: Entry) -> str:
    story = f" <i>· сюжет, {entry.story_day}-й день</i>" if entry.story_day >= 2 else ""
    reach = coverage(entry.item)
    loud = f" <i>· {plural(reach, 'издание', 'издания', 'изданий')}</i>" if reach >= LOUD_COVERAGE else ""
    return f"<b>{n}.</b> {escape(_clip(entry.title, 300))}{loud}{story}"


def build_message(digest: Digest, page_url: str, when: datetime, label: str = "ИИ-дайджест") -> str:
    head = [f"<b>{escape(_clip(digest.headline, 200))}</b>", f"<i>{escape(label)} · {human_date(when)}</i>", ""]
    body = []
    shown = digest.entries[:SHOWN]
    if shown:
        body.append(_line(1, shown[0]))
        if shown[0].why:
            body.append(f"<b>Почему важно:</b> {escape(_clip(shown[0].why, 500))}")
        body.append("")
        body += [_line(n, e) for n, e in enumerate(shown[1:], start=2)]
        hidden = len(digest.entries) - len(shown)
        if hidden > 0:
            body.append(f"…и ещё {hidden} в разборе")
    tail = ["", f'<a href="{escape(page_url)}">Разбор целиком</a>']
    # Длинные заголовки могут не влезть в лимит Telegram — срезаем пункты списка с конца
    while body and len("\n".join(head + body + tail)) > TG_LIMIT:
        body.pop()
    return "\n".join(head + body + tail)


def build_keyboard(digest: Digest) -> dict | None:
    """Кнопки оценок под сообщением: номер кнопки = номер пункта в тексте; нажатия принимает воркер обратной связи."""
    shown = [(n, e) for n, e in enumerate(digest.entries[:SHOWN], start=1) if e.id]
    if not shown:
        return None
    return {"inline_keyboard": [
        [{"text": f"👍 {n}", "callback_data": f"u:{e.id}"} for n, e in shown],
        [{"text": f"👎 {n}", "callback_data": f"d:{e.id}"} for n, e in shown],
    ]}


def send(text: str, page_url: str, token: str, chat_id: str, keyboard: dict | None = None, silent: bool = False) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_notification": silent,
               "link_preview_options": {"url": page_url, "prefer_small_media": True}}
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        response = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=30)
    except httpx.HTTPError as exc:
        # URL запроса содержит токен бота — в текст ошибки его не пропускаем
        raise TelegramError(f"Telegram недоступен: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise TelegramError(f"Telegram ответил {response.status_code}: {response.json().get('description')}")
