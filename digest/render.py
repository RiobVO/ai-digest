import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

from digest.dedup import ARXIV_ID, coverage
from digest.llm import Digest, Entry
from digest.models import Item

TEMPLATE = Template((Path(__file__).parent / "templates" / "page.html").read_text(encoding="utf-8"))
# Читатель в Ташкенте: даты на странице — по его времени, а не по UTC раннера
DISPLAY_TZ = timezone(timedelta(hours=5))
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
SECTION_LABELS = {"product": "Компании и продукты", "model": "Модели и исследования", "business": "Деньги и законы",
                  "dev": "Для тебя", "paper": "Статья"}
# (раздел, якорь, заголовок раздела, короткое имя для бокового меню)
ROW_SECTIONS = [("product", "products", "Компании и продукты", "Компании"),
                ("model", "models", "Модели и исследования", "Модели"),
                ("business", "business", "Деньги, законы, люди", "Деньги и законы"), ("dev", "dev", "Для тебя", "Для тебя")]
ARCHIVE_IN_SIDEBAR = 7
ALSO_SHOWN = 4
# Читаемые имена служебных id источников из sources.toml; у Google News источник уже равен названию издания
SOURCE_LABELS = {
    "openai": "OpenAI", "anthropic": "Anthropic", "deepmind": "DeepMind", "google_ai": "Google", "google_research": "Google Research",
    "meta_ai": "Meta AI", "nvidia": "NVIDIA", "apple_ml": "Apple ML", "mistral": "Mistral", "hf_blog": "Hugging Face",
    "techmeme": "Techmeme", "hf_papers": "HF Papers", "gh_vllm": "GitHub · vLLM", "gh_ollama": "GitHub · Ollama",
    "gh_llama_cpp": "GitHub · llama.cpp", "gh_transformers": "GitHub · transformers", "hn": "Hacker News",
    "hn_top": "Hacker News", "r_localllama": "Reddit · LocalLLaMA", "techcrunch": "TechCrunch", "verge": "The Verge",
    "ars_ai": "Ars Technica", "wired_ai": "Wired", "mit_tr_ai": "MIT Technology Review", "the_decoder": "The Decoder",
    "import_ai": "Import AI", "last_week_in_ai": "Last Week in AI", "simon_willison": "Simon Willison",
    "latent_space": "Latent Space", "interconnects": "Interconnects",
}
# Тред с парой баллов — не обсуждение, а чей-то репост; такой не показываем
HN_MIN_SHOWN = 20
# Средняя скорость чтения русского текста с экрана, слов в минуту
WORDS_PER_MINUTE = 180

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{}</svg>'
ICONS = {
    "main-story": _SVG.format('<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>'),
    "models": _SVG.format('<path d="M12 3l1.8 4.7L18 9.5l-4.2 1.8L12 16l-1.8-4.7L6 9.5l4.2-1.8z"/><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>'),
    "products": _SVG.format('<path d="M4 7h16v13H4zM9 7V4h6v3"/>'),
    "dev": _SVG.format('<path d="M8 6l-6 6 6 6M16 6l6 6-6 6"/>'),
    "papers": _SVG.format('<path d="M6 3h9l5 5v13H6zM14 3v6h6"/>'),
    "business": _SVG.format('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
    "noise": _SVG.format('<path d="M3 5h18l-7 8v6l-4 2v-8z"/>'),
    "archive": _SVG.format('<path d="M3 5h18v4H3zM5 9v10h14V9M10 13h4"/>'),
    "up": _SVG.format('<path d="M7 10v11M15 5.9 14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 7A2 2 0 0 1 17.5 21H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h2.8a2 2 0 0 0 1.8-1.1L12 2a3.1 3.1 0 0 1 3 3.9z"/>'),
    "warn": _SVG.format('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/>'),
    "terminal": _SVG.format('<path d="M4 17l6-6-6-6M12 19h8"/>'),
    "down": _SVG.format('<path d="M17 14V3M9 18.1 10 14H4.2a2 2 0 0 1-1.9-2.6l2.3-7A2 2 0 0 1 6.5 3H20a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-2.8a2 2 0 0 0-1.8 1.1L12 22a3.1 3.1 0 0 1-3-3.9z"/>'),
}
TRY_TODAY_MAX = 4
# Без сторонних скриптов и сетевых запросов: листание карточек с клавиатуры и копирование команд
PAGE_SCRIPT = """<script>
(() => {
  document.querySelectorAll(".copy").forEach(b => b.addEventListener("click", () => {
    const code = b.closest(".term").querySelector("code").textContent;
    navigator.clipboard.writeText(code).then(() => {
      b.textContent = "Скопировано";
      setTimeout(() => { b.textContent = "Скопировать"; }, 1500);
    }, () => { b.textContent = "Не вышло"; });
  }));
})();
(() => {
  const cards = [...document.querySelectorAll(".hero, .item, .paper")];
  let at = -1;
  const go = (i) => {
    cards[at]?.classList.remove("kb");
    at = Math.max(0, Math.min(cards.length - 1, i));
    cards[at].classList.add("kb");
    cards[at].scrollIntoView({block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
  };
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || /input|textarea/i.test(e.target.tagName) || !cards.length) return;
    if (e.key === "j") go(at + 1);
    else if (e.key === "k") go(at - 1);
    else if (e.key === "o" && at >= 0) { const d = cards[at].querySelector("details.more"); if (d) d.open = !d.open; }
  });
})();
</script>"""
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
    names = list(dict.fromkeys(source_label(s) for s, _ in entry.item.also))
    # Громкую новость пишут десятки изданий — все не перечисляем, охват и так виден в плашке резонанса
    shown = ", ".join(names[:ALSO_SHOWN]) + (f" и ещё {len(names) - ALSO_SHOWN}" if len(names) > ALSO_SHOWN else "")
    return f"также: {escape(shown)}" if names else ""


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def _buzz(item: Item) -> str:
    parts = []
    reach = coverage(item)
    if reach > 1:
        parts.append(f'<span class="buzz">{plural(reach, "источник", "источника", "источников")}</span>')
    if item.hn_points >= HN_MIN_SHOWN:
        parts.append(f'<a class="buzz" href="{_href(item.hn_url)}">HN {item.hn_points} ▲ · '
                     f'{item.hn_comments} комм.</a>')
    return "".join(parts)


def _source(url: str) -> str:
    host = escape(_host(url))
    if urlsplit(url).scheme not in ("http", "https") or not host:
        return f"<span>{host}</span>"
    # Иконку отдаёт DuckDuckGo: он не ставит куки и не требует ключа; битая иконка с пустым alt просто не видна
    return (f'<span class="src"><img class="fav" src="https://icons.duckduckgo.com/ip3/{host}.ico" alt="" '
            f'width="14" height="14" loading="lazy">{host}</span>')


def reading_minutes(entries: list[Entry]) -> int:
    words = sum(len(f"{e.title} {e.summary} {e.why}".split()) for e in entries)
    return max(1, round(words / WORDS_PER_MINUTE))


def _why(entry: Entry) -> str:
    return f'<div class="why"><b>Почему важно:</b> {escape(entry.why)}</div>' if entry.why else ""


def _vote(entry: Entry, enabled: bool) -> str:
    if not enabled or not entry.id:
        return ""
    return (f'<span class="vote" data-id="{escape(entry.id)}">'
            f'<button type="button" data-v="up" aria-label="Полезно" title="Полезно">{ICONS["up"]}</button>'
            f'<button type="button" data-v="down" aria-label="Мимо" title="Мимо">{ICONS["down"]}</button></span>')


def _actions(entry: Entry, votes: bool, details: bool = True) -> str:
    # Оценки прижаты к строке «Подробнее» абсолютно: раскрытый разбор занимает всю ширину карточки
    more = _more(entry) if details else ""
    vote = _vote(entry, votes)
    return f'<div class="actions">{more}{vote}</div>' if more or vote else ""


def _story(entry: Entry) -> str:
    # Первый день сюжета ничем не отличается от обычной новости — метку показываем со второго
    if entry.story_day < 2:
        return ""
    prev = "".join(f"<li><b>{short_day(day)}</b> {escape(title)}</li>" for day, title in entry.story_prev)
    return (f'<div class="story"><span class="tag story-tag">Сюжет · {escape(entry.story_name)} · {entry.story_day}-й день</span>'
            f'{f"<ul>{prev}</ul>" if prev else ""}</div>')


def _term(commands: list[str]) -> str:
    return (f'<div class="term"><div class="term-bar">{ICONS["terminal"]}<span>Терминал</span>'
            f'<button type="button" class="copy">Скопировать</button></div>'
            f'<pre><code>{escape(chr(10).join(commands))}</code></pre></div>')


def _anchor(entry: Entry) -> str:
    return f' id="e-{escape(entry.id)}"' if entry.id else ""


def _more(entry: Entry) -> str:
    source = f'<a class="pill" href="{_href(entry.item.url)}">Первоисточник {ARROW}</a>'
    d = entry.details
    if d is None:
        return f'<div class="more-links">{source}</div>'
    parts = [f'<p class="more-lead">{escape(d.what)}</p>']
    if d.points:
        points = "".join(f"<li>{escape(p)}</li>" for p in d.points)
        parts.append(f'<section><div class="more-label">Ключевые детали</div><ol class="points">{points}</ol></section>')
    if d.try_it or d.commands:
        note = f'<p class="note">{escape(d.try_it)}</p>' if d.try_it else ""
        parts.append(f'<section><div class="more-label">Как попробовать</div>'
                     f'{_term(d.commands) if d.commands else ""}{note}</section>')
    if d.caveats:
        parts.append(f'<div class="caveat">{ICONS["warn"]}<div><b>Ограничения</b><p>{escape(d.caveats)}</p></div></div>')
    parts.append(f'<div class="more-links">{source}</div>')
    return f'<details class="more"><summary>Подробнее</summary><div class="more-body">{"".join(parts)}</div></details>'


def _try_today(entries: list[Entry]) -> str:
    rows, shown = [], set()
    for entry in entries:
        commands = entry.details.commands if entry.details else []
        # Общие команды вроде «pip install transformers» повторяются у разных новостей — показываем один раз
        commands = [c for c in commands if c not in shown]
        shown.update(commands)
        if commands and entry.id:
            rows.append(f'<div class="try"><a class="try-title" href="#e-{escape(entry.id)}">{escape(entry.title)}</a>'
                        f'{_term(commands[:2])}</div>')
    if not rows:
        return ""
    return (f'        <section class="card" id="try">\n          <div class="card-head"><h3>Попробовать сегодня</h3>'
            f'<small>из разборов</small></div>\n'
            f'          <div class="card-body">{"".join(rows[:TRY_TODAY_MAX])}</div>\n        </section>')


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
        f'          <h2{_anchor(entry)}>{escape(entry.title)}</h2>\n          {_story(entry)}\n          <p>{escape(entry.summary)}</p>\n'
        f'          {_why(entry)}\n'
        f'          <div class="meter"><div class="meter-row">Важность<span>{entry.importance:g} / 10</span></div>'
        f'<div class="bar"><i style="width:{share}%"></i></div></div>\n'
        f'          <div class="legend"><span>{SECTION_LABELS.get(entry.section, "Событие")}</span>{_source(entry.item.url)}'
        f'{_buzz(entry.item)}{also}</div>\n'
        f'          {_actions(entry, votes)}\n'
        f'        </section>')


def _item(n: int, entry: Entry, votes: bool) -> str:
    hot = " hot" if entry.importance >= 8 else ""
    also = f'<span>{_also(entry)}</span>' if entry.item.also else ""
    importance_css = "tag green" if entry.importance >= 8 else "tag"
    return (f'<div class="item"><span class="num{hot}">{n}</span>'
            f'<div><div class="meta"><span class="{importance_css}">важность {entry.importance:g}</span>'
            f'{_source(entry.item.url)}{_buzz(entry.item)}{also}</div>'
            f'<h4{_anchor(entry)}>{escape(entry.title)}</h4>{_story(entry)}<p>{escape(entry.summary)}</p>{_why(entry)}'
            f'{_actions(entry, votes)}</div></div>')


def _paper(entry: Entry, votes: bool) -> str:
    match = ARXIV_ID.search(entry.item.url)
    ident = f"arXiv:{match.group(1)}" if match else _host(entry.item.url)
    score = f'<span class="tag green">{entry.item.score} ▲</span>' if entry.item.score else ""
    return (f'<div class="paper"><div class="paper-foot">{score}'
            f'<a class="key" href="{_href(entry.item.url)}">{escape(ident)} {ARROW}</a></div>'
            f'<h4{_anchor(entry)}>{escape(entry.title)}</h4>{_story(entry)}<p>{escape(entry.summary)}</p>'
            f'{_actions(entry, votes, details=entry.details is not None)}</div>')


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

    for section, anchor, label, short in ROW_SECTIONS:
        group = [e for e in rest if e.section == section]
        if not group:
            continue
        items = "".join(_item(n, e, votes) for n, e in enumerate(group, start=1))
        main.append(f'        <section class="card" id="{anchor}">\n'
                    f'          <div class="card-head"><h3>{label}</h3><small>{len(group)}</small></div>\n'
                    f'          <div class="card-body">{items}</div>\n        </section>')
        nav.append(_nav(anchor, short, len(group)))

    aside = []
    papers = [e for e in rest if e.section == "paper"]
    if papers:
        aside.append(f'        <section class="card" id="papers">\n'
                     f'          <div class="card-head"><h3>Статьи</h3><small>HF Daily Papers</small></div>\n'
                     f'          <div class="card-body">{"".join(_paper(e, votes) for e in papers)}</div>\n        </section>')
        nav.append(_nav("papers", "Статьи", len(papers)))

    if digest.noise:
        by_source = Counter(i.source for i in digest.noise)
        noise_rows = "".join(f'<li><a href="{_href(i.url)}">{escape(i.title)}</a> · {escape(source_label(i.source))}</li>'
                             for i in digest.noise)
        sources = ", ".join(f"{escape(source_label(s))} {n}" for s, n in by_source.most_common(5))
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
        lead = ""
        funnel = []
    else:
        crumbs = f"Дайджест<span>/</span>{human_date(when)}"
        # Цифры воронки читателю почти ничего не дают — они уходят в подвал, а место справа занимают готовые команды
        summary = _try_today(digest.entries)
        lead = f'<p class="lead-top">{escape(digest.lead)}</p>' if digest.lead else ""
        funnel = [f'<span>Просмотрено {stats["raw"]}, новых {stats["merged"]}, в выпуске {len(digest.entries)}</span>']

    footer = [*funnel, f"<span>Собрано {local:%d.%m} в {local:%H:%M}</span>", "<span>Стоимость 0 ₽</span>"]
    if failures:
        footer.append(f'<span class="fail">Не собрались: {escape(", ".join(f.split(":")[0] for f in failures))}</span>')

    count = plural(len(digest.entries), *(("сюжет", "сюжета", "сюжетов") if kind == "week"
                                         else ("событие", "события", "событий")))
    description = f"{len(digest.entries)} событий. {digest.lead}"
    return TEMPLATE.substitute(
        headline=escape(digest.headline), description=escape(description[:300]), crumbs=crumbs, lead=lead,
        sub=f"{count} · ≈{reading_minutes(digest.entries)} мин чтения",
        short_date=f"{local:%d.%m}", weekday=WEEKDAYS[local.weekday()] if kind == "day" else "Неделя",
        nav="".join(nav), archive=_archive_sidebar(issues, page_name),
        main="\n".join(main), summary=summary, aside="\n".join(aside), footer="".join(footer),
        script=(VOTE_SCRIPT % json.dumps(feedback_url) if votes else "") + PAGE_SCRIPT,
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
        sub=plural(len(issues), "выпуск", "выпуска", "выпусков"), lead="",
        short_date=f"{local:%d.%m}", weekday="Архив", nav=f'<a class="on" href="#main-story">{ICONS["archive"]}Все выпуски</a>',
        archive=_archive_sidebar(issues), main=main,
        summary=_summary("Архив", "", "", [(len(issues), "выпусков"), (sum(len(i["entries"]) for i in issues), "событий")]),
        aside="", footer="<span>Стоимость 0 ₽</span>", script="",
    )
