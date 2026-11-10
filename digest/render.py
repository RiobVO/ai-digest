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
from digest.mood import MOODS, mood

TEMPLATE = Template((Path(__file__).parent / "templates" / "page.html").read_text(encoding="utf-8"))
# Прежняя оболочка: на ней остаются архив, арсенал, сюжеты и здоровье, пока не переедут на новую
# (design/REDESIGN.md, этап 3)
LEGACY = Template((Path(__file__).parent / "templates" / "legacy.html").read_text(encoding="utf-8"))
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
    "simon_til": "Simon Willison · TIL", "securelist": "Securelist", "unit42": "Unit 42",
    "ms_threat_intel": "Microsoft Threat Intelligence", "dfir_report": "The DFIR Report", "project_zero": "Project Zero",
    "thehackernews": "The Hacker News", "securityweek": "SecurityWeek", "xakep": "Хакер", "habr_infosec": "Хабр · ИБ",
    "embrace_the_red": "Embrace The Red", "trail_of_bits": "Trail of Bits", "reddit_tips": "Reddit",
    "n8n_blog": "n8n", "sans_isc": "SANS ISC", "bhis": "Black Hills InfoSec", "hackingarticles": "Hacking Articles",
}
# Тред с парой баллов — не обсуждение, а чей-то репост; такой не показываем
HN_MIN_SHOWN = 20
# Средняя скорость чтения русского текста с экрана, слов в минуту
WORDS_PER_MINUTE = 180

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{}</svg>'
ICONS = {
    "archive": _SVG.format('<path d="M3 5h18v4H3zM5 9v10h14V9M10 13h4"/>'),
    "health": _SVG.format('<path d="M3 12h4l3-8 4 16 3-8h4"/>'),
    "tips": _SVG.format('<path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z"/>'),
    "up": _SVG.format('<path d="M7 10v11M15 5.9 14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 7A2 2 0 0 1 17.5 21H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h2.8a2 2 0 0 0 1.8-1.1L12 2a3.1 3.1 0 0 1 3 3.9z"/>'),
    "terminal": _SVG.format('<path d="M4 17l6-6-6-6M12 19h8"/>'),
    "down": _SVG.format('<path d="M17 14V3M9 18.1 10 14H4.2a2 2 0 0 1-1.9-2.6l2.3-7A2 2 0 0 1 6.5 3H20a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-2.8a2 2 0 0 0-1.8 1.1L12 22a3.1 3.1 0 0 1-3-3.9z"/>'),
}
TRY_TODAY_MAX = 4
# Тот же предел, что модели задан в промпте для имени сюжета (digest/llm.py)
STORY_TAG_NAME_MAX = 40
# Скрипт прежней оболочки (legacy.html): листание карточек с клавиатуры и копирование команд
LEGACY_SCRIPT = """<script>
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

# Скрипт страницы выпуска: шапка с датой и кругами прогресса, лист разбора и сюжета, содержание, термины,
# оценки и отметки чтения (POST в воркер обратной связи). Адрес воркера подставляется вместо __FB__; без него
# оценок нет, а отметки чтения живут до закрытия страницы. Без сторонних скриптов.
PAGE_SCRIPT = """<script>
(() => {
  const FB = __FB__;
  const root = document.documentElement, $ = id => document.getElementById(id);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const deck = $("deck"), hdr = $("hdr"), sheet = $("sheet"), scrim = $("scrim"), shBody = $("sh-b"), tip = $("tip");
  const slides = [...document.querySelectorAll(".slide")], stories = slides.filter(s => s.classList.contains("st"));
  const N = stories.length, progs = [...document.querySelectorAll(".prog")];
  const nn = i => String(i + 1).padStart(2, "0");
  // Хранилище браузера бывает закрыто (частный режим, запрет для сайта): страница работает и без памяти о выборе
  const recall = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const remember = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { return; } };
  const go = (el, now) => el.scrollIntoView({block: "start", behavior: now || reduced ? "instant" : "smooth"});
  root.classList.add("js");
  hdr.hidden = false;

  // Тема: системная, пока читатель не выбрал сам; выбор помнится в браузере (его применяет скрипт в <head>)
  const themeBtn = $("theme");
  const dark = () => root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const paintTheme = () => { if (themeBtn) themeBtn.textContent = dark() ? "день" : "ночь"; };
  if (themeBtn) themeBtn.hidden = false;
  paintTheme();

  // Прочитано: задержался на новости, открыл разбор или ушёл к первоисточнику. Круг закрашивается сразу,
  // а в браузере отметка запоминается только после ответа воркера: при сбое сети следующий раз отправит её снова
  const read = new Set(), verdict = $("verdict");
  const count = () => { if (verdict && N) verdict.textContent = read.size === N ? "Сигнал принят" : "Прочитано " + read.size + " из " + N; };
  function paint(i) {
    read.add(i);
    progs.forEach(p => p.children[i].classList.add("on"));
    document.querySelectorAll('[data-toc="' + i + '"]').forEach(a => a.classList.add("read"));
    if (read.size === N) progs.forEach(p => p.classList.add("done"));
    count();
  }
  function markRead(s) {
    const i = +s.dataset.i, id = s.dataset.entry;
    if (!read.has(i)) paint(i);
    if (!FB || !id || recall("r:" + id)) return;
    // keepalive: клик по первоисточнику уводит со страницы, и без него браузер оборвал бы запрос
    fetch(FB + "/read?e=" + encodeURIComponent(id), {method: "POST", keepalive: true})
      .then(r => { if (r.ok) remember("r:" + id, "1"); })
      .catch(() => { /* сеть недоступна: отметки в браузере нет, следующая задержка или клик отправит её снова */ });
  }
  stories.forEach(s => { if (s.dataset.entry && recall("r:" + s.dataset.entry)) paint(+s.dataset.i); });
  count();

  if ("IntersectionObserver" in window) {
    root.classList.add("anim");
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) e.target.classList.add("in"); }), {threshold: .01, rootMargin: "0px 0px 12% 0px"});
    slides.forEach(s => io.observe(s));
  }

  // Дата: один элемент поверх страницы. Пока обложка уезжает, он сжимается и встаёт в шапку — и остаётся там
  const fly = $("fly"), ph = document.querySelector(".cover .ph"), anchor = $("hdr-date"), cover = $("cover");
  let P0, T, sEnd, D;
  function measure() {
    const r = ph.getBoundingClientRect();
    hdr.style.transform = "none";
    const a = anchor.getBoundingClientRect();
    P0 = {x: r.left, y: r.top + scrollY}; T = {x: a.left, y: a.top}; sEnd = a.height / r.height;
    D = Math.max(240, cover.offsetHeight - a.bottom - 8);
    root.style.setProperty("--date-w", Math.ceil(r.width * sEnd) + "px");
  }
  // Дата всегда во всю ширину колонки: кегль считается от ширины набранной строки
  function fitDate() {
    root.style.removeProperty("--date-size");
    const box = ph.closest(".content").clientWidth - 30, w = ph.getBoundingClientRect().width, fs = parseFloat(getComputedStyle(ph).fontSize);
    root.style.setProperty("--date-size", Math.min(fs * box / w, innerHeight * .36, 400) + "px");
  }
  function place() {
    const y = scrollY, p = Math.min(1, Math.max(0, y / D)), e = reduced ? (p < 1 ? 0 : 1) : p * p * (3 - 2 * p);
    const fromY = P0.y - y;
    fly.style.transform = "translate(" + (P0.x + (T.x - P0.x) * e) + "px," + (fromY + (T.y - fromY) * e) + "px) scale(" + (1 + (sEnd - 1) * e) + ")";
    hdr.style.transform = "translateY(" + (Math.min(1, Math.max(0, (p - .72) / .28)) - 1) * 102 + "%)";
  }

  // Текущий плакат — тот, что лежит на линии в 40% высоты экрана. Новость прочитана, если на ней задержались
  // дольше DWELL_MS (решение владельца 2026-11-10: 6–7 секунд, секунды из макета мало — так считается и пролистанное)
  const DWELL_MS = 6500;
  let cur = -1, dwell = 0;
  function arm() {
    clearTimeout(dwell);
    const s = slides[cur];
    if (!s || !s.classList.contains("st") || document.hidden || sheet.classList.contains("open")) return;
    dwell = setTimeout(() => { if (!document.hidden && !sheet.classList.contains("open")) markRead(s); }, DWELL_MS);
  }
  function setCur(i) {
    if (i === cur) return;
    cur = i; const s = slides[i], st = s.classList.contains("st");
    $("hdr-sec").textContent = s.dataset.label || ""; $("hdr-n").textContent = st ? nn(+s.dataset.i) + " / " + N : "";
    arm();
  }
  function onScroll() {
    place();
    const line = innerHeight * .4;
    for (let i = 0; i < slides.length; i++) { const r = slides[i].getBoundingClientRect(); if (r.top <= line && r.bottom > line) { setCur(i); break; } }
  }
  function refit() { fitDate(); measure(); onScroll(); }
  let tick = false;
  addEventListener("scroll", () => { hideTip(); if (tick) return; tick = true; requestAnimationFrame(() => { tick = false; onScroll(); }); }, {passive: true});
  addEventListener("resize", refit);
  document.addEventListener("visibilitychange", arm);
  root.classList.add("fly"); refit();
  document.fonts.ready.then(refit);
  document.fonts.addEventListener("loadingdone", refit);

  // Лист снизу. Пока он открыт, страница под ним не листается: прокрутка заперта и жесты мимо текста листа гасятся
  let opener = null, hiddenHl = null, sheetStory = null;
  function showSheet(kicker, title, nodes, from) {
    const t = $("sh-t");
    $("sh-k").textContent = kicker; if (from) t.innerHTML = from.innerHTML; else t.textContent = title;
    t.style.cssText = ""; shBody.replaceChildren(...nodes); shBody.scrollTop = 0;
    opener = document.activeElement;
    sheet.hidden = scrim.hidden = false; root.classList.add("locked"); deck.inert = true;
    if (from && !reduced) {
      // Заголовок в листе — точная уменьшенная копия заголовка плаката (те же переносы), поэтому перелёт заканчивается без скачка
      const r = from.getBoundingClientRect(), cs = getComputedStyle(from), fs = parseFloat(cs.fontSize), k = Math.min(.72, 30 / fs);
      t.style.cssText = "font-size:" + fs * k + "px;line-height:" + parseFloat(cs.lineHeight) / fs + ";max-width:" + Math.ceil(r.width * k) + "px;opacity:0";
      sheet.style.transition = "none"; sheet.style.transform = "none";
      const tr = t.getBoundingClientRect(), css = getComputedStyle(root);
      sheet.style.transform = ""; void sheet.offsetHeight; sheet.style.transition = "";
      const c = from.cloneNode(true);
      c.className = "hl flight";
      c.style.cssText = "left:" + r.left + "px;top:" + r.top + "px;width:" + r.width + "px;font-size:" + fs + "px";
      document.body.append(c);
      from.style.visibility = "hidden"; hiddenHl = from;
      c.animate([{transform: "none", color: css.getPropertyValue("--ink")}, {transform: "translate(" + (tr.left - r.left) + "px," + (tr.top - r.top) + "px) scale(" + k + ")", color: css.getPropertyValue("--paper")}],
        {duration: 520, easing: "cubic-bezier(.22,1,.36,1)", fill: "forwards"}).onfinish = () => { t.style.opacity = 1; c.remove(); };
    }
    void sheet.offsetHeight; void scrim.offsetHeight;
    sheet.classList.add("open"); scrim.classList.add("open");
    sheet.focus({preventScroll: true});
    clearTimeout(dwell);
  }
  function closeSheet() {
    sheet.style.transition = ""; sheet.style.transform = "";
    sheet.classList.remove("open"); scrim.classList.remove("open"); root.classList.remove("locked"); deck.inert = false;
    if (hiddenHl) { hiddenHl.style.visibility = ""; hiddenHl = null; }
    sheetStory = null;
    setTimeout(() => { if (!sheet.classList.contains("open")) sheet.hidden = scrim.hidden = true; }, 480);
    if (opener) opener.focus({preventScroll: true});
    arm();
  }
  // Текст разбора лежит в разметке новости: лист показывает его копию. Разборы можно читать подряд, не закрывая лист
  const withMore = stories.filter(s => s.querySelector(".more-body"));
  const kicker = s => "Разбор · " + nn(+s.dataset.i) + " / " + N;
  function breakdown(s) {
    const at = withMore.indexOf(s), prev = withMore[at - 1], next = withMore[at + 1];
    const nav = document.createElement("div");
    nav.className = "sh-nav";
    nav.innerHTML = (prev ? '<button type="button" class="lnk" data-more-go="' + prev.dataset.i + '">← Предыдущий</button>' : "<span></span>")
      + (next ? '<button type="button" class="btn" data-more-go="' + next.dataset.i + '">Следующий разбор →</button>' : '<button type="button" class="btn" data-close>Закрыть</button>');
    return [...s.querySelector(".more-body").cloneNode(true).childNodes, nav];
  }
  function openBreakdown(s) {
    showSheet(kicker(s), "", breakdown(s), s.querySelector(".hl"));
    sheetStory = s; markRead(s);
  }
  function swapBreakdown(s) {
    if (hiddenHl) { hiddenHl.style.visibility = ""; hiddenHl = null; }
    const t = $("sh-t");
    t.style.cssText = ""; t.innerHTML = s.querySelector(".hl").innerHTML;
    $("sh-k").textContent = kicker(s);
    shBody.replaceChildren(...breakdown(s)); shBody.scrollTop = 0;
    go(s, true); onScroll(); clearTimeout(dwell);
    sheetStory = s; markRead(s);
    if (!reduced) shBody.animate([{opacity: 0, transform: "translateY(10px)"}, {opacity: 1, transform: "none"}], {duration: 260, easing: "ease-out"});
  }
  // Содержание из любого места: нажатие на верхнюю строку
  function openToc() {
    const toc = document.querySelector("#index .toc");
    if (toc) showSheet(N ? "Прочитано " + read.size + " из " + N : "", "Содержание", [toc.cloneNode(true)]);
  }
  function openThread(tag) {
    const box = tag.closest(".thread"), s = tag.closest(".st"), list = box.querySelector(".tl").cloneNode(true), name = box.querySelector("b");
    const title = s.querySelector(".hl").cloneNode(true);
    title.querySelector(".idx")?.remove();
    const now = document.createElement("li"), time = document.createElement("time"), text = document.createElement("span");
    now.className = "now"; time.textContent = "сегодня"; text.textContent = title.textContent;
    now.append(time, text); list.append(now);
    const nodes = [list];
    if (tag.href) { const all = document.createElement("a"); all.className = "lnk"; all.href = tag.href; all.textContent = "Вся хронология →"; nodes.push(all); }
    showSheet(tag.textContent.replace(" →", ""), name ? name.textContent : "Сюжет", nodes);
  }
  function showTip(el) {
    const b = document.createElement("b");
    b.textContent = el.textContent;
    tip.replaceChildren(b, el.dataset.def); tip.hidden = false;
    const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(12, Math.min(innerWidth - w - 12, r.left + r.width / 2 - w / 2)) + "px";
    tip.style.top = (r.bottom + 10 + h < innerHeight ? r.bottom + 10 : r.top - h - 10) + "px";
  }
  function hideTip() { tip.hidden = true; }

  // Смахивание листа вниз закрывает его; текст разбора при этом прокручивается как обычно
  let drag = null;
  sheet.addEventListener("touchstart", e => { drag = {y: e.touches[0].clientY, at: Date.now(), on: false, dy: 0}; }, {passive: true});
  sheet.addEventListener("touchmove", e => {
    if (!drag) return;
    const dy = e.touches[0].clientY - drag.y, scrolls = shBody.contains(e.target) && shBody.scrollHeight > shBody.clientHeight;
    if (!drag.on) {
      if (scrolls && !(shBody.scrollTop <= 0 && dy > 0)) return;
      if (dy <= 0) { e.preventDefault(); return; }
      drag.on = true; sheet.style.transition = "none";
    }
    e.preventDefault(); drag.dy = dy;
    sheet.style.transform = "translateY(" + Math.max(0, dy) + "px)";
  }, {passive: false});
  sheet.addEventListener("touchend", () => {
    const d = drag; drag = null;
    if (!d || !d.on) return;
    if (d.dy > 110 || d.dy / (Date.now() - d.at) > .6) closeSheet(); else { sheet.style.transition = ""; sheet.style.transform = ""; }
  });
  scrim.addEventListener("touchmove", e => e.preventDefault(), {passive: false});
  scrim.addEventListener("click", closeSheet);
  document.addEventListener("click", e => { if (!e.target.closest(".tw,.tt")) hideTip(); }, true);

  // Оценка уходит в воркер; палец закрашивается сразу, а при сбое возвращается прежний выбор
  const setVote = (box, v) => box.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  document.querySelectorAll(".vote").forEach(box => { const saved = recall("v:" + box.dataset.id); if (saved) setVote(box, saved); });
  function vote(b) {
    const box = b.closest(".vote"), id = box.dataset.id, v = b.dataset.v, was = box.querySelector(".on")?.dataset.v || "";
    if (v === was) return;
    box.classList.remove("err"); setVote(box, v);
    if (v === "up" && !reduced) {
      const el = document.createElement("span");
      el.className = "burst";
      el.innerHTML = "<b></b><b></b>" + Array.from({length: 8}, (x, k) => '<i style="--a:' + k * 45 + 'deg"></i>').join("");
      b.append(el); setTimeout(() => el.remove(), 800);
      // Вибрации нет на iPhone и в части встроенных окон — эффект остаётся только зрительным
      if (navigator.vibrate) navigator.vibrate(12);
    }
    fetch(FB + "/vote?e=" + encodeURIComponent(id) + "&v=" + v, {method: "POST"})
      .then(r => { if (!r.ok) throw new Error(r.status); remember("v:" + id, v); })
      .catch(() => { setVote(box, was); box.classList.add("err"); });
  }
  function copy(b) {
    const code = b.closest(".term").querySelector("code");
    const ok = () => { b.textContent = "Скопировано"; setTimeout(() => { b.textContent = "Скопировать"; }, 1600); };
    // Буфер обмена закрыт (встроенный браузер, нет разрешения): выделяем текст, чтобы его можно было скопировать руками
    const select = () => { const r = document.createRange(); r.selectNodeContents(code); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); b.textContent = "Выделено"; };
    try { navigator.clipboard.writeText(code.textContent).then(ok, select); } catch (err) { select(); }
  }

  document.addEventListener("click", e => {
    const t = e.target.closest("button,a,summary"); if (!t) return;
    const s = t.closest(".st") || sheetStory;
    if (t.classList.contains("tw")) { e.preventDefault(); showTip(t); }
    else if (t.matches(".more>summary")) { e.preventDefault(); openBreakdown(t.closest(".st")); }
    else if (t.dataset.moreGo) swapBreakdown(stories[+t.dataset.moreGo]);
    else if ("close" in t.dataset || t.id === "sh-x") closeSheet();
    else if (t.matches(".thread .tag") && t.parentElement.querySelector(".tl")) { e.preventDefault(); openThread(t); }
    else if (t === themeBtn) { root.dataset.theme = dark() ? "light" : "dark"; remember("theme", root.dataset.theme); paintTheme(); }
    else if (t.id === "hdr-btn") openToc();
    else if (t.matches(".vote button")) vote(t);
    else if (t.classList.contains("copy")) copy(t);
    else if (t.matches('a[href^="#"]')) {
      const to = $(decodeURIComponent(t.getAttribute("href").slice(1)));
      if (!to) return;
      e.preventDefault();
      if (sheet.classList.contains("open")) closeSheet();
      go(to); history.replaceState(null, "", t.getAttribute("href"));
    }
    else if (s && t.matches('a[href^="http"]')) markRead(s);
  });
  // Esc закрывает лист; j и k листают плакаты, o открывает разбор текущей новости
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") hideTip();
    if (sheet.classList.contains("open")) { if (e.key === "Escape") closeSheet(); return; }
    if (e.metaKey || e.ctrlKey || e.altKey || /input|textarea/i.test(e.target.tagName)) return;
    const step = e.key === "j" ? 1 : e.key === "k" ? -1 : 0;
    if (step) go(slides[Math.max(0, Math.min(slides.length - 1, cur + step))]);
    else if (e.key === "o" && slides[cur]?.querySelector(".more-body")) openBreakdown(slides[cur]);
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


NBSP, WJ = "\u00a0", "\u2060"
_LETTER = "A-Za-zА-Яа-яЁё"
_SHORT_WORD = re.compile(r"(^|[\s(«])([А-Яа-яЁё]{1,2}) (?=\S)")
_NUMBER = re.compile(rf"(\d) (?=[{_LETTER}%])")
# Короткая часть до дефиса в начале слова: русская («из-за», «по-новому») или число перед словом («0-day», «3-й»).
# Латинские имена с дефисом (CVE-2026-1, gpt-4o, claude-code) не трогаем: их копируют из текста в поиск и в терминал
_HYPHEN_HEAD = re.compile(rf"(?<![{_LETTER}0-9])([А-Яа-яЁё]{{1,3}}|\d{{1,3}}(?=-[{_LETTER}]))-(?=[{_LETTER}0-9])")
_HYPHEN_TAIL = re.compile(rf"(?<=[А-Яа-яЁё])-([А-Яа-яЁё]{{1,3}})(?![{_LETTER}0-9])")
_URL = re.compile(r"(\S*://\S*)")
# Служебные знаки, которыми _rich помечает место термина в тексте: из чужого текста их убираем
_MARKS = re.compile("[\ue000-\ue0ff]")


def typo(text: str) -> str:
    """Русский набор: предлог или союз в 1–2 буквы не остаётся в конце строки, тире и число не отрываются от
    соседнего слова, короткая часть через дефис («из-за», «0-day») не переносится. Адреса не трогает."""
    parts = _URL.split(text)
    for n in range(0, len(parts), 2):
        part = parts[n]
        # Дважды: в «и в доме» первое совпадение съедает пробел, с которого начинается второе
        part = _SHORT_WORD.sub(rf"\1\2{NBSP}", _SHORT_WORD.sub(rf"\1\2{NBSP}", part))
        part = _NUMBER.sub(rf"\1{NBSP}", part.replace(" —", f"{NBSP}—"))
        part = _HYPHEN_TAIL.sub(rf"{WJ}-{WJ}\1", _HYPHEN_HEAD.sub(rf"\1{WJ}-{WJ}", part))
        parts[n] = part
    return "".join(parts)


def _rich(text: str, terms: list[tuple[str, str]] | None = None) -> str:
    """Чужой текст для страницы: экранирование, русский набор и термины. Первое дословное вхождение термина
    становится кнопкой с объяснением; найденные термины из списка terms убираются — остаток покажет _terms."""
    text = _MARKS.sub("", text)
    found: list[tuple[str, str]] = []
    for pair in list(terms or []):
        match = re.search(rf"(?<!\w){re.escape(pair[0])}(?!\w)", text) if pair[0] else None
        if match is None or terms is None:
            continue
        text = text[:match.start()] + chr(0xE000 + len(found)) + text[match.end():]
        found.append(pair)
        terms.remove(pair)
    html = escape(typo(text))
    for n, (term, meaning) in enumerate(found):
        html = html.replace(chr(0xE000 + n),
                            f'<button type="button" class="tw" data-def="{escape(meaning)}">{escape(typo(term))}</button>')
    return html


def _terms(pending: list[tuple[str, str]]) -> str:
    """Термины, которых в видимом тексте новости дословно нет: строкой под текстом, чтобы объяснение не пропало."""
    rows = "".join(f"<div><dt>{escape(t)}</dt><dd>{escape(m)}</dd></div>" for t, m in pending)
    return f'<dl class="terms rv" style="--d:4">{rows}</dl>' if rows else ""


def _vote(entry: Entry, enabled: bool) -> str:
    if not enabled or not entry.id:
        return ""
    return (f'<span class="vote" data-id="{escape(entry.id)}">'
            f'<button type="button" data-v="up" aria-label="Полезно" title="Полезно">{ICONS["up"]}</button>'
            f'<button type="button" data-v="down" aria-label="Мимо" title="Мимо">{ICONS["down"]}</button></span>')


def _actions(entry: Entry, votes: bool) -> str:
    source = f'<a class="lnk" href="{_href(entry.item.url)}">Источник ↗</a>'
    return f'<div class="act rv" style="--d:5">{_more(entry)}{source}{_vote(entry, votes)}</div>'


def _thread(entry: Entry) -> str:
    # Первый день сюжета ничем не отличается от обычной новости — метку показываем со второго
    if entry.story_day < 2:
        return ""
    prev = "".join(f"<li><time>{short_day(day)}</time><span>{_rich(title)}</span></li>" for day, title in entry.story_prev)
    # Модель нередко называет сюжет целым заголовком вместо короткого имени: такое имя лишь повторяет хронологию
    name = f"<b>{escape(entry.story_name)}</b>" if 0 < len(entry.story_name) <= STORY_TAG_NAME_MAX else ""
    label = f"Сюжет · {entry.story_day}-й день"
    # Со второго дня у сюжета есть своя страница с хронологией (render_story_pages); в итоге недели id нет.
    # Скрипт по нажатию показывает прошлые дни листом, без скрипта ссылка ведёт на страницу сюжета
    tag = (f'<a class="tag" href="{story_page(entry.story)}">{label} {ARROW}</a>' if entry.story
           else f'<button type="button" class="tag">{label}</button>')
    chronology = f'<ol class="tl">{prev}</ol>' if prev else ""
    return f'<div class="thread rv">{tag}{name}{chronology}</div>'


def _status(entry: Entry) -> str:
    label = STATUS_LABELS.get(entry.status)
    return f'<span class="status">{label}</span>' if label else ""


# Промпт фишки из непроверенного источника (Reddit, HN): вставленный агенту, он исполняется как команда
UNTRUSTED_LABEL = "Непроверенный промпт — прочитай перед вставкой"


def _term(commands: list[str], label: str = "Терминал", wrap: bool = False, warn: bool = False) -> str:
    """wrap — для фишек: там бывает промпт, а это текст; команда терминала остаётся в одну строку с прокруткой.
    warn — подпись-предупреждение: на странице выпуска подпись видна только у неё."""
    return (f'<div class="term{" wrap" if wrap else ""}"><div class="term-bar{" warn" if warn else ""}">'
            f'{ICONS["terminal"]}<span>{label}</span>'
            f'<button type="button" class="copy">Скопировать</button></div>'
            f'<pre><code>{escape(chr(10).join(commands))}</code></pre></div>')


def _more(entry: Entry) -> str:
    """Разбор новости. Лежит в разметке закрытым: скрипт показывает его листом, без скрипта он раскрывается на месте."""
    d = entry.details
    # Пустой разбор — честный ответ модели, когда текста первоисточника не было: раскрывать нечего
    if d is None or not (d.what or d.points or d.try_it or d.commands or d.caveats):
        return ""
    parts = [f'<p class="more-lead">{_rich(d.what)}</p>'] if d.what else []
    if d.points:
        parts.append(f'<ol class="points">{"".join(f"<li>{_rich(p)}</li>" for p in d.points)}</ol>')
    if d.commands:
        parts.append(_term(d.commands))
    if d.try_it:
        parts.append(f'<p class="note">{_rich(d.try_it)}</p>')
    if d.caveats:
        parts.append(f'<div class="caveat"><b class="label">Ограничения</b>{_rich(d.caveats)}</div>')
    parts.append(f'<a class="lnk" href="{_href(entry.item.url)}">Первоисточник ↗</a>')
    return (f'<details class="more"><summary class="btn">Разбор</summary>'
            f'<div class="more-body">{"".join(parts)}</div></details>')


def _meta(entry: Entry) -> str:
    item = entry.item
    if entry.section == "paper":
        match = ARXIV_ID.search(item.url)
        source = f"<span>arXiv:{escape(match.group(1))}</span>" if match else _source(item.url)
        score = f"<span>{item.score} ▲</span>" if item.score else ""
        return f"{score}{source}{_status(entry)}"
    also = f'<span class="also">{_also(entry)}</span>' if item.also else ""
    return f"{_source(item.url)}{_buzz(item)}{_status(entry)}{also}"


def _side(entry: Entry) -> str:
    """Команда рядом с текстом новости: у фишки — её команда или промпт, у события — команды из разбора."""
    if entry.copy:
        block = _term([entry.copy], UNTRUSTED_LABEL if entry.copy_untrusted else "Команда или промпт", wrap=True,
                      warn=entry.copy_untrusted)
    elif entry.details and entry.details.commands:
        block = _term(entry.details.commands[:2])
    else:
        return ""
    return f'<div class="side rv" style="--d:4">{block}</div>'


def _card_id(n: int, entry: Entry) -> str:
    # У записей итога недели id нет: якорем служит номер на странице
    return f"e-{escape(entry.id)}" if entry.id else f"s-{n + 1}"


def _card(n: int, entry: Entry, votes: bool, label: str) -> str:
    """Новость в ленте. Все новости в одном наборе (решение владельца 2026-11-10): раздел виден в шапке страницы,
    номер — маленький индекс перед заголовком. Оценка важности нужна только отбору и читателю не показывается."""
    pending = list(entry.details.terms) if entry.details else []
    title = _rich(entry.title, pending)
    why = _rich(entry.why, pending) if entry.why else ""
    body = _rich(entry.summary, pending)
    ident = f' id="{_card_id(n, entry)}"' + (f' data-entry="{escape(entry.id)}"' if entry.id else "")
    parts = [
        _thread(entry),
        f'<h2 class="hl rv" style="--d:1"><span class="idx">{n + 1:02d}</span>{title}</h2>',
        f'<div class="meta rv" style="--d:2">{_meta(entry)}</div>',
        f'<p class="why rv" style="--d:3"><b>Почему важно</b>{why}</p>' if why else "",
        f'<div class="cols"><p class="body rv" style="--d:4">{body}</p>{_side(entry)}</div>',
        _terms(pending),
        _actions(entry, votes),
    ]
    return (f'<article class="slide st"{ident} data-i="{n}" data-label="{escape(label)}"><div class="inner">'
            f'<div class="content">{"".join(parts)}</div></div></article>')


def _block(anchor: str, title: str, body: str) -> str:
    return (f'<section class="slide blk" id="{anchor}" data-label="{title}"><div class="inner"><div class="content">'
            f'<h2 class="hl rv">{title}</h2>{body}</div></div></section>')


def _try_today(entries: list[Entry], numbers: dict[int, int]) -> str:
    rows: list[str] = []
    shown: set[str] = set()
    for entry in entries:
        commands = entry.details.commands if entry.details else []
        # Общие команды вроде «pip install transformers» повторяются у разных новостей — показываем один раз
        commands = [c for c in commands if c not in shown]
        shown.update(commands)
        if commands and entry.id:
            rows.append(f'<div class="try"><a href="#{_card_id(numbers[id(entry)], entry)}">{_rich(entry.title)}</a>'
                        f'{_term(commands[:2])}</div>')
    return _block("try", "Попробовать сегодня", "".join(rows[:TRY_TODAY_MAX])) if rows else ""


def _also_today(more: list[Mention]) -> str:
    """«Ещё за день»: важное, что не вошло в разборы, — одной строкой со ссылкой на источник."""
    rows = "".join(f'<li><a href="{_href(m.item.url)}">{_rich(m.title)}</a>'
                   f'<span class="k"><span>{escape(source_label(m.item.source))}</span>{_buzz(m.item)}</span></li>'
                   for m in more)
    return _block("also-today", "Ещё за день", f'<ol class="list">{rows}</ol>')


def _issue_day(issue: dict) -> str:
    return short_day(issue["date"]) + (" веч." if issue.get("slot") == "evening" else "")


def _missed(missed: list[dict], morning_page: str) -> str:
    links = "".join(f'<li><a href="{escape(morning_page)}#e-{escape(m["id"])}">{_rich(m["title"])}</a></li>'
                    for m in missed)
    return _block("missed", "Утром ты пропустил", f'<ul class="list">{links}</ul>')


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
    """Строка «9 событий · ≈2 мин чтения» — на обложке страницы и на картинке выпуска."""
    count = plural(len(entries), *(("сюжет", "сюжета", "сюжетов") if kind == "week" else ("событие", "события", "событий")))
    return f"{count} · ≈{reading_minutes(entries)} мин чтения"


def _og_image(url: str) -> str:
    if not url:
        return ""
    return (f'\n<meta property="og:image" content="{escape(url)}">\n<meta property="og:image:width" content="1200">'
            '\n<meta property="og:image:height" content="630">\n<meta name="twitter:card" content="summary_large_image">')


def _progress(count: int) -> str:
    """Круги прогресса: по кругу на новость, изнутри наружу; прочитанные закрашивает скрипт."""
    step = 13.5 / max(count - 1, 1)
    circles = "".join(f'<circle cx="17" cy="17" r="{1.6 + n * step:.1f}" style="--i:{n}"/>' for n in range(count))
    return f'<svg class="prog" viewBox="0 0 34 34" aria-hidden="true">{circles}</svg>'


def _mood_css() -> str:
    day = ";".join(f"--{name}:{m.color}" for name, m in MOODS.items())
    night = ";".join(f"--{name}:{m.night}" for name, m in MOODS.items())
    pick = " ".join(f'[data-mood="{name}"]{{--mood:var(--{name})}}' for name in MOODS)
    return (f':root{{{day}}}\n@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{night}}}}}\n'
            f':root[data-theme="dark"]{{{night}}}\n{pick}')


MOOD_CSS = _mood_css()
# Круги сигнала на обложке: расходятся от точки, цвет — цвет дня
RINGS = ('<svg class="rings" viewBox="0 0 100 100" aria-hidden="true"><circle class="dot" cx="50" cy="50" r="1.6"/>'
         + "".join(f'<circle cx="50" cy="50" r="{6 + 5 * n}" style="--i:{n}"/>' for n in range(9)) + "</svg>")
MARK = ('<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="14" fill="none" stroke="currentColor" '
        'stroke-width="2.4"/><circle cx="16" cy="16" r="6" fill="currentColor"/></svg>')


def _cover(headline: str, lead: str, date: str, small: str, spoken: str, meta: list[str], first: str, toc: bool) -> str:
    """Обложка выпуска: дата во всю ширину цветом дня. spoken — дата словами для чтения вслух («10.11» там невнятно)."""
    body = f'<p class="body rv" style="--d:2">{_rich(lead)}</p>' if lead else ""
    spans = "".join(f"<span>{m}</span>" for m in meta if m)
    read = f'<a class="btn" href="#{first}">Читать ↓</a>' if first else ""
    index = '<a class="lnk" href="#index">Содержание</a>' if toc else ""
    return (f'<section class="slide cover" id="cover" data-label="">{RINGS}<div class="inner">'
            f'<div class="bar"><a class="mark" href="index.html">{MARK}сигнал</a>'
            f'<button type="button" class="lnk sm" id="theme" hidden>ночь</button></div>'
            f'<div class="content"><div class="date" role="img" aria-label="{escape(spoken)}"><span class="ph">{date}</span>'
            f'<small>{escape(small)}</small></div>'
            f'<h1 class="hl rv" style="--d:1">{_rich(headline)}</h1>{body}'
            f'<div class="meta rv" style="--d:3">{spans}</div>'
            f'<div class="act rv" style="--d:4">{read}{index}</div></div></div></section>')


def _noise(noise: list[Item]) -> str:
    if not noise:
        return ""
    by_source = Counter(i.source for i in noise)
    rows = "".join(f'<li><a href="{_href(i.url)}">{escape(i.title)}</a>'
                   f'<span class="k">{escape(source_label(i.source))}</span></li>' for i in noise)
    sources = ", ".join(f"{escape(source_label(s))} {n}" for s, n in by_source.most_common(5))
    return (f'<details class="noise rv" id="noise"><summary class="lnk">Отсеяно как шум: {len(noise)}</summary>'
            f'<p class="body">Больше всего: {sources}</p><ul class="list">{rows}</ul></details>')


def _previous_issue(issues: list[dict] | None, page_name: str) -> str:
    """Ссылка на выпуск перед этим — вместо списка архива в боковой колонке прежней страницы."""
    pages = [i["page"] for i in issues or []]
    if page_name not in pages or pages.index(page_name) + 1 >= len(pages):
        return ""
    return f'<a class="lnk" href="{escape(pages[pages.index(page_name) + 1])}">← Прошлый выпуск</a>'


def render_page(digest: Digest, stats: dict, when: datetime, failures: list[str], *,
                feedback_url: str = "", issues: list[dict] | None = None, kind: str = "day", period: str = "",
                page_name: str = "", slot: str = "morning", missed: list[dict] | None = None,
                morning_page: str = "", og_image: str = "") -> str:
    votes = bool(feedback_url)
    # Героем идёт событие: если модель вернула одни авторские посты и фишки, «Главного» нет, они остаются в разделах
    top = next((e for e in digest.entries if e.section not in EXTRA_SECTIONS), None)
    rest = [e for e in digest.entries if e is not top]
    # Настроение — у дня; у недельной сводки его нет: пять сюжетов про бизнес — это не «тихий день»
    day_mood = mood([e.section for e in digest.entries]) if kind == "day" else "calm"

    def rows(extra: bool) -> list[tuple[str, str, list[Entry]]]:
        return [(anchor, label, [e for e in rest if e.section == section])
                for section, anchor, label, _ in ROW_SECTIONS if (section in EXTRA_SECTIONS) == extra]

    lead_group = [("main-story", "Главное" if kind == "day" else "Сюжет недели №1", [top])] if top else []
    # «Ещё за день» — продолжение новостей дня: после разделов событий, до фишек и авторских постов
    groups = [*lead_group, *rows(False), ("also-today", "", []), *rows(True),
              ("papers", "Статьи", [e for e in rest if e.section == "paper"])]
    feed: list[str] = []
    toc: list[str] = []
    # Номер новости на странице по объекту записи: у записей итога недели id нет
    numbers: dict[int, int] = {}
    for anchor, label, group in groups:
        if anchor == "also-today":
            if digest.more:
                feed.append(_also_today(digest.more))
                toc.append(f'<li><a href="#also-today"><span class="n"></span><span class="t">Ещё за день</span><span class="k">'
                           f'{plural(len(digest.more), "новость", "новости", "новостей")} одной строкой</span></a></li>')
            continue
        cards = []
        for entry in group:
            n = numbers[id(entry)] = len(numbers)
            cards.append(_card(n, entry, votes, label))
            toc.append(f'<li><a data-toc="{n}" href="#{_card_id(n, entry)}"><span class="n">{n + 1:02d}</span>'
                       f'<span class="t">{_rich(entry.title)}</span>'
                       f'<span class="k">{BRIEF_LABELS.get(entry.section, "")}</span></a></li>')
        if cards:
            feed.append(f'<section class="grp" id="{anchor}" data-label="{label}">{"".join(cards)}</section>')

    local = when.astimezone(DISPLAY_TZ)
    count = len(digest.entries)
    if kind == "week":
        # Обложка недели — крупно её номер (решение владельца): дата у итога недели не одна
        date, small, spoken = str(local.isocalendar().week), f"неделя · {period}", f"Итог недели, {period}"
        meta = ["итоги недели", *(plural(n, *forms) for n, forms in (
            (stats["issues"], ("выпуск", "выпуска", "выпусков")), (stats["events"], ("событие", "события", "событий")),
            (count, ("сюжет", "сюжета", "сюжетов"))))]
        funnel = (f"За неделю вышло {plural(stats['issues'], 'выпуск', 'выпуска', 'выпусков')} и "
                  f"{plural(stats['events'], 'событие', 'события', 'событий')}; в итог прошло {count}.")
    else:
        evening = slot == "evening"
        date, small = f"{local:%d.%m}", f"{WEEKDAYS[local.weekday()].lower()} · {'вечер' if evening else 'утро'}"
        spoken = f"{human_date(when)}{' · вечер' if evening else ''}"
        meta = [issue_summary(digest.entries), MOODS[day_mood].label]
        funnel = (f"Из {stats['raw']} просмотренных новостей новых {stats['merged']}, в выпуск прошло {count}."
                  + (f" Ещё {len(digest.noise)} отсеяно как шум." if digest.noise else ""))

    first = "main-story" if top else (_card_id(0, digest.entries[0]) if digest.entries else "")
    body = [_cover(digest.headline, digest.lead, date, small, spoken, meta, first, bool(toc))]
    if missed:
        body.append(_missed(missed, morning_page))
    if toc:
        body.append(f'<section class="slide blk" id="index" data-label="Содержание"><div class="inner"><div class="content">'
                    f'<ol class="toc">{"".join(toc)}</ol></div></div></section>')
    body += feed
    body.append(_try_today(digest.entries, numbers))

    fail = (f'<span class="fail">Не собрались: {escape(", ".join(f.split(":")[0] for f in failures))}</span>'
            if failures else "")
    body.append(
        f'<section class="slide end" id="end" data-label="Конец выпуска"><div class="inner"><div class="content">'
        f'{_progress(len(numbers))}<h2 class="hl rv" id="verdict">Конец выпуска</h2>'
        f'<p class="body rv" style="--d:1">{funnel}</p>{_noise(digest.noise)}'
        f'<div class="meta rv" style="--d:2"><span>собрано {local:%d.%m} в {local:%H:%M}</span><span>стоимость 0 ₽</span>{fail}</div>'
        f'<div class="act rv" style="--d:3"><a class="btn" href="#cover">К началу ↑</a>{_previous_issue(issues, page_name)}'
        f'<a class="lnk" href="archive.html">Архив</a><a class="lnk" href="tips.html">Арсенал фишек</a>'
        f'<a class="lnk" href="health.html">Здоровье</a></div></div></div></section>')

    description = f"{count} событий. {digest.lead}"
    return TEMPLATE.substitute(
        headline=escape(digest.headline), description=escape(description[:300]), og_image=_og_image(og_image),
        mood=day_mood, mood_css=MOOD_CSS, date=date, rings=_progress(len(numbers)),
        body="\n".join(block for block in body if block),
        script=PAGE_SCRIPT.replace("__FB__", json.dumps(feedback_url)),
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
    return LEGACY.substitute(
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
               "triage": "Отсев срочных", "alert": "Текст срочной", "numbers": "Проверка чисел"}
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
    return LEGACY.substitute(
        headline="Здоровье дайджеста", description="Вызовы Gemini, квота и источники за 30 дней", og_image="",
        crumbs="Дайджест<span>/</span>Здоровье", lead="",
        sub=f"{plural(len(runs), 'запуск', 'запуска', 'запусков')} за {health.RETENTION_DAYS} дней",
        short_date=f"{local:%d.%m}", weekday="Здоровье", nav="".join(nav), archive="",
        main="\n".join(main),
        summary=_summary("30 дней", "", "", [(len(runs), "запусков"), (len(calls), "вызовов"), (refused, "отказов")]),
        aside=aside, footer=f"<span>Обновлено {local:%d.%m} в {local:%H:%M}</span>", script=LEGACY_SCRIPT,
    )


# Темы фишек — в том же порядке, что в llm.TIP_TOPICS; «Разное» — для фишек без темы (архив до арсенала, ошибка модели)
TIP_TOPIC_LABELS = {"claude_code": "Claude Code", "agents": "Агенты и IDE", "prompts": "Промпты",
                    "automation": "Автоматизация и боты", "security": "ИБ-инструменты", "ai_security": "ИИ-безопасность",
                    "local": "Локальные модели",
                    "api": "API и SDK", "workflow": "Рабочий процесс", "other": "Разное"}

# Галочка «попробовал»: состояние живёт в воркере (страница статичная и пересобирается дважды в день), браузер помнит
# последний выбор на случай, если воркер не ответил. Снять галочку можно повторным нажатием
TIPS_SCRIPT = """<script>
(() => {
  const FB = %s;
  const boxes = [...document.querySelectorAll(".tried input")];
  // Галочки, которые читатель уже трогал: снимок из воркера, пришедший позже клика, их не перезаписывает
  const touched = new Set();
  const count = () => {
    const tile = document.querySelector("[data-tried-count]");
    if (tile) tile.textContent = boxes.filter(b => b.checked).length;
  };
  const set = (id, on) => boxes.filter(b => b.dataset.id === id).forEach(b => {
    b.checked = on; b.closest(".tip").classList.toggle("done", on);
  });
  const remember = (id, on) => { try { localStorage.setItem("t:" + id, on ? "1" : "0"); } catch (e) {} };
  boxes.forEach(b => { try { if (localStorage.getItem("t:" + b.dataset.id) === "1") set(b.dataset.id, true); } catch (e) {} });
  count();
  fetch(FB + "/tried").then(r => r.ok ? r.json() : Promise.reject(r.status)).then(({ids}) => {
    const on = new Set(ids);
    boxes.filter(b => !touched.has(b.dataset.id)).forEach(b => { set(b.dataset.id, on.has(b.dataset.id)); remember(b.dataset.id, on.has(b.dataset.id)); });
    count();
  }).catch(() => {});
  boxes.forEach(b => b.addEventListener("change", () => {
    const id = b.dataset.id, on = b.checked;
    const label = b.closest(".tried");
    touched.add(id);
    label.classList.remove("err");
    set(id, on);
    count();
    // Пока запрос не вернулся, галочка заблокирована: два запроса подряд воркер мог бы применить в обратном порядке
    b.disabled = true;
    fetch(FB + "/tried?e=" + encodeURIComponent(id) + "&v=" + (on ? 1 : 0), {method: "POST"})
      .then(r => { if (!r.ok) throw new Error(r.status); remember(id, on); })
      // Изменение не сохранилось — значит, верен снимок воркера: снова разрешаем ему обновить эту галочку
      .catch(() => { touched.delete(id); set(id, !on); count(); label.classList.add("err"); })
      .finally(() => { b.disabled = false; });
  }));
})();
</script>"""


def _tip(tip: dict, page: str, tried: bool) -> str:
    copy = (_term([tip["copy"]], UNTRUSTED_LABEL if tip.get("copy_untrusted") else "Команда или промпт", wrap=True)
            if tip.get("copy")
            else '<p class="tip-note">Команда не подтверждена источником — шаги в первоисточнике</p>')
    why = f'<div class="why"><b>Зачем:</b> {escape(tip["why"])}</div>' if tip.get("why") else ""
    summary = f'<p>{escape(tip["summary"])}</p>' if tip.get("summary") else ""
    # Шаги из разбора полного текста — то же, что «Подробности» в Telegram; у фишек до 22.10 их нет
    steps = "".join(f"<li>{escape(step)}</li>" for step in tip.get("steps", [])[:5])
    details = (f'<section><div class="more-label">Подробности</div><ol class="points">{steps}</ol></section>'
               if steps else "")
    check = (f'<label class="tried"><input type="checkbox" data-id="{escape(tip["id"])}">попробовал</label>'
             if tried else "")
    return (f'<div class="item tip" data-tip="{escape(tip["id"])}"><div>'
            f'<div class="meta"><span class="tag">{short_day(tip["date"])}</span>'
            f'<span>{escape(source_label(tip["source"]))}</span></div>'
            f'<h4>{escape(tip["title"])}</h4>{summary}{why}{details}{copy}'
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
    return LEGACY.substitute(
        headline="Арсенал фишек", description=f"{plural(total, 'фишка', 'фишки', 'фишек')} из выпусков ИИ-дайджеста",
        og_image="", crumbs="Дайджест<span>/</span>Арсенал", lead="",
        sub=f"{plural(total, 'фишка', 'фишки', 'фишек')} из архива",
        short_date=f"{local:%d.%m}", weekday="Арсенал", nav="".join(nav), archive=_archive_sidebar(issues),
        main="\n".join(main), summary=summary, aside="",
        footer=f"<span>Обновлено {local:%d.%m} в {local:%H:%M}</span>",
        script=(TIPS_SCRIPT % json.dumps(feedback_url) if tried else "") + LEGACY_SCRIPT,
    )


def story_page(story_id: str) -> str:
    # Плоское имя, а не папка story/: относительные ссылки шаблона (иконки, манифест, архив) должны работать
    return f"story-{story_id}.html"


def _slot_order(issue: dict) -> tuple[str, bool]:
    return issue["date"], issue.get("slot") == "evening"


def render_story_pages(issues: list[dict], stories: dict[str, dict], when: datetime) -> dict[str, str]:
    """Страница на каждый сюжет со второго дня: хронология из архива — тех же записей, что вышли в выпусках."""
    events: dict[str, list[tuple[dict, dict]]] = {}
    for issue in sorted(issues, key=_slot_order):
        for e in issue["entries"]:
            if e.get("story"):
                events.setdefault(e["story"], []).append((issue, e))
    pages = {}
    local = when.astimezone(DISPLAY_TZ)
    for sid, rows in events.items():
        days = sorted({issue["date"] for issue, _ in rows})
        if len(days) < 2:
            continue
        name = stories.get(sid, {}).get("name", "")
        title = name or rows[0][1]["title"]
        items = "".join(
            f'<li><time>{short_day(issue["date"])}{" · вечер" if issue.get("slot") == "evening" else ""}</time>'
            f'<a href="{escape(issue["page"])}#e-{escape(e["id"])}">{escape(e["title"])}</a>'
            f'<div class="meta"><span class="tag">{SECTION_LABELS.get(e["section"], "Событие")}</span>'
            f'<span>{escape(source_label(e["source"]))}</span></div></li>'
            for issue, e in rows)
        main = (f'        <section class="card" id="main-story">\n          <div class="card-head"><h3>Хронология</h3>'
                f'<small>{short_day(days[0])} — {short_day(days[-1])}</small></div>\n'
                f'          <ol class="timeline">{items}</ol>\n        </section>')
        pages[story_page(sid)] = LEGACY.substitute(
            headline=escape(title), description=escape(f"Сюжет: {title}"), og_image="",
            crumbs="Дайджест<span>/</span>Сюжет", lead="",
            sub=f"{plural(len(days), 'день', 'дня', 'дней')} · {plural(len(rows), 'событие', 'события', 'событий')}",
            short_date=f"{local:%d.%m}", weekday="Сюжет",
            nav=f'<a class="on" href="#main-story">{ICONS["archive"]}Хронология</a>', archive=_archive_sidebar(issues),
            main=main, summary=_summary("Сюжет", "", "", [(len(days), "дней"), (len(rows), "событий")]),
            aside="", footer=f"<span>Обновлено {local:%d.%m} в {local:%H:%M}</span>", script=LEGACY_SCRIPT,
        )
    return pages
