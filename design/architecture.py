"""Архитектура ai-digest: от расписания до выпуска и обратно через голоса читателя.

Пересборка: pip install open-diagram && python design/architecture.py → design/architecture.svg (+ .png при наличии
Chrome). Пакет нужен только для этой схемы, поэтому его нет в зависимостях проекта. Сборка падает с LayoutError,
если блоки или подписи наезжают друг на друга.
"""
from pathlib import Path

from open_diagram import Diagram, Graph, Note, Panel, Tile, Tiles
from open_diagram.theme import Status, Theme

theme = Theme(
    statuses={
        "live": Status("В проде", "#27323F", "#FFFFFF"),
        "building": Status("В разработке", "#B26B00", "#FFF5E5", "dashed"),
        "trial": Status("Пилот", "#6B4FD8", "#F4F1FE", "dashed"),
        "planned": Status("План", "#5C636B", "#F1F2F4", "dotted"),
        "retiring": Status("Выводится", "#B42318", "#FEF0EE"),
    },
    link_labels={"flow": "Поток данных", "planned": "Связь в плане"},
    col_gap=84,
    row_gap=32,
)

d = Diagram(
    "ai-digest: как собирается выпуск",
    subtitle="Личный дайджест AI-новостей: ~40 лент → отбор моделью → сайт и Telegram, дважды в день и ежечасные алерты",
    tag="ARCH",
    theme=theme,
    note=Note("Правило fail-soft", "Падение источника, extras, explain или обложки — только запись в лог",
              "Выпуск отменяет лишь select, упавший на всех моделях",
              "state/ коммитится последним: сбой оставляет новости непрочитанными"),
)

scheduler = Panel(
    "Worker · cron",
    Tiles([Tile("08:07", sub="утренний выпуск"), Tile("18:07", sub="вечерний выпуск"),
           Tile(":17 каждый час", sub="алерты и фишки")], cols=1),
    sub="Cloudflare · Ташкент UTC+5", tone="dark", footer="Причина: GitHub cron ненадёжен",
)

actions = Panel(
    "GitHub Actions",
    Tiles([Tile("digest.yml", sub="build · weekly по вс"), Tile("alert.yml", sub="alert · tip"),
           Tile("ci.yml", sub="ruff · mypy · pytest")], cols=1),
    sub="workflow_dispatch",
)
sources = Panel(
    "Источники",
    Tiles(["RSS / Atom", "Reddit", "GitHub releases", "Google News", "Hacker News"], cols=2),
    sub="sources.toml · порядок = приоритет",
)

pipeline = Panel(
    "Пайплайн build",
    Graph(
        {"fetch": "fetch_all", "seen": "take_new", "sel": "select", "ext": "extras",
         "num": "verify_numbers", "exp": "explain", "det": "verify_details", "rec": "record_issue"},
        [("fetch", "seen"), ("seen", "sel"), ("sel", "ext"), ("ext", "num"),
         ("num", "exp"), ("exp", "det"), ("det", "rec")],
        # Одна строка: у графа нет стрелок, и «змейка» читалась бы справа налево
        at={k: (i, 0) for i, k in enumerate(["fetch", "seen", "sel", "ext", "num", "exp", "det", "rec"])},
        spacing=(124, 62),
    ),
    Tiles([Tile("≤ 6 событий", sub="EVENTS_CAP"), Tile("≤ 3 про безопасность", sub="SECURITY_CAP"),
           Tile("≤ 3 с важностью ≥ 8", sub="cap_hot")], cols=3),
    sub="python -m digest · digest/__main__.py", tone="accent",
)

models = Panel(
    "Цепочка моделей",
    Tiles([Tile("Claude Haiku 5.5", sub="платная · первая"), Tile("Gemini 3.8 Flash", sub="20 запросов/день"),
           Tile("Gemini 3.6 Flash", sub="свои 20/день"), Tile("Flash Lite", sub="500/день · пишет чаще всех")],
          cols=4),
    sub="generate_first: слева направо, по 1 попытке, без ретраев", footer="Принцип: каждый 503 съедает квоту",
)

site = Panel(
    "GitHub Pages",
    Tiles(["Выпуск дня", "Архив обложек", "Арсенал фишек", "Сюжеты", "health.html"], cols=2),
    sub="статичный сайт · site/",
)
telegram = Panel(
    "Telegram",
    Tiles([Tile("Выпуск + обложка"), Tile("Срочные алерты"), Tile("Фишка в случайный час")], cols=1),
    sub="бот · кнопки «зашло / не зашло»",
)
state = Panel(
    "state/ в git",
    Tiles(["seen.json", "issues.json", "stories.json", "health/"], cols=2),
    sub="память между запусками · коммит последним", tone="neutral",
)

top = d.columns([[scheduler], [actions, sources], [pipeline, models], [site, telegram, state]],
                valign="center")
(c_sched,), (c_act, c_src), (c_pipe, c_models), (c_site, c_tg, c_state) = top

# Обратная связь: тот же Worker, но как хранилище голосов — под всей схемой
low_y = max(p.bottom for col in top for p in col) + 56
feedback = d.add(
    Panel("Worker · отзывы", Tiles(["/vote", "/read", "/tried", "/telegram", "KV VOTES"], cols=5),
          sub="голоса и отметки «прочитано» · только PAGE_ORIGIN и OWNER_CHAT_ID", tone="dark"),
    x=c_pipe.x, y=low_y,
)

d.group("Сборка в CI", [c_act, c_src, c_pipe, c_models], sub="GitHub Actions runner")
d.group("Публикация", [c_site, c_tg, c_state])

d.link(c_sched, c_act, label="dispatch")
d.link(c_act, c_pipe, label="запуск")
d.link(c_src, c_pipe, label="~40 лент")
d.link(c_pipe, c_models, start="bottom", end="top", label="JSON-схема")
d.link(c_pipe, c_site, label="HTML")
d.link(c_pipe, c_tg)
d.link(c_pipe, c_state)
# Обходим «Публикацию» справа, чтобы линия не выглядела связью Pages → Telegram → state
d.link(c_site, feedback, start="right", end="right", label="голоса · прочитано", via_x=c_site.right + 70)
d.link(feedback, c_pipe, start="left", end="bottom", label="fetch_feedback", via_x=c_pipe.x - 24, cross_ok=True)

d.save(str(Path(__file__).with_suffix("")), png=False)
