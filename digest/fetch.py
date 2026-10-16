import logging
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urljoin

import feedparser
import httpx

from digest.models import Item

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; ai-digest/0.1)"
DEFAULT_MAX_AGE_HOURS = 48
DATE_TOKEN = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, \d{4}"
TAG = re.compile(r"<[^>]+>")
PRERELEASE = re.compile(r"[-.]?(rc|alpha|beta|dev|pre)\d*\b", re.I)


def _text(html: str) -> str:
    return " ".join(unescape(TAG.sub(" ", html)).split())


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_feed(body: bytes, src: dict) -> list[Item]:
    items = []
    prefix = src.get("title_prefix")
    for entry in feedparser.parse(body).entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        title = _text(entry.get("title", ""))
        items.append(Item(
            source=src["name"],
            title=f"{prefix} {title}" if prefix else title,
            url=entry.get("link", ""),
            category=src["category"],
            published=datetime.fromtimestamp(timegm(stamp), timezone.utc) if stamp else None,
            summary=_text(entry.get("summary", ""))[:600],
        ))
    if src.get("latest_only"):
        # Репозитории вроде llama.cpp выпускают десяток сборок в день; в дайджест идёт одна стабильная
        stable = [i for i in items if not PRERELEASE.search(i.title)]
        return stable[:1]
    return items


def parse_hf_papers(payload: list, src: dict) -> list[Item]:
    items = []
    for row in payload:
        paper = row["paper"]
        upvotes = paper.get("upvotes", 0)
        if upvotes < src.get("min_upvotes", 0):
            continue
        items.append(Item(
            source=src["name"],
            title=paper["title"],
            url=f"https://arxiv.org/abs/{paper['id']}",
            category=src["category"],
            published=_iso(paper["publishedAt"]),
            summary=paper.get("ai_summary") or paper.get("summary", "")[:600],
            score=upvotes,
        ))
    return items


def parse_hn(payload: dict, src: dict) -> list[Item]:
    keywords = src.get("keywords")
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, keywords)) + r")\b", re.I) if keywords else None
    items = []
    for hit in payload["hits"]:
        if pattern and not pattern.search(hit["title"]):
            continue
        items.append(Item(
            source=src["name"],
            title=hit["title"],
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
            category=src["category"],
            published=_iso(hit["created_at"]),
            score=hit.get("points"),
        ))
    return items


def _clean_title(text: str, strip_words: list[str]) -> str:
    # Карточки блогов склеивают в ссылку дату и рубрику; срезаем их только по краям, чтобы не задеть заголовок
    edge = "|".join([DATE_TOKEN] + [re.escape(w) for w in strip_words])
    edges = re.compile(rf"^(?:{edge})\s*|\s*(?:{edge})$")
    previous = None
    while previous != text:
        previous, text = text, edges.sub("", text).strip()
    return text


def parse_html(body: str, src: dict) -> list[Item]:
    anchor = re.compile(rf"<a\b[^>]*href=\"({src['link_pattern']}/?)\"[^>]*>(.*?)</a>", re.S)
    titles: dict[str, str] = {}
    for href, inner in anchor.findall(body):
        url = urljoin(src["url"], href)
        title = _clean_title(_text(inner), src.get("strip_words", []))
        if len(title) > len(titles.get(url, "")):
            titles[url] = title
    return [Item(source=src["name"], title=t, url=u, category=src["category"]) for u, t in titles.items() if t]


NOISE_BLOCKS = re.compile(r"<(script|style|noscript|svg|nav|header|footer|aside|form)\b.*?</\1>", re.S | re.I)
MAIN_BLOCK = re.compile(r"<(article|main)\b[^>]*>(.*)</\1>", re.S | re.I)
ARTICLE_LIMIT = 8000


def extract_text(html: str, limit: int = ARTICLE_LIMIT) -> str:
    """Грубое извлечение текста статьи: без меню и скриптов, с приоритетом <article>/<main>."""
    html = NOISE_BLOCKS.sub(" ", html)
    main = MAIN_BLOCK.search(html)
    text = _text(main.group(2) if main else html)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


def fetch_articles(urls: list[str]) -> dict[str, str]:
    """Полные тексты первоисточников для подробного разбора; недоступная страница — пустая строка, не ошибка выпуска."""
    texts = {}
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            try:
                response = client.get(url)
                response.raise_for_status()
                texts[url] = extract_text(response.text)
            except httpx.HTTPError as exc:
                log.error("текст первоисточника %s не получен: %s", url, exc)
                texts[url] = ""
    return texts


def _fetch_one(client: httpx.Client, src: dict, now: datetime) -> list[Item]:
    kind = src["type"]
    if kind == "hn":
        since = int((now - timedelta(hours=src.get("max_age_hours", DEFAULT_MAX_AGE_HOURS))).timestamp())
        params = {"tags": "story", "hitsPerPage": 200,
                  "numericFilters": f"points>={src.get('min_points', 0)},created_at_i>{since}"}
        response = client.get(src["url"], params=params)
    else:
        response = client.get(src["url"])
    response.raise_for_status()
    if kind == "rss":
        return parse_feed(response.content, src)
    if kind == "hf_papers":
        return parse_hf_papers(response.json(), src)
    if kind == "hn":
        return parse_hn(response.json(), src)
    if kind == "html":
        return parse_html(response.text, src)
    raise ValueError(f"неизвестный тип источника {kind!r} у {src['name']}")


def fetch_all(sources: list[dict], now: datetime) -> tuple[list[Item], list[str]]:
    """Собирает все источники; упавший источник не валит выпуск, а попадает в список сбоев."""
    items: list[Item] = []
    failures: list[str] = []
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
        for src in sources:
            try:
                fetched = _fetch_one(client, src, now)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.error("источник %s не собран: %s", src["name"], exc)
                failures.append(f"{src['name']}: {type(exc).__name__}")
                continue
            cutoff = now - timedelta(hours=src.get("max_age_hours", DEFAULT_MAX_AGE_HOURS))
            fresh = [i for i in fetched if i.url and (i.published is None or i.published >= cutoff)]
            log.info("%s: %d из %d свежих", src["name"], len(fresh), len(fetched))
            items.extend(fresh)
    return items, failures
