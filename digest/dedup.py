import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit

from digest.models import Item

ARXIV_ID = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|huggingface\.co/papers/)(\d{4}\.\d{4,5})")
TRACKING = re.compile(r"^(utm_.*|ref|ref_src|fbclid|gclid|source)$")
TITLE_THRESHOLD = 0.88
# Страницы-подборки, а не статьи: у Google News ссылка открывается только через JS, у Techmeme — чужие сюжеты вперемешку
AGGREGATOR_HOSTS = {"news.google.com", "techmeme.com"}


def canonical_key(url: str) -> str:
    match = ARXIV_ID.search(url)
    if match:
        return f"arxiv:{match.group(1)}"
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)))
    return f"{host}{path}" + (f"?{query}" if query else "")


def bare_host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def is_aggregator(url: str) -> bool:
    return bare_host(url) in AGGREGATOR_HOSTS


def norm_title(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", title.lower()).split())


def absorb(primary: Item, dup: Item) -> None:
    primary.also.append((dup.source, dup.url))
    primary.also.extend(dup.also)
    if dup.score is not None and (primary.score is None or dup.score > primary.score):
        primary.score = dup.score
    if not primary.summary:
        primary.summary = dup.summary
    if dup.hn_points > primary.hn_points:
        primary.hn_points, primary.hn_comments, primary.hn_url = dup.hn_points, dup.hn_comments, dup.hn_url


def coverage(item: Item) -> int:
    """Сколько разных источников написали о событии — главный бесплатный признак, что его обсуждают все."""
    return len({item.source, *(source for source, _ in item.also)})


def dedupe(items: list[Item]) -> list[Item]:
    """Склеивает дубли; первый встреченный элемент остаётся основным, порядок входа = приоритет.
    Исключение — агрегатор: он уступает место прямой ссылке на ту же статью."""
    by_key: dict[str, Item] = {}
    for item in items:
        key = canonical_key(item.url)
        if key in by_key:
            absorb(by_key[key], item)
        else:
            by_key[key] = item

    result: list[Item] = []
    for item in by_key.values():
        title = norm_title(item.title)
        for n, kept in enumerate(result):
            # Внутри одного источника похожие заголовки — разные записи (v0.12.0 и v0.12.1)
            if kept.source == item.source:
                continue
            if SequenceMatcher(None, title, norm_title(kept.title)).ratio() >= TITLE_THRESHOLD:
                # Google News стоит в списке выше изданий и индексирует их же статьи: иначе основной осталась бы
                # ссылка, которая не открывается без JS, а прямая ушла бы в «также»
                if is_aggregator(kept.url) and not is_aggregator(item.url):
                    absorb(item, kept)
                    result[n] = item
                else:
                    absorb(kept, item)
                break
        else:
            result.append(item)
    return result
