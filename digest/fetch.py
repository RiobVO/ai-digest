import json
import logging
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urlencode, urljoin, urlsplit

import feedparser
import httpx

from digest import health
from digest.dedup import bare_host, is_aggregator
from digest.models import Item

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; ai-digest/0.1)"
DEFAULT_MAX_AGE_HOURS = 48
DATE_TOKEN = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, \d{4}"
TAG = re.compile(r"<[^>]+>")
# Метка пре-релиза — суффикс сразу за цифрой версии (v1.2.3-rc1, 0.164.0-alpha.9, v0.67.0-nightly.…), а не слово
# в заголовке: стабильный «v0.66.0 — preview features» не должен выпасть
PRERELEASE = re.compile(r"\d[-.]?(rc|alpha|beta|dev|pre|preview|nightly)\d*\b", re.I)
HREF = re.compile(r'href="(https?://[^"]+)"', re.I)


def _text(html: str) -> str:
    return " ".join(unescape(TAG.sub(" ", html)).split())


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _original_link(link: str, summary_html: str) -> str:
    """Techmeme ведёт на свою страницу-подборку; статья-оригинал — первая внешняя ссылка в описании.
    Ссылка на главную издания (путь «/») — подпись к статье, а не она сама."""
    own = urlsplit(link).netloc
    for href in map(unescape, HREF.findall(summary_html)):
        parts = urlsplit(href)
        if parts.netloc != own and parts.path.strip("/"):
            return href
    return link


def parse_feed(body: bytes, src: dict) -> list[Item]:
    items = []
    prefix = src.get("title_prefix")
    entries = feedparser.parse(body).entries
    if not entries:
        # Лента хранит историю, пустой она не бывает: адрес отдаёт заглушку или HTML. feedparser на это не падает,
        # и без исключения источник молча выпал бы из выпусков
        raise ValueError(f"{src['name']}: в ленте ни одной записи")
    for entry in entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        title = _text(entry.get("title", ""))
        source, summary = src["name"], _text(entry.get("summary", ""))[:600]
        publisher = _text(entry.get("source", {}).get("title", ""))
        if src.get("publisher_in_title") and publisher:
            # Google News: «Заголовок - Издание». Издание становится источником, чтобы охват считался по изданиям;
            # описание у него — список ссылок на те же статьи, а не текст
            title, source, summary = title.removesuffix(f" - {publisher}"), publisher, ""
        url = entry.get("link", "")
        content = entry.get("content") or [{}]
        full = _text(content[0].get("value", ""))[:ARTICLE_LIMIT] if src.get("body_from_feed") else ""
        if src.get("original_link"):
            url = _original_link(url, entry.get("summary", ""))
        items.append(Item(
            source=source,
            title=f"{prefix} {title}" if prefix else title,
            url=url,
            category=src["category"],
            published=datetime.fromtimestamp(timegm(stamp), timezone.utc) if stamp else None,
            summary=summary,
            body=full,
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
            hn_points=hit.get("points") or 0,
            hn_comments=hit.get("num_comments") or 0,
            hn_url=f"https://news.ycombinator.com/item?id={hit['objectID']}",
        ))
    return items


HN_SEARCH = "https://hn.algolia.com/api/v1/search"


def parse_discussion(payload: dict) -> tuple[int, int, str] | None:
    hits = [h for h in payload.get("hits", []) if h.get("points")]
    if not hits:
        return None
    top = max(hits, key=lambda h: h["points"])
    return top["points"], top.get("num_comments") or 0, f"https://news.ycombinator.com/item?id={top['objectID']}"


def attach_discussions(items: list[Item]) -> None:
    """Ищет тред на HN по ссылке события: так видно обсуждение и у новостей, пришедших не из HN."""
    with httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}) as client:
        for item in items:
            try:
                response = client.get(HN_SEARCH, params={"query": item.url, "tags": "story",
                                                         "restrictSearchableAttributes": "url"})
                response.raise_for_status()
                found = parse_discussion(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                log.error("обсуждение на HN для %s не найдено: %s", item.url, exc)
                continue
            if found and found[0] > item.hn_points:
                item.hn_points, item.hn_comments, item.hn_url = found


GNEWS_ARTICLE = "https://news.google.com/rss/articles/{}"
GNEWS_BATCH = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
GNEWS_SIGNATURE = re.compile(r'data-n-a-sg="([^"]+)"')
GNEWS_TIMESTAMP = re.compile(r'data-n-a-ts="(\d+)"')


def _gnews_form(article_id: str, page: str) -> str | None:
    """Тело запроса, которым страница Google News сама узнаёт адрес статьи; подпись и время — из её разметки."""
    signature, stamp = GNEWS_SIGNATURE.search(page), GNEWS_TIMESTAMP.search(page)
    if not (signature and stamp):
        return None
    context = [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
               "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0]
    inner = json.dumps(["garturlreq", context, article_id, int(stamp.group(1)), signature.group(1)], separators=(",", ":"))
    return urlencode({"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"))})


def _gnews_target(reply: str) -> str:
    # Ответ: строка-заглушка «)]}'», пустая строка, JSON; адрес статьи — во вложенном JSON третьего поля
    return json.loads(json.loads(reply.split("\n\n", 1)[1])[0][2])[1]


def resolve_gnews(items: list[Item]) -> None:
    """Ссылки Google News открываются только через JS: без адреса статьи у события нет ни текста для разбора,
    ни обсуждения на HN. Узнаём адрес тем же внутренним запросом, что делает их страница, — только для событий,
    уже попавших в выпуск: десяток запросов, а не сотня. API недокументированный, поэтому при любом сбое
    ссылка остаётся прежней и выпуск выходит как раньше."""
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for item in items:
            if bare_host(item.url) != "news.google.com":
                continue
            article_id = urlsplit(item.url).path.rsplit("/", 1)[-1]
            try:
                page = client.get(GNEWS_ARTICLE.format(article_id))
                page.raise_for_status()
                form = _gnews_form(article_id, page.text)
                if form is None:
                    log.error("ссылка Google News не раскодирована: на странице нет подписи (%s)", item.url)
                    continue
                reply = client.post(GNEWS_BATCH, content=form,
                                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
                reply.raise_for_status()
                target = _gnews_target(reply.text)
                # Внутри try: urlsplit сам бросает ValueError на адресах вроде «https://[broken»
                valid = isinstance(target, str) and urlsplit(target).scheme in ("http", "https") and not is_aggregator(target)
            except (httpx.HTTPError, ValueError, LookupError, TypeError) as exc:
                # LookupError — и KeyError, и IndexError: ответ другой структуры не должен сорвать выпуск
                log.error("ссылка Google News не раскодирована: %s (%s)", type(exc).__name__, item.url)
                continue
            except Exception:
                # Раскодирование — необязательное улучшение поверх недокументированного API: перечислить все способы,
                # которыми чужой ответ ломает разбор, нельзя (так нашлись KeyError, ValueError, RecursionError).
                # Любой сбой оставляет прежнюю ссылку; traceback в логе, чтобы не спрятать ошибку в нашем коде
                log.error("неожиданный сбой раскодирования ссылки Google News (%s)", item.url, exc_info=True)
                continue
            if not valid:
                log.error("Google News вернул не адрес статьи: %r (%s)", str(target)[:100], item.url)
                continue
            item.url = target


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
    items = [Item(source=src["name"], title=t, url=u, category=src["category"]) for u, t in titles.items() if t]
    if not items:
        # На странице новостей ссылки есть всегда; ноль значит, что блог сменил вёрстку и link_pattern устарел
        raise ValueError(f"{src['name']}: на странице нет ссылок по link_pattern")
    return items


NOISE_BLOCKS = re.compile(r"<(script|style|noscript|svg|nav|header|footer|aside|form)\b.*?</\1>", re.S | re.I)
MAIN_BLOCK = re.compile(r"<(article|main)\b[^>]*>(.*)</\1>", re.S | re.I)
ARTICLE_LIMIT = 8000
# Короче — заглушка («Google News», «включите JavaScript»), а не статья: модель получит «текст недоступен»
# и не станет додумывать по мусору
MIN_ARTICLE_CHARS = 300


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
            if is_aggregator(url):
                texts[url] = ""
                continue
            try:
                response = client.get(url)
                response.raise_for_status()
                text = extract_text(response.text)
                texts[url] = text if len(text) >= MIN_ARTICLE_CHARS else ""
            except httpx.HTTPError as exc:
                log.error("текст первоисточника %s не получен: %s", url, exc)
                texts[url] = ""
    return texts


def fill_from_feed(texts: dict[str, str], items: list[Item]) -> None:
    """Страница не отдалась — берём полный текст из ленты, если он есть. Полученная статья важнее: в ленте бывает
    обрезанная версия."""
    for item in items:
        if item.body and not texts.get(item.url):
            texts[item.url] = item.body


def _fetch_one(client: httpx.Client, src: dict, now: datetime) -> list[Item]:
    kind = src["type"]
    if kind == "hn":
        since = int((now - timedelta(hours=src.get("max_age_hours", DEFAULT_MAX_AGE_HOURS))).timestamp())
        params: dict[str, str | int] = {"tags": src.get("tags", "story"), "hitsPerPage": 200,
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
                health.record_source(src["name"], 0, type(exc).__name__)
                continue
            cutoff = now - timedelta(hours=src.get("max_age_hours", DEFAULT_MAX_AGE_HOURS))
            fresh = [i for i in fetched if i.url and (i.published is None or i.published >= cutoff)]
            for item in fresh:
                item.feed_host = bare_host(src["url"])
            log.info("%s: %d из %d свежих", src["name"], len(fresh), len(fetched))
            health.record_source(src["name"], len(fresh))
            items.extend(fresh)
    return items, failures
