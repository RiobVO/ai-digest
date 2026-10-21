import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

from digest import health
from digest.dedup import ARXIV_ID, coverage
from digest.llm import EXTRA_SECTIONS, Digest, Entry, Mention
from digest.models import Item

TEMPLATE = Template((Path(__file__).parent / "templates" / "page.html").read_text(encoding="utf-8"))
# Читатель в Ташкенте: даты на странице — по его времени, а не по UTC раннера
DISPLAY_TZ = timezone(timedelta(hours=5))
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
SECTION_LABELS = {"product": "Компании и продукты", "model": "Модели и исследования", "business": "Законы и цены",
                  "dev": "Для тебя", "paper": "Статья", "voices": "Инженеры", "security": "Безопасность",
                  "tips": "Фишка"}
# Стадия события из отбора (llm.STATUSES): анонс и доступный продукт — разные новости для читателя
STATUS_LABELS = {"announced": "анонс", "available": "доступно", "research": "исследование", "in_use": "применяют",
                 "rumor": "слух"}
# (раздел, якорь, заголовок раздела, короткое имя для бокового меню)
ROW_SECTIONS = [("product", "products", "Компании и продукты", "Компании"),
                ("model", "models", "Модели и исследования", "Модели"),
                ("business", "business", "Законы, цены, люди", "Законы и цены"), ("security", "security", "Безопасность", "Безопасность"),
                ("dev", "dev", "Для тебя", "Для тебя"),
                ("tips", "tips", "Фишки", "Фишки"),
                ("voices", "voices", "Что пишут инженеры", "Инженеры")]
BRIEF_LABELS = {section: short for section, _, _, short in ROW_SECTIONS} | {"paper": "Статья"}
ARCHIVE_IN_SIDEBAR = 7
ALSO_SHOWN = 4
# Читаемые имена служебных id источников из sources.toml; у Google News источник уже равен названию издания
SOURCE_LABELS = {
    "openai": "OpenAI", "anthropic": "Anthropic", "deepmind": "DeepMind", "google_ai": "Google", "google_research": "Google Research",
    "meta_ai": "Meta AI", "nvidia": "NVIDIA", "apple_ml": "Apple ML", "mistral": "Mistral", "hf_blog": "Hugging Face",
    "techmeme": "Techmeme", "hf_papers": "HF Papers", "gh_vllm": "GitHub · vLLM", "gh_ollama": "GitHub · Ollama",
    "gh_llama_cpp": "GitHub · llama.cpp", "gh_transformers": "GitHub · transformers",
    "gh_codex": "GitHub · Codex CLI", "gh_gemini_cli": "GitHub · Gemini CLI", "cursor": "Cursor", "hn": "Hacker News",
    "hn_top": "Hacker News", "hn_show": "Show HN", "r_localllama": "Reddit · LocalLLaMA", "reddit": "Reddit", "techcrunch": "TechCrunch", "verge": "The Verge",
    "ars_ai": "Ars Technica", "wired_ai": "Wired", "mit_tr_ai": "MIT Technology Review", "the_decoder": "The Decoder",
    "import_ai": "Import AI", "last_week_in_ai": "Last Week in AI", "simon_willison": "Simon Willison",
    "latent_space": "Latent Space", "interconnects": "Interconnects",
    "karpathy": "Andrej Karpathy", "armin_ronacher": "Armin Ronacher", "mitchell_hashimoto": "Mitchell Hashimoto",
    "thorsten_ball": "Thorsten Ball", "hamel_husain": "Hamel Husain", "sebastian_raschka": "Sebastian Raschka",
    "addy_osmani": "Addy Osmani", "ethan_mollick": "Ethan Mollick", "dwarkesh": "Dwarkesh Patel",
    "pragmatic_engineer": "Pragmatic Engineer", "bleepingcomputer": "BleepingComputer", "krebs": "Krebs on Security",
    "the_record": "The Record", "ars_security": "Ars Technica",
    "the_guardian": "The Guardian", "the_register": "The Register", "scmp_tech": "SCMP", "technode": "TechNode",
    "sifted": "Sifted", "bloomberg_tech": "Bloomberg", "ft_tech": "Financial Times", "wired_security": "Wired",
    "anthropic_eng": "Anthropic Engineering", "claude_dev": "Claude Code Blog", "gh_claude_code": "GitHub · Claude Code",
    "simon_til": "Simon Willison · TIL",
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
    "voices": _SVG.format('<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>'),
    "security": _SVG.format('<path d="M12 2l8 3v6c0 5-3.4 9.3-8 11-4.6-1.7-8-6-8-11V5z"/>'),
    "health": _SVG.format('<path d="M3 12h4l3-8 4 16 3-8h4"/>'),
    "tips": _SVG.format('<path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z"/>'),
    "also-today": _SVG.format('<path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01"/>'),
    "up": _SVG.format('<path d="M7 10v11M15 5.9 14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 7A2 2 0 0 1 17.5 21H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h2.8a2 2 0 0 0 1.8-1.1L12 2a3.1 3.1 0 0 1 3 3.9z"/>'),
    "warn": _SVG.format('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/>'),
    "terminal": _SVG.format('<path d="M4 17l6-6-6-6M12 19h8"/>'),
    "down": _SVG.format('<path d="M17 14V3M9 18.1 10 14H4.2a2 2 0 0 1-1.9-2.6l2.3-7A2 2 0 0 1 6.5 3H20a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-2.8a2 2 0 0 0-1.8 1.1L12 22a3.1 3.1 0 0 1-3-3.9z"/>'),
}
TRY_TODAY_MAX = 4
# Тот же предел, что модели задан в промпте для имени сюжета (digest/llm.py)
STORY_TAG_NAME_MAX = 40
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
(() => {
  // Подсветка раздела, который сейчас читают: верхний из разделов, пересекающих полосу у верха экрана
  const nav = document.querySelector("nav");
  const links = [...document.querySelectorAll('nav a[href^="#"]')];
  const sections = links.map(a => document.getElementById(a.getAttribute("href").slice(1))).filter(Boolean);
  if (!nav || !sections.length || !("IntersectionObserver" in window)) return;
  const inBand = new Set();
  const pick = () => {
    const current = sections.find(s => inBand.has(s));
    if (!current) return;
    const on = links.find(a => a.getAttribute("href") === "#" + current.id);
    links.forEach(a => a.classList.toggle("on", a === on));
    // На телефоне лента разделов прокручивается вбок: подсвеченный раздел подтягиваем в кадр
    const r = on.getBoundingClientRect(), n = nav.getBoundingClientRect();
    if (r.left < n.left || r.right > n.right) nav.scrollBy({left: r.left - n.left - 16});
  };
  const io = new IntersectionObserver(entries => {
    entries.forEach(e => e.isIntersecting ? inBand.add(e.target) : inBand.delete(e.target));
    pick();
  }, {rootMargin: "-80px 0px -55% 0px"});
  sections.forEach(s => io.observe(s));
})();
</script>"""
ARROW = "→"

# Кнопки оценок шлют POST в воркер обратной связи; выбор запоминается в браузере, чтобы не мигал при перезагрузке
VOTE_SCRIPT = """<script>
(() => {
  const FB = %s;
  const mark = (box, v) => box.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  // «Прочитано» = раскрыл разбор или ушёл к первоисточнику. В браузере запоминаем только после ответа воркера,
  // иначе при сбое сети отметка не дошла бы до вечернего выпуска уже никогда
  const tocRead = (id) => document.querySelectorAll('[data-toc="' + CSS.escape(id) + '"]').forEach(li => li.classList.add("read"));
  // Сколько прочитано: строка под заголовком и полоска под лентой разделов на телефоне
  const cards = document.querySelectorAll("[data-entry]");
  const progress = () => {
    const done = document.querySelectorAll("[data-entry].read").length;
    const line = document.querySelector(".sub .progress");
    if (line && done) { line.hidden = false; line.textContent = " · прочитано " + done + " из " + cards.length; }
    document.querySelector(".side")?.style.setProperty("--read", cards.length ? done / cards.length : 0);
  };
  const markRead = (card) => {
    card.classList.add("read");
    const id = card.dataset.entry;
    tocRead(id);
    progress();
    try { if (localStorage.getItem("r:" + id)) return; } catch (e) {}
    // keepalive: клик по первоисточнику уводит со страницы, и без него браузер оборвал бы запрос
    // Сбой сети не страшен: отметки в браузере нет, и следующее раскрытие или клик отправит её снова
    fetch(FB + "/read?e=" + encodeURIComponent(id), {method: "POST", keepalive: true})
      .then(r => { if (r.ok) { try { localStorage.setItem("r:" + id, "1"); } catch (e) {} } })
      .catch(() => {});
  };
  cards.forEach(card => {
    try { if (localStorage.getItem("r:" + card.dataset.entry)) { card.classList.add("read"); tocRead(card.dataset.entry); } } catch (e) {}
    card.querySelectorAll("details.more").forEach(d => d.addEventListener("toggle", () => { if (d.open) markRead(card); }));
    card.querySelectorAll("a[href^='http']").forEach(a => a.addEventListener("click", () => markRead(card)));
  });
  progress();
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
    # Модель нередко называет сюжет целым заголовком вместо короткого имени: такое имя лишь повторяет
    # хронологию под меткой и растягивает её на две строки
    name = f"{escape(entry.story_name)} · " if 0 < len(entry.story_name) <= STORY_TAG_NAME_MAX else ""
    return (f'<div class="story"><span class="tag story-tag">Сюжет · {name}<span class="story-day">{entry.story_day}-й день</span></span>'
            f'{f"<ul>{prev}</ul>" if prev else ""}</div>')


def _status(entry: Entry) -> str:
    label = STATUS_LABELS.get(entry.status)
    return f'<span class="tag status">{label}</span>' if label else ""


def _term(commands: list[str], label: str = "Терминал") -> str:
    return (f'<div class="term"><div class="term-bar">{ICONS["terminal"]}<span>{label}</span>'
            f'<button type="button" class="copy">Скопировать</button></div>'
            f'<pre><code>{escape(chr(10).join(commands))}</code></pre></div>')


def _entry_attr(entry: Entry) -> str:
    return f' data-entry="{escape(entry.id)}"' if entry.id else ""


def _anchor(entry: Entry) -> str:
    return f' id="e-{escape(entry.id)}"' if entry.id else ""


def _more(entry: Entry) -> str:
    source = f'<a class="pill" href="{_href(entry.item.url)}">Первоисточник {ARROW}</a>'
    d = entry.details
    # Пустой разбор — честный ответ модели, когда текста первоисточника не было: раскрывать нечего
    if d is None or not (d.what or d.points or d.try_it or d.commands or d.caveats):
        return f'<div class="more-links">{source}</div>'
    parts = [f'<p class="more-lead">{escape(d.what)}</p>'] if d.what else []
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


def _brief(entries: list[Entry]) -> str:
    """Весь выпуск списком заголовков: за полминуты видно всё, а читать идёшь к нужной карточке по якорю."""
    rows = "".join(f'<li data-toc="{escape(e.id)}"><a href="#e-{escape(e.id)}">{escape(e.title)}</a>'
                   f'<span>{BRIEF_LABELS.get(e.section, "")}</span></li>' for e in entries if e.id)
    if not rows:
        return ""
    return (f'        <section class="card brief" id="brief">\n          <div class="card-head"><h3>Коротко</h3>'
            f'<small>весь выпуск за полминуты</small></div>\n          <ol>{rows}</ol>\n        </section>')


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
    also = f'<span class="also">{_also(entry)}</span>' if entry.item.also else ""
    return (
        f'        <section class="card hero" id="main-story"{_entry_attr(entry)}>\n'
        f'          <div class="hero-top"><span class="chip">{chip}</span>'
        f'<a class="btn" href="{_href(entry.item.url)}">Первоисточник {ARROW}</a></div>\n'
        f'          <h2{_anchor(entry)}>{escape(entry.title)}</h2>\n          {_story(entry)}\n          <p>{escape(entry.summary)}</p>\n'
        f'          {_why(entry)}\n'
        f'          <div class="legend"><span>{SECTION_LABELS.get(entry.section, "Событие")}</span>{_status(entry)}'
        f'{_source(entry.item.url)}'
        f'{_buzz(entry.item)}{also}</div>\n'
        f'          {_actions(entry, votes)}\n'
        f'        </section>')


def _item(n: int, entry: Entry, votes: bool) -> str:
    hot = " hot" if entry.importance >= 8 else ""
    also = f'<span class="also">{_also(entry)}</span>' if entry.item.also else ""
    # Оценка важности нужна только отбору: читателю «8.6» ничего не говорит, главное видно по месту в выпуске
    copy = _term([entry.copy], "Команда или промпт") if entry.copy else ""
    return (f'<div class="item"{_entry_attr(entry)}><span class="num{hot}">{n}</span>'
            f'<div><div class="meta">{_status(entry)}'
            f'{_source(entry.item.url)}{_buzz(entry.item)}{also}</div>'
            f'<h4{_anchor(entry)}>{escape(entry.title)}</h4>{_story(entry)}<p>{escape(entry.summary)}</p>{_why(entry)}'
            f'{copy}{_actions(entry, votes)}</div></div>')


def _paper(entry: Entry, votes: bool) -> str:
    match = ARXIV_ID.search(entry.item.url)
    ident = f"arXiv:{match.group(1)}" if match else _host(entry.item.url)
    score = f'<span class="tag green">{entry.item.score} ▲</span>' if entry.item.score else ""
    return (f'<div class="paper"{_entry_attr(entry)}><div class="paper-foot">{score}{_status(entry)}'
            f'<a class="key" href="{_href(entry.item.url)}">{escape(ident)} {ARROW}</a></div>'
            f'<h4{_anchor(entry)}>{escape(entry.title)}</h4>{_story(entry)}<p>{escape(entry.summary)}</p>'
            f'{_actions(entry, votes, details=entry.details is not None)}</div>')


def _also_today(more: list[Mention]) -> str:
    """«Ещё за день»: важное, что не вошло в разборы, — одной строкой со ссылкой на источник."""
    rows = "".join(f'<li><a href="{_href(m.item.url)}">{escape(m.title)}</a>'
                   f'<span class="src-name">{escape(source_label(m.item.source))}</span>{_buzz(m.item)}</li>'
                   for m in more)
    return (f'        <section class="card also-today" id="also-today">\n          <div class="card-head"><h3>Ещё за день</h3>'
            f'<small>{plural(len(more), "новость", "новости", "новостей")} одной строкой</small></div>\n'
            f'          <ol>{rows}</ol>\n        </section>')


def _issue_day(issue: dict) -> str:
    return short_day(issue["date"]) + (" веч." if issue.get("slot") == "evening" else "")


def _missed(missed: list[dict], morning_page: str) -> str:
    links = "".join(f'<li><a href="{escape(morning_page)}#e-{escape(m["id"])}">{escape(m["title"])}</a></li>'
                    for m in missed)
    return (f'        <section class="card missed">\n          <div class="card-head"><h3>Утром ты пропустил</h3>'
            f'<small>{plural(len(missed), "новость", "новости", "новостей")}</small></div>\n'
            f'          <ul>{links}</ul>\n        </section>')


def _archive_sidebar(issues: list[dict] | None, current: str = "") -> str:
    if not issues:
        return ""
    links = "".join(
        f'<a href="{escape(i["page"])}"{" class=\"on\"" if i["page"] == current else ""}>'
        f'<b>{_issue_day(i)}</b><span>{escape(i["headline"])}</span></a>'
        for i in issues[:ARCHIVE_IN_SIDEBAR])
    return (f'<div class="arch"><div class="arch-title">Архив</div>{links}'
            f'<a class="arch-all" href="archive.html">Все выпуски {ARROW}</a></div>')


def _summary(title: str, sub: str, lead: str, cards: list[tuple[int | str, str]]) -> str:
    tiles = "".join(f'<div class="stat"><b>{value}</b><span>{label}</span></div>' for value, label in cards)
    lead_html = f'<p class="lead">{escape(lead)}</p>' if lead else ""
    return (f'        <section class="card">\n          <div class="card-head"><h3>{title}</h3><small>{sub}</small></div>\n'
            f'          {lead_html}\n          <div class="stats">{tiles}</div>\n        </section>')


def issue_summary(entries: list[Entry], kind: str = "day") -> str:
    """Строка «9 событий · ≈2 мин чтения» — под заголовком страницы и на обложке выпуска."""
    count = plural(len(entries), *(("сюжет", "сюжета", "сюжетов") if kind == "week" else ("событие", "события", "событий")))
    return f"{count} · ≈{reading_minutes(entries)} мин чтения"


def _og_image(url: str) -> str:
    if not url:
        return ""
    return (f'\n<meta property="og:image" content="{escape(url)}">\n<meta property="og:image:width" content="1200">'
            '\n<meta property="og:image:height" content="630">\n<meta name="twitter:card" content="summary_large_image">')


def _add_rows(main: list[str], nav: list[str], rows: list[tuple[str, str, str, str]], entries: list[Entry],
              votes: bool) -> None:
    for section, anchor, label, short in rows:
        group = [e for e in entries if e.section == section]
        if not group:
            continue
        items = "".join(_item(n, e, votes) for n, e in enumerate(group, start=1))
        main.append(f'        <section class="card" id="{anchor}">\n'
                    f'          <div class="card-head"><h3>{label}</h3><small>{len(group)}</small></div>\n'
                    f'          <div class="card-body">{items}</div>\n        </section>')
        nav.append(_nav(anchor, short, len(group)))


def render_page(digest: Digest, stats: dict, when: datetime, failures: list[str], *,
                feedback_url: str = "", issues: list[dict] | None = None, kind: str = "day", period: str = "",
                page_name: str = "", slot: str = "morning", missed: list[dict] | None = None,
                morning_page: str = "", og_image: str = "") -> str:
    votes = bool(feedback_url)
    # Героем идёт событие: если модель вернула одни авторские посты и фишки, «Главного» нет, они остаются в разделах
    top = next((e for e in digest.entries if e.section not in EXTRA_SECTIONS), None)
    rest = [e for e in digest.entries if e is not top]
    main = [_missed(missed, morning_page)] if missed else []
    main += [_hero(top, votes, "Главное" if kind == "day" else "Сюжет недели №1")] if top else []
    nav = [_nav("main-story", "Главное", active=True)] if top else []

    _add_rows(main, nav, [row for row in ROW_SECTIONS if row[0] not in EXTRA_SECTIONS], rest, votes)
    # «Ещё за день» — продолжение новостей дня: после разделов событий, до фишек и авторских постов
    if digest.more:
        main.append(_also_today(digest.more))
        nav.append(_nav("also-today", "Ещё за день", len(digest.more)))
    _add_rows(main, nav, [row for row in ROW_SECTIONS if row[0] in EXTRA_SECTIONS], rest, votes)

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
        crumbs = f"Дайджест<span>/</span>{human_date(when)}{' · вечер' if slot == 'evening' else ''}"
        # Цифры воронки читателю почти ничего не дают — они уходят в подвал, а место справа занимают оглавление
        # выпуска и готовые команды
        summary = "\n".join(block for block in (_brief(digest.entries), _try_today(digest.entries)) if block)
        lead = f'<p class="lead-top">{escape(digest.lead)}</p>' if digest.lead else ""
        funnel = [f'<span>Просмотрено {stats["raw"]}, новых {stats["merged"]}, в выпуске {len(digest.entries)}</span>']

    footer = [*funnel, f"<span>Собрано {local:%d.%m} в {local:%H:%M}</span>", "<span>Стоимость 0 ₽</span>",
              '<a href="tips.html">Арсенал фишек</a>', '<a href="health.html">Здоровье</a>']
    if failures:
        footer.append(f'<span class="fail">Не собрались: {escape(", ".join(f.split(":")[0] for f in failures))}</span>')

    description = f"{len(digest.entries)} событий. {digest.lead}"
    return TEMPLATE.substitute(
        headline=escape(digest.headline), description=escape(description[:300]), crumbs=crumbs, lead=lead,
        og_image=_og_image(og_image), sub=issue_summary(digest.entries, kind),
        short_date=f"{local:%d.%m}", weekday=WEEKDAYS[local.weekday()] if kind == "day" else "Неделя",
        nav="".join(nav), archive=_archive_sidebar(issues, page_name),
        main="\n".join(main), summary=summary, aside="\n".join(aside), footer="".join(footer),
        script=(VOTE_SCRIPT % json.dumps(feedback_url) if votes else "") + PAGE_SCRIPT,
    )


def render_archive(issues: list[dict], when: datetime) -> str:
    cards = "".join(
        f'<a class="item issue-link" href="{escape(i["page"])}"><span class="num">{short_day(i["date"]).split()[0]}</span>'
        f'<div><h4>{escape(i["headline"])}</h4><div class="meta"><span class="tag">{_issue_day(i)}</span>'
        f'<span>{plural(len(i["entries"]), "событие", "события", "событий")}</span></div></div></a>'
        for i in issues)
    main = (f'        <section class="card" id="main-story">\n          <div class="card-head"><h3>Все выпуски</h3>'
            f'<small>{len(issues)}</small></div>\n          <div class="card-body">{cards}</div>\n        </section>')
    local = when.astimezone(DISPLAY_TZ)
    return TEMPLATE.substitute(
        headline="Архив выпусков", description=f"{len(issues)} выпусков ИИ-дайджеста", crumbs="Дайджест<span>/</span>Архив",
        og_image="",
        sub=plural(len(issues), "выпуск", "выпуска", "выпусков"), lead="",
        short_date=f"{local:%d.%m}", weekday="Архив", nav=f'<a class="on" href="#main-story">{ICONS["archive"]}Все выпуски</a>',
        archive=_archive_sidebar(issues), main=main,
        summary=_summary("Архив", "", "", [(len(issues), "выпусков"), (sum(len(i["entries"]) for i in issues), "событий")]),
        aside="", footer="<span>Стоимость 0 ₽</span>", script="",
    )


MODEL_NAME = re.compile(r"gemini-(\d+(?:\.\d+)*)-(flash|pro)(-lite)?")
CALL_LABELS = {"select": "Отбор", "extras": "Ещё за день и фишки", "explain": "Разборы", "weekly": "Итог недели",
               "triage": "Отсев срочных", "alert": "Текст срочной"}
# Квота израсходована на эту долю — полоса желтеет: до вечернего выпуска может не хватить
QUOTA_WARN = 0.75


def model_label(name: str) -> str:
    """gemini-3.5-flash-lite → «3.5 Flash-Lite»: в цепочке попыток полные имена не помещаются в строку телефона."""
    match = MODEL_NAME.search(name)
    if not match:
        # Алиас gemini-flash-latest → «Flash latest»: какая модель за ним, знает только успешный ответ
        return name.removeprefix("models/").removeprefix("gemini-").replace("-", " ").capitalize()
    return f"{match.group(1)} {match.group(2).capitalize()}{'-Lite' if match.group(3) else ''}"


def _run_title(run: dict) -> str:
    if run["kind"] == "build":
        return "Вечерний выпуск" if run.get("slot") == "evening" else "Утренний выпуск"
    return {"weekly": "Итог недели", "alert": "Срочные"}.get(run["kind"], run["kind"])


def _attempt(call: dict) -> str:
    ok = call["status"] == 200 and not call["error"]
    # Модель, ответившую на алиас, называет только успешный ответ; у отказа — то, что просили, без догадок
    name = escape(model_label(call["served"] or call["asked"]))
    seconds = f'{call["seconds"]:.0f} с'
    if ok:
        thinking = f' · размышл. {call["thinking"]}' if call["thinking"] else ""
        return f'<span class="att ok">{name} · {seconds}{thinking}</span>'
    reason = escape(call["error"] or str(call["status"]))
    return f'<span class="att bad">{name} · {reason} · {seconds}</span>'


def _chains(calls: list[dict]) -> str:
    by_call: dict[str, list[dict]] = {}
    for call in calls:
        by_call.setdefault(call["call"], []).append(call)
    rows = "".join(f'<div class="chain"><b>{escape(CALL_LABELS.get(name, name or "вызов"))}</b>'
                   f'<span class="atts">{f" <i>{ARROW}</i> ".join(map(_attempt, attempts))}</span></div>'
                   for name, attempts in by_call.items())
    return f'<div class="chains">{rows}</div>' if rows else ""


def _run(run: dict) -> str:
    local = datetime.fromisoformat(run["at"].replace("Z", "+00:00")).astimezone(DISPLAY_TZ)
    # «Вышел» — только у выпуска; у часовой проверки срочных успех — норма, отмечаем лишь сбой
    if run["outcome"] != "ok":
        outcome = f'<span class="fail">сбой: {escape(run["outcome"][:120])}</span>'
    else:
        outcome = '<span class="tag green">вышел</span>' if run["kind"] != "alert" else ""
    parts = [_chains(run.get("calls", []))]
    if run.get("failed"):
        failed = ", ".join(f"{escape(source_label(s))} ({escape(e)})" for s, e in run["failed"].items())
        parts.append(f'<p class="run-note"><span class="fail">Не собрались</span> {failed}</p>')
    if run.get("events"):
        given = " · ".join(f"{escape(source_label(s))} {n}" for s, n in run["events"].items())
        parts.append(f'<p class="run-note">Дали событий: {given}</p>')
    if run.get("sources"):
        # Сколько свежих записей принёс каждый источник — видно и тогда, когда отбор упал и событий нет
        counts = " · ".join(f"{escape(source_label(s))} {n}" for s, n in
                            sorted(run["sources"].items(), key=lambda kv: -kv[1]))
        total = sum(run["sources"].values())
        parts.append(f'<details class="sources"><summary>Записей из источников: {total}</summary>'
                     f'<p class="run-note">{counts}</p></details>')
    return (f'<div class="item run"><div><div class="meta"><b>{local:%H:%M}</b>'
            f'<span>{_run_title(run)}</span>{outcome}</div>{"".join(parts)}</div></div>')


def _quota(runs: list[dict], settings: dict, now: datetime, aliases: dict[str, str]) -> str:
    usage = health.quota_usage(runs, health.quota_day_start(now))
    end = health.quota_day_end(now)
    reset_at = end.astimezone(DISPLAY_TZ)
    left = end - now
    hours, minutes = divmod(int(left.total_seconds()) // 60, 60)
    rows = []
    for role, limit in health.DAILY_LIMITS.items():
        name = settings[role]
        row = usage.get(name, {"used": 0, "failed": 0, "served": ""})
        share = min(1.0, row["used"] / limit)
        level = " full" if share >= 1 else " warn" if share >= QUOTA_WARN else ""
        failed = f'<span class="fail">отказов {row["failed"]}</span>' if row["failed"] else ""
        # У алиаса подписываем, какая модель отвечала на него последней: квота считается по ней
        now_serving = aliases.get(name, name)
        label = escape(model_label(name)) + (f'<small>сейчас {escape(model_label(now_serving))}</small>'
                                             if model_label(now_serving) != model_label(name) else "")
        rows.append(f'<div class="quota-row"><b>{label}</b>'
                    f'<span class="meter{level}" role="img" aria-label="{row["used"]} из {limit}">'
                    f'<i style="width:{share * 100:.0f}%"></i></span>'
                    f'<span class="quota-num">{row["used"]}/{limit}</span>{failed}</div>')
    return (f'        <section class="card" id="quota">\n          <div class="card-head"><h3>Квота Gemini</h3>'
            f'<small>сброс в {reset_at:%H:%M}, через {f"{hours} ч" if hours else f"{minutes} мин"}</small></div>\n'
            f'          <div class="quota">{"".join(rows)}</div>\n'
            f'          <p class="lead">Каждая попытка списывает запрос, даже 503 и таймаут. Локальные прогоны eval '
            f'здесь не видны.</p>\n        </section>')


def _health_day(day: str, runs: list[dict]) -> str:
    calls = [c for r in runs for c in r.get("calls", [])]
    refused = sum(1 for c in calls if c["status"] != 200 or c["error"])
    # Срочные проверяются каждый час, и почти все проверки проходят без модели и без сбоев — их только считаем
    quiet = [r for r in runs if r["kind"] == "alert" and not r.get("calls") and not r.get("failed")
             and r["outcome"] == "ok"]
    shown = [r for r in reversed(runs) if r not in quiet]
    summary = [plural(len(calls), "вызов", "вызова", "вызовов")]
    if refused:
        summary.append(f"отказов {refused}")
    body = "".join(map(_run, shown))
    if quiet:
        body += f'<p class="run-note quiet">{plural(len(quiet), "проверка", "проверки", "проверок")} срочных без вызовов модели</p>'
    d = date.fromisoformat(day)
    return (f'        <section class="card" id="d-{day}">\n          <div class="card-head"><h3>{d.day} {MONTHS[d.month - 1]}</h3>'
            f'<small>{" · ".join(summary)}</small></div>\n          <div class="card-body">{body}</div>\n        </section>')


def render_health(runs: list[dict], settings: dict, now: datetime) -> str:
    """Панель здоровья: квота текущих квотных суток, затем дни — какие модели просили, какие ответили, что упало."""
    days: dict[str, list[dict]] = {}
    for run in runs:
        local = datetime.fromisoformat(run["at"].replace("Z", "+00:00")).astimezone(DISPLAY_TZ)
        days.setdefault(local.date().isoformat(), []).append(run)
    # Какая модель отвечала на алиас последней — только для подписи квоты, не для отдельных попыток
    aliases = {c["asked"]: c["served"] for r in runs for c in r.get("calls", []) if c["served"]}
    main = [_quota(runs, settings, now, aliases)]
    nav = [f'<a class="on" href="#quota">{ICONS["health"]}Квота</a>']
    for day in sorted(days, reverse=True):
        main.append(_health_day(day, days[day]))
        nav.append(f'<a href="#d-{day}">{ICONS["archive"]}{short_day(day)}</a>')
    if not runs:
        main.append('        <section class="card noise"><h3>Запусков пока нет</h3>'
                    '<p>Панель заполнится после первого выпуска или проверки срочных.</p></section>')
    calls = [c for r in runs for c in r.get("calls", [])]
    refused = sum(1 for c in calls if c["status"] != 200 or c["error"])
    failed_sources = Counter(s for r in runs for s in r.get("failed", {}))
    aside = ""
    if failed_sources:
        rows = "".join(f'<li>{escape(source_label(s))}<span class="src-name">{plural(n, "раз", "раза", "раз")}</span></li>'
                       for s, n in failed_sources.most_common(8))
        aside = (f'        <section class="card also-today">\n          <div class="card-head"><h3>Падали чаще всего</h3>'
                 f'<small>за {health.RETENTION_DAYS} дней</small></div>\n          <ol>{rows}</ol>\n        </section>')
    local = now.astimezone(DISPLAY_TZ)
    return TEMPLATE.substitute(
        headline="Здоровье дайджеста", description="Вызовы Gemini, квота и источники за 30 дней", og_image="",
        crumbs="Дайджест<span>/</span>Здоровье", lead="",
        sub=f"{plural(len(runs), 'запуск', 'запуска', 'запусков')} за {health.RETENTION_DAYS} дней",
        short_date=f"{local:%d.%m}", weekday="Здоровье", nav="".join(nav), archive="",
        main="\n".join(main),
        summary=_summary("30 дней", "", "", [(len(runs), "запусков"), (len(calls), "вызовов"), (refused, "отказов")]),
        aside=aside, footer=f"<span>Обновлено {local:%d.%m} в {local:%H:%M}</span>", script=PAGE_SCRIPT,
    )


# Темы фишек — в том же порядке, что в llm.TIP_TOPICS; «Разное» — для фишек без темы (архив до арсенала, ошибка модели)
TIP_TOPIC_LABELS = {"claude_code": "Claude Code", "agents": "Агенты и IDE", "local": "Локальные модели",
                    "prompts": "Промпты", "api": "API и SDK", "workflow": "Рабочий процесс", "other": "Разное"}

# Галочка «попробовал»: состояние живёт в воркере (страница статичная и пересобирается дважды в день), браузер помнит
# последний выбор на случай, если воркер не ответил. Снять галочку можно повторным нажатием
TIPS_SCRIPT = """<script>
(() => {
  const FB = %s;
  const boxes = [...document.querySelectorAll(".tried input")];
  const count = () => {
    const done = boxes.filter(b => b.checked).length;
    const tile = document.querySelector("[data-tried-count]");
    if (tile) tile.textContent = done;
  };
  const set = (id, on) => boxes.filter(b => b.dataset.id === id).forEach(b => {
    b.checked = on; b.closest(".tip").classList.toggle("done", on);
  });
  boxes.forEach(b => { try { if (localStorage.getItem("t:" + b.dataset.id) === "1") set(b.dataset.id, true); } catch (e) {} });
  count();
  fetch(FB + "/tried").then(r => r.ok ? r.json() : Promise.reject(r.status)).then(({ids}) => {
    const on = new Set(ids);
    boxes.forEach(b => {
      set(b.dataset.id, on.has(b.dataset.id));
      try { localStorage.setItem("t:" + b.dataset.id, on.has(b.dataset.id) ? "1" : "0"); } catch (e) {}
    });
    count();
  }).catch(() => {});
  boxes.forEach(b => b.addEventListener("change", () => {
    const id = b.dataset.id, on = b.checked;
    const label = b.closest(".tried");
    label.classList.remove("err");
    set(id, on);
    count();
    fetch(FB + "/tried?e=" + encodeURIComponent(id) + "&v=" + (on ? 1 : 0), {method: "POST"})
      .then(r => { if (!r.ok) throw new Error(r.status); try { localStorage.setItem("t:" + id, on ? "1" : "0"); } catch (e) {} })
      .catch(() => { set(id, !on); count(); label.classList.add("err"); });
  }));
})();
</script>"""


def _tip(tip: dict, page: str, tried: bool) -> str:
    copy = (_term([tip["copy"]], "Команда или промпт") if tip.get("copy")
            else '<p class="tip-note">Команда не подтверждена источником — шаги в первоисточнике</p>')
    why = f'<div class="why"><b>Зачем:</b> {escape(tip["why"])}</div>' if tip.get("why") else ""
    summary = f'<p>{escape(tip["summary"])}</p>' if tip.get("summary") else ""
    check = (f'<label class="tried"><input type="checkbox" data-id="{escape(tip["id"])}">попробовал</label>'
             if tried else "")
    return (f'<div class="item tip" data-tip="{escape(tip["id"])}"><div>'
            f'<div class="meta"><span class="tag">{short_day(tip["date"])}</span>'
            f'<span>{escape(source_label(tip["source"]))}</span></div>'
            f'<h4>{escape(tip["title"])}</h4>{summary}{why}{copy}'
            f'<div class="tip-foot">{check}<a class="pill" href="{escape(page)}#e-{escape(tip["id"])}">В выпуске</a>'
            f'<a class="pill" href="{_href(tip["url"])}">Первоисточник {ARROW}</a></div></div></div>')


def render_tips(issues: list[dict], when: datetime, feedback_url: str = "") -> str:
    """Арсенал: все фишки архива по темам, новые сверху. Тему ставит модель в том же вызове extras."""
    tried = bool(feedback_url)
    groups: dict[str, list[tuple[dict, str]]] = {topic: [] for topic in TIP_TOPIC_LABELS}
    for issue in issues:
        for e in issue["entries"]:
            if e["section"] == "tips":
                topic = e.get("topic") if e.get("topic") in TIP_TOPIC_LABELS else "other"
                groups[topic].append(({**e, "date": issue["date"]}, issue["page"]))
    total = sum(map(len, groups.values()))
    main: list[str] = []
    nav: list[str] = []
    for topic, tips in groups.items():
        if not tips:
            continue
        cards = "".join(_tip(tip, page, tried) for tip, page in tips)
        main.append(f'        <section class="card" id="t-{topic}">\n          <div class="card-head"><h3>{TIP_TOPIC_LABELS[topic]}</h3>'
                    f'<small>{len(tips)}</small></div>\n          <div class="card-body">{cards}</div>\n        </section>')
        nav.append(f'<a{" class=\"on\"" if not nav else ""} href="#t-{topic}">{ICONS["tips"]}{TIP_TOPIC_LABELS[topic]}'
                   f'<span class="count">{len(tips)}</span></a>')
    main.append('        <section class="card noise"><h3>Новые фишки появятся с выпусками</h3>'
                '<p>Каждый выпуск добавляет 2–4 приёма из релизов, блогов инженеров, Reddit и HN.</p></section>')
    with_copy = sum(1 for tips in groups.values() for tip, _ in tips if tip.get("copy"))
    tiles: list[tuple[int | str, str]] = [(total, "фишек"), (with_copy, "с командой")]
    if tried:
        # Сколько попробовано, знает только воркер: число подставит скрипт страницы
        tiles.append(('<output data-tried-count>0</output>', "попробовано"))
    summary = _summary("Арсенал", "", "", tiles)
    local = when.astimezone(DISPLAY_TZ)
    return TEMPLATE.substitute(
        headline="Арсенал фишек", description=f"{plural(total, 'фишка', 'фишки', 'фишек')} из выпусков ИИ-дайджеста",
        og_image="", crumbs="Дайджест<span>/</span>Арсенал", lead="",
        sub=f"{plural(total, 'фишка', 'фишки', 'фишек')} из архива",
        short_date=f"{local:%d.%m}", weekday="Арсенал", nav="".join(nav), archive=_archive_sidebar(issues),
        main="\n".join(main), summary=summary, aside="",
        footer=f"<span>Обновлено {local:%d.%m} в {local:%H:%M}</span>",
        script=(TIPS_SCRIPT % json.dumps(feedback_url) if tried else "") + PAGE_SCRIPT,
    )
