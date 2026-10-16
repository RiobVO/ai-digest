from datetime import datetime
from html import escape

import httpx

from digest.llm import Digest
from digest.render import human_date

TG_LIMIT = 4096
BULLETS = 4


class TelegramError(RuntimeError):
    pass


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_message(digest: Digest, page_url: str, when: datetime) -> str:
    head = [f"<b>{escape(_clip(digest.headline, 200))}</b>", f"<i>ИИ-дайджест · {human_date(when)}</i>", ""]
    body = []
    if digest.entries:
        top = digest.entries[0]
        body.append(f"<b>Главное:</b> {escape(_clip(top.title, 300))}")
        if top.why:
            body.append(f"<b>Зачем тебе:</b> {escape(_clip(top.why, 500))}")
        body.append("")
        body += [f"• {escape(_clip(e.title, 300))}" for e in digest.entries[1:1 + BULLETS]]
        hidden = len(digest.entries) - 1 - BULLETS
        if hidden > 0:
            body.append(f"…и ещё {hidden} в разборе")
    tail = ["", f'<a href="{escape(page_url)}">Разбор целиком</a>']
    # Длинные заголовки могут не влезть в лимит Telegram — срезаем пункты списка с конца
    while body and len("\n".join(head + body + tail)) > TG_LIMIT:
        body.pop()
    return "\n".join(head + body + tail)


def send(text: str, page_url: str, token: str, chat_id: str) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "link_preview_options": {"url": page_url, "prefer_small_media": True}}
    try:
        response = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=30)
    except httpx.HTTPError as exc:
        # URL запроса содержит токен бота — в текст ошибки его не пропускаем
        raise TelegramError(f"Telegram недоступен: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise TelegramError(f"Telegram ответил {response.status_code}: {response.json().get('description')}")
