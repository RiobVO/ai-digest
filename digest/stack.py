"""«Твой стек»: релизы инструментов читателя (секция [[stack]] в sources.toml) прямо с GitHub, без модели.

Показываем только минорные и мажорные версии и патчи с ломающими изменениями: патч-релизы выходят каждый день
и шумят. Пре-релиз определяет флаг GitHub: у n8n бета 2.45.0 вышла с чистым тегом (21.10), а Atom-лента флаг не отдаёт;
суффикс тега (rc, alpha, nightly, preview…) — запасное правило, то же, что у релизных источников выпуска."""
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from digest.fetch import PRERELEASE, USER_AGENT

log = logging.getLogger(__name__)

MAX_AGE_HOURS = 30
API = "https://api.github.com/repos/{repo}/releases"
# Последних релизов хватает с запасом: у Gemini CLI за сутки nightly, preview и стабильный
PER_PAGE = 15
# Версия из тега: v1.2.3, rust-v0.163.0, n8n@2.45.0, v22.8. Тег без версии (b11798 у llama.cpp, stable у n8n) — мимо
VERSION = re.compile(r"(?:^|[^\d.])v?(\d+)\.(\d+)(?:\.(\d+))?(?![\d.]*\d)")
BREAKING = re.compile(r"\bbreaking\b", re.I)
# «No breaking changes», «Non-breaking fixes», «without breaking the API» — обычный патч: отрицания вырезаем до поиска
NOT_BREAKING = re.compile(r"\b(?:no|non|not|without)[\s-]+breaking\b", re.I)


@dataclass
class Release:
    name: str
    version: str
    kind: str
    url: str
    published: datetime


def classify(tag: str, notes: str) -> str | None:
    """major / minor / breaking (патч с ломающими изменениями) или None — не показывать."""
    if PRERELEASE.search(tag):
        return None
    match = VERSION.search(tag)
    if not match:
        return None
    _, minor, patch = match.groups()
    if patch and int(patch) > 0:
        return "breaking" if BREAKING.search(NOT_BREAKING.sub("", notes)) else None
    return "major" if int(minor) == 0 else "minor"


def parse_releases(payload: list[dict], tool: dict, now: datetime, max_age_hours: int = MAX_AGE_HOURS) -> list[Release]:
    cutoff = now - timedelta(hours=max_age_hours)
    releases = []
    for row in payload:
        if row.get("prerelease") or row.get("draft") or not row.get("published_at"):
            continue
        published = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
        # Имя релиза бывает «Release v0.65.0» или «Patch release v5.20.2»; версия надёжнее в теге
        kind = classify(row["tag_name"], row.get("body") or "")
        if published >= cutoff and kind:
            releases.append(Release(tool["name"], row["tag_name"], kind, row["html_url"], published))
    return releases


def fetch_stack(tools: list[dict], now: datetime, max_age_hours: int = MAX_AGE_HOURS) -> list[Release]:
    """Свежие заметные релизы всех инструментов. Недоступный репозиторий не мешает остальным и выпуску."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    # Без токена API даёт 60 запросов в час на IP, а IP у раннеров GitHub общие. В CI — токен самого запуска
    # (github.token); он только в заголовке, а httpx на уровне WARNING запросы не логирует
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    releases: list[Release] = []
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        for tool in tools:
            try:
                response = client.get(API.format(repo=tool["repo"]), params={"per_page": PER_PAGE})
                response.raise_for_status()
                releases.extend(parse_releases(response.json(), tool, now, max_age_hours))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.error("релизы %s не получены: %s", tool["name"], type(exc).__name__)
    return sorted(releases, key=lambda r: r.published, reverse=True)
