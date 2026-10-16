from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

from digest.dedup import ARXIV_ID
from digest.llm import Digest, Entry

TEMPLATE = Template((Path(__file__).parent / "templates" / "page.html").read_text(encoding="utf-8"))
# Читатель в Ташкенте: даты на странице — по его времени, а не по UTC раннера
DISPLAY_TZ = timezone(timedelta(hours=5))
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
SECTION_LABELS = {"model": "Модель", "release": "Релиз", "paper": "Статья", "industry": "Индустрия"}
# (секция, якорь, заголовок, css-класс цвета иконки во вкладках)
ROW_SECTIONS = [("model", "models", "Модели", "t1"), ("release", "releases", "Релизы", "t2"),
                ("industry", "industry", "Индустрия", "t4")]

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{}</svg>'
ICONS = {
    "main-story": _SVG.format('<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>'),
    "models": _SVG.format('<path d="M12 3l1.8 4.7L18 9.5l-4.2 1.8L12 16l-1.8-4.7L6 9.5l4.2-1.8z"/><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>'),
    "releases": _SVG.format('<path d="M8 6l-6 6 6 6M16 6l6 6-6 6"/>'),
    "papers": _SVG.format('<path d="M6 3h9l5 5v13H6zM14 3v6h6"/>'),
    "industry": _SVG.format('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
    "noise": _SVG.format('<path d="M3 5h18l-7 8v6l-4 2v-8z"/>'),
}
ARROW = "→"


def _href(url: str) -> str:
    # Ссылки приходят из чужих лент: javascript:/data: в href исполнились бы по клику
    return escape(url) if urlsplit(url).scheme in ("http", "https") else "#"


def human_date(when: datetime) -> str:
    local = when.astimezone(DISPLAY_TZ)
    return f"{local.day} {MONTHS[local.month - 1]}"


def _host(url: str) -> str:
    return urlsplit(url).netloc.removeprefix("www.")


def _also(entry: Entry) -> str:
    names = list(dict.fromkeys(s for s, _ in entry.item.also))
    return f"также: {escape(', '.join(names))}" if names else ""


def _why(entry: Entry) -> str:
    return f'<div class="why"><b>Зачем тебе:</b> {escape(entry.why)}</div>' if entry.why else ""


def _more(entry: Entry) -> str:
    source = f'<a class="pill" href="{_href(entry.item.url)}">Первоисточник {ARROW}</a>'
    d = entry.details
    if d is None:
        return f'<div class="more-links">{source}</div>'
    points = "".join(f"<li>{escape(p)}</li>" for p in d.points)
    parts = [f"<p>{escape(d.what)}</p>"]
    if points:
        parts.append(f'<div class="more-label">Ключевые детали</div><ul>{points}</ul>')
    if d.try_it:
        parts.append(f'<div class="more-label">Как попробовать</div><div class="code">{escape(d.try_it)}</div>')
    if d.caveats:
        parts.append(f'<div class="caveat"><b>Ограничения:</b> {escape(d.caveats)}</div>')
    parts.append(f'<div class="more-links">{source}</div>')
    return f'<details class="more"><summary>Подробнее</summary><div class="more-body">{"".join(parts)}</div></details>'


def _nav(anchor: str, label: str, count: int | None = None, active: bool = False) -> str:
    badge = f'<span class="count">{count}</span>' if count is not None else ""
    css = ' class="on"' if active else ""
    return f'<a{css} href="#{anchor}">{ICONS[anchor]}{label}{badge}</a>'


def _hero(entry: Entry) -> str:
    share = round(entry.importance * 10)
    also = f"<span>{_also(entry)}</span>" if entry.item.also else ""
    return (
        f'        <section class="card hero" id="main-story">\n'
        f'          <div class="hero-top"><span class="chip">Главное</span>'
        f'<a class="btn" href="{_href(entry.item.url)}">Первоисточник {ARROW}</a></div>\n'
        f'          <h2>{escape(entry.title)}</h2>\n          <p>{escape(entry.summary)}</p>\n          {_why(entry)}\n'
        f'          <div class="meter"><div class="meter-row">Важность<span>{entry.importance:g} / 10</span></div>'
        f'<div class="bar"><i style="width:{share}%"></i></div></div>\n'
        f'          <div class="legend"><span>{SECTION_LABELS[entry.section]} · {escape(_host(entry.item.url))}</span>{also}</div>\n'
        f'          {_more(entry)}\n'
        f'        </section>')


def _item(n: int, entry: Entry) -> str:
    hot = " hot" if entry.importance >= 8 else ""
    also = f'<span>{_also(entry)}</span>' if entry.item.also else ""
    importance_css = "tag green" if entry.importance >= 8 else "tag"
    return (f'<div class="item"><span class="num{hot}">{n}</span>'
            f'<div><h4>{escape(entry.title)}</h4><p>{escape(entry.summary)}</p>{_why(entry)}'
            f'<div class="meta"><span class="{importance_css}">важность {entry.importance:g}</span>'
            f'<span>{escape(_host(entry.item.url))}</span>{also}</div>{_more(entry)}</div></div>')


def _paper(entry: Entry) -> str:
    match = ARXIV_ID.search(entry.item.url)
    ident = f"arXiv:{match.group(1)}" if match else _host(entry.item.url)
    votes = f'<span class="tag green">{entry.item.score} ▲</span>' if entry.item.score else ""
    return (f'<div class="paper"><h4>{escape(entry.title)}</h4><p>{escape(entry.summary)}</p>'
            f'<div class="paper-foot">{votes}<a class="key" href="{_href(entry.item.url)}">{escape(ident)} {ARROW}</a></div>'
            f'{_more(entry) if entry.details else ""}</div>')


def _tabs(groups: list[tuple[str, str, str, int]]) -> str:
    links = "".join(f'<a href="#{anchor}"{" class=\"on\"" if i == 0 else ""}><span class="{tone}">{ICONS[anchor]}</span>'
                    f'{label} <small>{count}</small></a>'
                    for i, (anchor, label, tone, count) in enumerate(groups))
    return f'        <nav class="card tabs" aria-label="Разделы">{links}</nav>'


def render_page(digest: Digest, stats: dict, when: datetime, failures: list[str]) -> str:
    top, rest = (digest.entries[0], digest.entries[1:]) if digest.entries else (None, [])
    main = [_hero(top)] if top else []
    nav = [_nav("main-story", "Главное", active=True)] if top else []
    tabs = []

    for section, anchor, label, tone in ROW_SECTIONS:
        group = [e for e in rest if e.section == section]
        if not group:
            continue
        items = "".join(_item(n, e) for n, e in enumerate(group, start=1))
        main.append(f'        <section class="card" id="{anchor}">\n'
                    f'          <div class="card-head"><h3>{label}</h3><small>{len(group)}</small></div>\n'
                    f'          <div class="card-body">{items}</div>\n        </section>')
        nav.append(_nav(anchor, label, len(group)))
        tabs.append((anchor, label, tone, len(group)))

    aside = []
    papers = [e for e in rest if e.section == "paper"]
    if papers:
        aside.append(f'        <section class="card" id="papers">\n'
                     f'          <div class="card-head"><h3>Статьи</h3><small>HF Daily Papers</small></div>\n'
                     f'          <div class="card-body">{"".join(_paper(e) for e in papers)}</div>\n        </section>')
        nav.append(_nav("papers", "Статьи", len(papers)))
        tabs.append(("papers", "Статьи", "t3", len(papers)))

    if digest.noise:
        by_source = Counter(i.source for i in digest.noise)
        noise_rows = "".join(f'<li><a href="{_href(i.url)}">{escape(i.title)}</a> · {escape(i.source)}</li>'
                             for i in digest.noise)
        sources = ", ".join(f"{escape(s)} {n}" for s, n in by_source.most_common(5))
        aside.append(f'        <section class="card noise" id="noise">\n          <h3>Отсеяно как шум: {len(digest.noise)}</h3>\n'
                     f'          <p>Больше всего: {sources}</p>\n'
                     f'          <details><summary>Показать отсеянное</summary><ul>{noise_rows}</ul></details>\n'
                     f'        </section>')
        nav.append(_nav("noise", "Отсеяно", len(digest.noise)))

    local = when.astimezone(DISPLAY_TZ)
    footer = [f"<span>Собрано {local:%d.%m} в {local:%H:%M}</span>", "<span>Стоимость 0 ₽</span>"]
    if failures:
        footer.append(f'<span class="fail">Не собрались: {escape(", ".join(f.split(":")[0] for f in failures))}</span>')

    description = f"{len(digest.entries)} событий из {stats['raw']} записей. {digest.lead}"
    return TEMPLATE.substitute(
        headline=escape(digest.headline), description=escape(description[:300]), lead=escape(digest.lead),
        date=human_date(when), short_date=f"{local:%d.%m}", weekday=WEEKDAYS[local.weekday()],
        nav="".join(nav), tabs=_tabs(tabs) if len(tabs) > 1 else "", main="\n".join(main), aside="\n".join(aside),
        footer="".join(footer), raw=stats["raw"], new=stats["merged"], selected=len(digest.entries),
    )
