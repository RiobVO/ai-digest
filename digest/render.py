import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
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
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
SECTION_LABELS = {"model": "Модель", "release": "Релиз", "paper": "Статья", "industry": "Индустрия"}
# (секция, якорь, заголовок, css-класс цвета иконки во вкладках)
ROW_SECTIONS = [("model", "models", "Модели", "t1"), ("release", "releases", "Релизы", "t2"),
                ("industry", "industry", "Индустрия", "t4")]
ARCHIVE_IN_SIDEBAR = 7

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{}</svg>'
ICONS = {
    "main-story": _SVG.format('<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>'),
    "models": _SVG.format('<path d="M12 3l1.8 4.7L18 9.5l-4.2 1.8L12 16l-1.8-4.7L6 9.5l4.2-1.8z"/><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>'),
    "releases": _SVG.format('<path d="M8 6l-6 6 6 6M16 6l6 6-6 6"/>'),
    "papers": _SVG.format('<path d="M6 3h9l5 5v13H6zM14 3v6h6"/>'),
    "industry": _SVG.format('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
    "noise": _SVG.format('<path d="M3 5h18l-7 8v6l-4 2v-8z"/>'),
    "archive": _SVG.format('<path d="M3 5h18v4H3zM5 9v10h14V9M10 13h4"/>'),
}
ARROW = "→"

# Кнопки оценок шлют POST в воркер обратной связи; выбор запоминается в браузере, чтобы не мигал при перезагрузке
VOTE_SCRIPT = """<script>
(() => {
  const FB = %s;
  const mark = (box, v) => box.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  document.querySelectorAll(".vote").forEach(box => {
    const id = box.dataset.id;
    try { const saved = localStorage.getItem("v:" + id); if (saved) mark(box, saved); } catch (e) {}
    box.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      box.classList.remove("err");
      fetch(FB + "/vote?e=" + encodeURIComponent(id) + "&v=" + b.dataset.v, {method: "POST"})
        .then(r => { if (!r.ok) throw new Error(r.status); mark(box, b.dataset.v);
                     try { localStorage.setItem("v:" + id, b.dataset.v); } catch (e) {} })
        .catch(() => box.classList.add("err"));
    }));
  });
})();
</script>"""


def _href(url: str) -> str:
    # Ссылки приходят из чужих лент: javascript:/data: в href исполнились бы по клику
    return escape(url) if urlsplit(url).scheme in ("http", "https") else "#"


def human_date(when: datetime) -> str:
    local = when.astimezone(DISPLAY_TZ)
    return f"{local.day} {MONTHS[local.month - 1]}"


def short_day(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.day} {MONTHS_SHORT[d.month - 1]}"


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def _host(url: str) -> str:
    return urlsplit(url).netloc.removeprefix("www.")


def _also(entry: Entry) -> str:
    names = list(dict.fromkeys(s for s, _ in entry.item.also))
    return f"также: {escape(', '.join(names))}" if names else ""


def _why(entry: Entry) -> str:
    return f'<div class="why"><b>Зачем тебе:</b> {escape(entry.why)}</div>' if entry.why else ""


def _vote(entry: Entry, enabled: bool) -> str:
    if not enabled or not entry.id:
        return ""
    return (f'<span class="vote" data-id="{escape(entry.id)}">'
            f'<button type="button" data-v="up" aria-label="Полезно">👍</button>'
            f'<button type="button" data-v="down" aria-label="Мимо">👎</button></span>')


def _story(entry: Entry) -> str:
    # Первый день сюжета ничем не отличается от обычной новости — метку показываем со второго
    if entry.story_day < 2:
        return ""
    prev = "".join(f"<li><b>{short_day(day)}</b> {escape(title)}</li>" for day, title in entry.story_prev)
    return (f'<div class="story"><span class="tag story-tag">Сюжет · {escape(entry.story_name)} · {entry.story_day}-й день</span>'
            f'{f"<ul>{prev}</ul>" if prev else ""}</div>')


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


def _hero(entry: Entry, votes: bool, chip: str) -> str:
    share = round(entry.importance * 10)
    also = f"<span>{_also(entry)}</span>" if entry.item.also else ""
    return (
        f'        <section class="card hero" id="main-story">\n'
        f'          <div class="hero-top"><span class="chip">{chip}</span>'
        f'<a class="btn" href="{_href(entry.item.url)}">Первоисточник {ARROW}</a></div>\n'
        f'          <h2>{escape(entry.title)}</h2>\n          {_story(entry)}\n          <p>{escape(entry.summary)}</p>\n'
        f'          {_why(entry)}\n'
        f'          <div class="meter"><div class="meter-row">Важность<span>{entry.importance:g} / 10</span></div>'
        f'<div class="bar"><i style="width:{share}%"></i></div></div>\n'
        f'          <div class="legend"><span>{SECTION_LABELS[entry.section]} · {escape(_host(entry.item.url))}</span>{also}</div>\n'
        f'          <div class="hero-actions">{_more(entry)}{_vote(entry, votes)}</div>\n'
        f'        </section>')


def _item(n: int, entry: Entry, votes: bool) -> str:
    hot = " hot" if entry.importance >= 8 else ""
    also = f'<span>{_also(entry)}</span>' if entry.item.also else ""
    importance_css = "tag green" if entry.importance >= 8 else "tag"
    return (f'<div class="item"><span class="num{hot}">{n}</span>'
            f'<div><h4>{escape(entry.title)}</h4>{_story(entry)}<p>{escape(entry.summary)}</p>{_why(entry)}'
            f'<div class="meta"><span class="{importance_css}">важность {entry.importance:g}</span>'
            f'<span>{escape(_host(entry.item.url))}</span>{also}{_vote(entry, votes)}</div>{_more(entry)}</div></div>')


def _paper(entry: Entry, votes: bool) -> str:
    match = ARXIV_ID.search(entry.item.url)
    ident = f"arXiv:{match.group(1)}" if match else _host(entry.item.url)
    score = f'<span class="tag green">{entry.item.score} ▲</span>' if entry.item.score else ""
    return (f'<div class="paper"><h4>{escape(entry.title)}</h4>{_story(entry)}<p>{escape(entry.summary)}</p>'
            f'<div class="paper-foot">{score}{_vote(entry, votes)}'
            f'<a class="key" href="{_href(entry.item.url)}">{escape(ident)} {ARROW}</a></div>'
            f'{_more(entry) if entry.details else ""}</div>')


def _tabs(groups: list[tuple[str, str, str, int]]) -> str:
    links = "".join(f'<a href="#{anchor}"{" class=\"on\"" if i == 0 else ""}><span class="{tone}">{ICONS[anchor]}</span>'
                    f'{label} <small>{count}</small></a>'
                    for i, (anchor, label, tone, count) in enumerate(groups))
    return f'        <nav class="card tabs" aria-label="Разделы">{links}</nav>'


def _archive_sidebar(issues: list[dict] | None, current: str = "") -> str:
    if not issues:
        return ""
    links = "".join(
        f'<a href="{escape(i["page"])}"{" class=\"on\"" if i["page"] == current else ""}>'
        f'<b>{short_day(i["date"])}</b><span>{escape(i["headline"])}</span></a>'
        for i in issues[:ARCHIVE_IN_SIDEBAR])
    return (f'<div class="arch"><div class="arch-title">Архив</div>{links}'
            f'<a class="arch-all" href="archive.html">Все выпуски {ARROW}</a></div>')


def _summary(title: str, sub: str, lead: str, cards: list[tuple[int, str]]) -> str:
    tiles = "".join(f'<div class="stat"><b>{value}</b><span>{label}</span></div>' for value, label in cards)
    lead_html = f'<p class="lead">{escape(lead)}</p>' if lead else ""
    return (f'        <section class="card">\n          <div class="card-head"><h3>{title}</h3><small>{sub}</small></div>\n'
            f'          {lead_html}\n          <div class="stats">{tiles}</div>\n        </section>')


def render_page(digest: Digest, stats: dict, when: datetime, failures: list[str], *,
                feedback_url: str = "", issues: list[dict] | None = None, kind: str = "day", period: str = "",
                page_name: str = "") -> str:
    votes = bool(feedback_url)
    top, rest = (digest.entries[0], digest.entries[1:]) if digest.entries else (None, [])
    main = [_hero(top, votes, "Главное" if kind == "day" else "Сюжет недели №1")] if top else []
    nav = [_nav("main-story", "Главное", active=True)] if top else []
    tabs = []

    for section, anchor, label, tone in ROW_SECTIONS:
        group = [e for e in rest if e.section == section]
        if not group:
            continue
        items = "".join(_item(n, e, votes) for n, e in enumerate(group, start=1))
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
                     f'          <div class="card-body">{"".join(_paper(e, votes) for e in papers)}</div>\n        </section>')
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
    if kind == "week":
        crumbs = f"Итог недели<span>/</span>{escape(period)}"
        summary = _summary("Неделя", escape(period), digest.lead,
                           [(n, plural(n, one, few, many).split(" ", 1)[1]) for n, (one, few, many) in (
                               (stats["issues"], ("выпуск", "выпуска", "выпусков")),
                               (stats["events"], ("событие", "события", "событий")),
                               (len(digest.entries), ("сюжет", "сюжета", "сюжетов")))])
    else:
        crumbs = f"Дайджест<span>/</span>{human_date(when)}"
        summary = _summary("Фильтр за сутки", human_date(when), digest.lead,
                           [(stats["raw"], "собрано"), (stats["merged"], "новых"), (len(digest.entries), "в выпуске")])

    footer = [f"<span>Собрано {local:%d.%m} в {local:%H:%M}</span>", "<span>Стоимость 0 ₽</span>"]
    if failures:
        footer.append(f'<span class="fail">Не собрались: {escape(", ".join(f.split(":")[0] for f in failures))}</span>')

    description = f"{len(digest.entries)} событий. {digest.lead}"
    return TEMPLATE.substitute(
        headline=escape(digest.headline), description=escape(description[:300]), crumbs=crumbs,
        short_date=f"{local:%d.%m}", weekday=WEEKDAYS[local.weekday()] if kind == "day" else "Неделя",
        nav="".join(nav), archive=_archive_sidebar(issues, page_name),
        tabs=_tabs(tabs) if len(tabs) > 1 else "", main="\n".join(main), summary=summary, aside="\n".join(aside),
        footer="".join(footer), script=VOTE_SCRIPT % json.dumps(feedback_url) if votes else "",
    )


def render_archive(issues: list[dict], when: datetime) -> str:
    cards = "".join(
        f'<a class="item issue-link" href="{escape(i["page"])}"><span class="num">{short_day(i["date"]).split()[0]}</span>'
        f'<div><h4>{escape(i["headline"])}</h4><div class="meta"><span class="tag">{short_day(i["date"])}</span>'
        f'<span>{plural(len(i["entries"]), "событие", "события", "событий")}</span></div></div></a>'
        for i in issues)
    main = (f'        <section class="card" id="main-story">\n          <div class="card-head"><h3>Все выпуски</h3>'
            f'<small>{len(issues)}</small></div>\n          <div class="card-body">{cards}</div>\n        </section>')
    local = when.astimezone(DISPLAY_TZ)
    return TEMPLATE.substitute(
        headline="Архив выпусков", description=f"{len(issues)} выпусков ИИ-дайджеста", crumbs="Дайджест<span>/</span>Архив",
        short_date=f"{local:%d.%m}", weekday="Архив", nav=f'<a class="on" href="#main-story">{ICONS["archive"]}Все выпуски</a>',
        archive=_archive_sidebar(issues), tabs="", main=main,
        summary=_summary("Архив", "", "", [(len(issues), "выпусков"), (sum(len(i["entries"]) for i in issues), "событий")]),
        aside="", footer="<span>Стоимость 0 ₽</span>", script="",
    )
