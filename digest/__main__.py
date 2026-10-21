import argparse
import copy
import json
import logging
import os
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest import alert as breaking
from digest import evaluate, health
from digest.cover import make_cover
from digest.dedup import dedupe
from digest.fetch import attach_discussions, fetch_all, fetch_articles, resolve_gnews
from digest.llm import LlmError, explain, extras, generate_first, select, verify_copies
from digest.memory import (Archive, Stories, fetch_feedback, issue_slot, missed_in_morning, preferences_prompt,
                           record_issue, resolve_votes)
from digest.models import Item
from digest.mood import mood
from digest.render import (DISPLAY_TZ, MONTHS, human_date, issue_summary, render_archive, render_health, render_page,
                           render_story_pages, render_tips)
from digest.stack import fetch_stack
from digest.state import SeenStore
from digest.telegram import build_keyboard, build_message, send
from digest.weekly import WEEK_DAYS, summarize_week

log = logging.getLogger("digest")
OUT = Path("out")


def model_chains(settings: dict) -> tuple[list[str], list[str]]:
    """Цепочки моделей бесплатного ключа: квоты раздельные, и при сбое вызов сразу идёт к следующей модели.
    Главная — отбор и разборы: сначала 3.8 Flash (20 запросов в день). Побочная — «Ещё за день» и фишки: 3.6 Flash,
    чтобы не тратить квоту 3.8. Последней везде стоит Lite (500 в день): выпуск похуже лучше, чем никакого."""
    return ([settings["model"], settings["extras_model"], settings["lite_model"]],
            [settings["extras_model"], settings["lite_model"]])


def load_env(path: Path) -> None:
    """Локальный запуск читает .env; в GitHub Actions переменные приходят из секретов и не перетираются."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            os.environ.setdefault(name.strip(), value.strip())


def _write_message(name: str, text: str, url: str, keyboard: dict | None) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps({"text": text, "url": url, "keyboard": keyboard}, ensure_ascii=False),
                            encoding="utf-8")


def _cover(site: Path, page_name: str, headline: str, date_label: str, sub: str, day_mood: str = "calm") -> str:
    """Снимает обложку выпуска в site/og/ и возвращает её адрес для og:image; без обложки — пустая строка."""
    cover = site / "og" / f"{Path(page_name).stem}.png"
    if not make_cover(headline, date_label, sub, cover, day_mood):
        return ""
    return f"{os.environ['SITE_URL'].rstrip('/')}/og/{cover.name}"


def build(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    today = now.astimezone(DISPLAY_TZ).date()
    # Слот — первым делом: панель здоровья подписывает им и сбой до любой работы (нет ключа, битый конфиг),
    # иначе упавший вечерний выпуск выглядел бы утренним
    slot = issue_slot(now, os.environ.get("ISSUE_SLOT", ""))
    health.RUN.slot = slot
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    settings = config["digest"]
    api_key = os.environ["GEMINI_API_KEY"]
    feedback_url = os.environ.get("FEEDBACK_URL", "")

    raw, failures = fetch_all(config["source"], now)
    if len(failures) == len(config["source"]):
        # Скорее всего легла сеть раннера; пустой выпуск выглядел бы как тихий день, а падение придёт оповещением
        raise SystemExit(f"ни один источник не собран: {failures}")
    store = SeenStore(args.state_dir / "seen.json")
    new = store.take_new(raw, now)
    merged = dedupe(new)
    stats = {"raw": len(raw), "new": len(new), "merged": len(merged)}
    log.info("собрано %d, новых %d, после склейки %d, сбоев %d", len(raw), len(new), len(merged), len(failures))
    if not merged:
        # Повтор уже вышедшего выпуска без новых записей затёр бы его страницу пустой
        raise SystemExit("новых записей нет — выпуск не собран, вышедший не трогаю")

    archive = Archive(args.state_dir / "issues.json")
    stories = Stories(args.state_dir / "stories.json")
    received = fetch_feedback(feedback_url, os.environ.get("FEEDBACK_TOKEN", ""))
    votes, reads = received or ([], set())
    feedback = resolve_votes(votes, archive)
    log.info("оценок читателя: %d, прочитано записей: %d", len(feedback), len(reads))

    main_models, side_models = model_chains(settings)
    digest = select(merged, settings["audience"], main_models, api_key,
                    preferences=preferences_prompt(feedback), stories=stories.active(today))
    if not digest.entries:
        raise SystemExit("модель не выбрала ни одного события — пустой выпуск не отправляю")
    try:
        extras(digest, merged, settings["audience"], side_models, api_key, stories=stories.active(today))
    except LlmError as exc:
        # «Ещё за день» и фишки — надстройка: без них выпуск всё равно выходит
        log.error("«Ещё за день» и фишки не собраны: %s", exc)
    log.info("в выпуске %d событий, ещё за день %d, в шуме %d", len(digest.entries), len(digest.more), len(digest.noise))
    # Сначала настоящий адрес статьи: по нему ищется обсуждение на HN и скачивается текст для разбора.
    # Строкам «Ещё за день» он нужен как ссылка: Google News без JS не открывается
    resolve_gnews([e.item for e in digest.entries] + [m.item for m in digest.more])
    attach_discussions([e.item for e in digest.entries])
    texts = fetch_articles([e.item.url for e in digest.entries])
    verify_copies(digest.entries, texts)
    try:
        explain(digest.entries, texts, settings["audience"], main_models, api_key)
    except LlmError as exc:
        # Подробные разборы — надстройка: без них выпуск всё равно выходит, с кнопкой на первоисточник
        log.error("подробные разборы не сгенерированы: %s", exc)

    # Утренний выпуск сохраняет прежнее имя страницы, вечерний — свою: ссылки на старые выпуски не ломаются
    page_name = f"{today:%Y-%m-%d}.html" if slot == "morning" else f"{today:%Y-%m-%d}-evening.html"
    morning_page = f"{today:%Y-%m-%d}.html"
    # Оценённая запись тоже считается увиденной: чтобы поставить 👍/👎, её прочитали
    opened = reads | {v["id"] for v in votes} if received is not None else None
    missed = missed_in_morning(archive, today, opened) if slot == "evening" else []
    record_issue(archive, stories, today, digest.headline, page_name, digest.entries, slot)
    health.record_events([e.item.source for e in digest.entries])

    date_label = f"{human_date(now)}{' · вечер' if slot == 'evening' else ''}"
    og_image = _cover(args.site, page_name, digest.headline, date_label, issue_summary(digest.entries),
                      mood([e.section for e in digest.entries]))
    # «Твой стек» — без модели, прямо с GitHub; недоступный репозиторий только пишет в лог
    releases = fetch_stack(config.get("stack", []), now)
    page = render_page(digest, stats, now, failures, feedback_url=feedback_url, issues=archive.issues, page_name=page_name,
                       slot=slot, missed=missed, morning_page=morning_page, og_image=og_image, stack=releases)
    args.site.mkdir(parents=True, exist_ok=True)
    for name in (page_name, "index.html"):
        (args.site / name).write_text(page, encoding="utf-8")
    (args.site / "archive.html").write_text(render_archive(archive.issues, now), encoding="utf-8")
    (args.site / "tips.html").write_text(render_tips(archive.issues, now, feedback_url), encoding="utf-8")
    for name, html in render_story_pages(archive.issues, stories.stories, now).items():
        (args.site / name).write_text(html, encoding="utf-8")

    page_url = f"{os.environ['SITE_URL'].rstrip('/')}/{page_name}"
    label = "Утренний дайджест" if slot == "morning" else "Вечерний дайджест"
    morning_url = f"{os.environ['SITE_URL'].rstrip('/')}/{morning_page}"
    message = build_message(digest, page_url, now, label=label, missed=missed, morning_url=morning_url)
    _write_message("message.json", message, page_url, build_keyboard(digest))
    log.info("страница %s, сообщение в out/message.json", args.site / page_name)
    # State сохраняем только после успешного отбора: упавший запуск не должен «съесть» новости
    if not args.dry_run:
        store.save(now=now)
        archive.save()
        stories.save(today=today)


def weekly(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    today = now.astimezone(DISPLAY_TZ).date()
    settings = tomllib.loads(args.config.read_text(encoding="utf-8"))["digest"]
    archive = Archive(args.state_dir / "issues.json")
    stories = Stories(args.state_dir / "stories.json")
    digest, stats = summarize_week(archive.issues, stories.stories, today, settings["audience"],
                                   model_chains(settings)[0], os.environ["GEMINI_API_KEY"])
    start = today - timedelta(days=WEEK_DAYS - 1)
    period = (f"{start.day}–{today.day} {MONTHS[today.month - 1]}" if start.month == today.month
              else f"{start.day} {MONTHS[start.month - 1]} – {today.day} {MONTHS[today.month - 1]}")
    page_name = f"week-{today:%Y-%m-%d}.html"
    og_image = _cover(args.site, page_name, digest.headline, f"Итог недели · {period}", issue_summary(digest.entries, "week"))
    page = render_page(digest, stats, now, [], issues=archive.issues, kind="week", period=period, page_name=page_name,
                       og_image=og_image)
    args.site.mkdir(parents=True, exist_ok=True)
    (args.site / page_name).write_text(page, encoding="utf-8")
    page_url = f"{os.environ['SITE_URL'].rstrip('/')}/{page_name}"
    _write_message("weekly.json", build_message(digest, page_url, now, label="Итог недели"), page_url, None)
    log.info("итог недели: %d сюжетов, страница %s", len(digest.entries), args.site / page_name)


def alert(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    settings = config["digest"]
    sources = [{**src, "max_age_hours": breaking.ALERT_HOURS} for src in config["source"] if src.get("alert")]
    items, failures = fetch_all(sources, now)
    if len(failures) == len(sources):
        # Молча выходить нельзя: срочные новости перестали бы приходить, и никто бы этого не заметил
        raise SystemExit(f"ни один источник срочных новостей не собран: {failures}")
    state = breaking.AlertState.load(args.state_dir / "alerts.json")
    candidates = breaking.find_candidates(breaking.split_undated(items, state, now), state, now)
    log.info("свежих записей %d, кандидатов в срочные %d, отправлено сегодня %d",
             len(items), len(candidates), state.sent_today(now))
    if candidates and state.sent_today(now) < breaking.DAILY_CAP:
        attach_discussions(candidates)
        # Без текста статьи у модели только заголовок, и «что это меняет» она додумывает
        texts = fetch_articles([c.url for c in candidates])
        api_key = os.environ["GEMINI_API_KEY"]
        # Отсев — на Lite, его квоты хватает на каждый час; 3.6 — запас, если Lite недоступен
        picked = None
        try:
            raw = generate_first(breaking.build_triage_prompt(candidates, state, settings["audience"], texts),
                                 breaking.TRIAGE_SCHEMA, [settings["lite_model"], settings["extras_model"]], api_key,
                                 call="triage")
        except LlmError as exc:
            # Как и с перепроверкой ниже: упавшая проверка не сохраняла состояние и через час тратила на тех же
            # кандидатов запасную 3.6. Откладываем их; выпуск их всё равно увидит
            log.error("срочные не отсеяны, откладываю %d: %s", len(candidates), exc)
            urgent: list[Item] = []
            rejected = list(candidates)
        else:
            urgent, rejected = breaking.triage(raw, candidates)
        if urgent:
            # Срочное может разбудить читателя ночью — перепроверку и текст пишет лучшая модель, 3.6 только запас.
            # До Flash доходит редко: почти все прогоны заканчиваются на отсеве Lite
            try:
                raw = generate_first(breaking.build_prompt(urgent, state, settings["audience"], texts),
                                     breaking.ALERT_SCHEMA, [settings["model"], settings["extras_model"]], api_key,
                                     call="alert")
            except LlmError as exc:
                # 21.10: 3.8 и 3.6 отвечали 503, проверка падала, не сохранив состояние, и каждый час тратила квоту
                # 3.8 и 3.6 — квоту утреннего выпуска — на ту же новость. Откладываем её как отклонённую: охлаждение
                # то же, станет громче — вернётся; выпуск её всё равно подхватит, а сбой виден на панели здоровья
                log.error("срочное не перепроверено, откладываю %d: %s", len(urgent), exc)
                rejected += urgent
            else:
                picked, refused = breaking.choose(raw, urgent)
                rejected += refused
        for item in rejected:
            state.mark_rejected(item, now)
        if picked:
            item, title, text = picked
            send(breaking.build_alert(item, title, text), item.url, os.environ["TELEGRAM_BOT_TOKEN"],
                 os.environ["TELEGRAM_CHAT_ID"], silent=breaking.is_quiet(now))
            state.mark_sent(item, title, now)
            log.info("срочная новость отправлена: %s", title)
    state.save(now)


SNAPSHOTS = Path("eval/snapshots")


def snapshot(args: argparse.Namespace) -> None:
    """Замораживает сегодняшние новости: на одном и том же срезе разные версии промпта можно сравнивать цифрами."""
    now = datetime.now(timezone.utc)
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    raw, failures = fetch_all(config["source"], now)
    path = SNAPSHOTS / f"{now.astimezone(DISPLAY_TZ):%Y-%m-%d-%H}.json"
    evaluate.save_snapshot(path, dedupe(raw), now)
    log.info("срез %s: %d записей, сбоев %d", path, len(raw), len(failures))


def run_eval(args: argparse.Namespace) -> None:
    """Прогоняет текущие промпты отбора и разборов по срезам и печатает признаки слопа; в state ничего не пишет."""
    settings = tomllib.loads(args.config.read_text(encoding="utf-8"))["digest"]
    api_key = os.environ["GEMINI_API_KEY"]
    paths = args.snapshots or sorted(SNAPSHOTS.glob("*.json"))
    if not paths:
        raise SystemExit(f"срезов нет — сначала python -m digest snapshot (папка {SNAPSHOTS})")
    print("срез | прогон | событий | ещё за день | фишки | важность 8+ | пустые оценки | канцелярит | "
          "выдуманные числа | прогнозы | why=пересказ | шаблоны why")
    for path in paths:
        items, taken, texts = evaluate.load_snapshot(path)
        for run in range(1, args.runs + 1):
            # Отбор склеивает дубли прямо в объектах записей: каждому прогону — своя копия, иначе следующий
            # прогон и сохранённый срез видели бы уже склеенные записи
            pool = copy.deepcopy(items)
            main_models, side_models = model_chains(settings)
            digest = select(pool, settings["audience"], main_models, api_key)
            try:
                extras(digest, pool, settings["audience"], side_models, api_key)
            except LlmError as exc:
                log.error("«Ещё за день» в прогоне %d не собраны: %s", run, exc)
            missing = [e.item.url for e in digest.entries if e.item.url not in texts]
            if missing:
                # Тексты статей докачиваем один раз и кладём в срез: следующие прогоны видят тот же вход
                texts.update(fetch_articles(missing))
                evaluate.save_snapshot(path, items, taken, texts)
            try:
                explain(digest.entries, texts, settings["audience"], main_models, api_key)
            except LlmError as exc:
                log.error("разборы в прогоне %d не сгенерированы: %s", run, exc)
            result = evaluate.report(digest, texts)
            print(f"{path.stem} | {run} | {result['entries']} | {result['more']} | {result['tips']} | "
                  f"{result['high_importance']} | {result['slop']} | {result['officialese']} | "
                  f"{result['invented_numbers']} | {result['forecasts']} | {result['why_repeats']} | "
                  f"{result['why_templates']}")
            for row in result["rows"]:
                problems = [f"слоп: {', '.join(row['slop'])}" if row["slop"] else "",
                            f"нет в источнике: {', '.join(row['invented'])}" if row["invented"] else "",
                            f"прогноз: {', '.join(row['forecasts'])}" if row["forecasts"] else "",
                            "why повторяет пересказ" if row["why_repeats"] else "",
                            f"шаблон why: {', '.join(row['templates'])}" if row["templates"] else ""]
                if any(problems):
                    print(f"    · {row['title']} — {'; '.join(p for p in problems if p)}")
            out = OUT / "eval" / f"{path.stem}-run{run}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"headline": digest.headline, "lead": digest.lead, "entries": [
                {"section": e.section, "title": e.title, "importance": e.importance, "summary": e.summary, "why": e.why,
                 "details": e.details.__dict__ if e.details else None, "url": e.item.url,
                 "also": [url for _, url in e.item.also]} for e in digest.entries],
                "more": [{"title": m.title, "url": m.item.url, "also": [url for _, url in m.item.also]}
                         for m in digest.more]},
                ensure_ascii=False, indent=1), encoding="utf-8")


def send_message(args: argparse.Namespace) -> None:
    message = json.loads((OUT / args.message).read_text(encoding="utf-8"))
    send(message["text"], message["url"], os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"],
         message.get("keyboard"), large_preview=True)
    log.info("отправлено в Telegram: %s", message["url"])


def run_with_health(args: argparse.Namespace) -> None:
    """Запуск команды с записью здоровья: вызовы Gemini и сбои источников сохраняются и при падении выпуска —
    панель нужна именно в такие дни. eval и --dry-run здоровье не пишут: их квоту видно в AI Studio."""
    kind = args.run.__name__
    if kind not in ("build", "weekly", "alert") or getattr(args, "dry_run", False):
        args.run(args)
        return
    health.reset()
    outcome = "ok"
    try:
        args.run(args)
    except SystemExit as exc:
        # Свои сообщения об остановке («новых записей нет») безопасны и объясняют сбой лучше имени исключения
        outcome = str(exc.code) if exc.code not in (None, 0) else "ok"
        raise
    except Exception as exc:
        outcome = type(exc).__name__
        raise
    finally:
        _save_health(args, kind, outcome)


def _save_health(args: argparse.Namespace, kind: str, outcome: str) -> None:
    now = datetime.now(timezone.utc)
    folder = args.state_dir / "health"
    try:
        health.save(folder / f"{kind}.json", kind, now, outcome, keep_sources=kind != "alert")
        if kind == "alert":
            # Страницу публикует только digest.yml: срочные попадут на неё со следующим выпуском
            return
        settings = tomllib.loads(args.config.read_text(encoding="utf-8"))["digest"]
        args.site.mkdir(parents=True, exist_ok=True)
        (args.site / "health.html").write_text(render_health(health.load_all(folder, now), settings, now), encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        # Панель — надстройка: её сбой не должен ронять выпуск или подменять исходную ошибку
        log.error("здоровье запуска не сохранено: %s", exc)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx на INFO пишет полный URL запроса, а в URL Telegram Bot API лежит токен; лог Actions публичный
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="ИИ-дайджест")
    commands = parser.add_subparsers(required=True)
    for name, run, help_text in (("build", build, "собрать, отфильтровать, сверстать страницу и сообщение"),
                                 ("weekly", weekly, "собрать итог недели из архива"),
                                 ("alert", alert, "проверить срочные новости и отправить громкую сразу")):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--config", type=Path, default=Path("sources.toml"))
        cmd.add_argument("--state-dir", type=Path, default=Path("state"))
        cmd.add_argument("--site", type=Path, default=Path("site"))
        cmd.set_defaults(run=run)
        if name == "build":
            cmd.add_argument("--dry-run", action="store_true", help="не обновлять state")
    snap_cmd = commands.add_parser("snapshot", help="заморозить сегодняшние новости для проверки промптов")
    snap_cmd.add_argument("--config", type=Path, default=Path("sources.toml"))
    snap_cmd.set_defaults(run=snapshot)
    eval_cmd = commands.add_parser("eval", help="прогнать промпты по срезам и посчитать признаки слопа")
    eval_cmd.add_argument("snapshots", nargs="*", type=Path, help="файлы срезов; по умолчанию все из eval/snapshots")
    eval_cmd.add_argument("--runs", type=int, default=1, help="прогонов на срез: ответ модели от раза к разу плавает")
    eval_cmd.add_argument("--config", type=Path, default=Path("sources.toml"))
    eval_cmd.set_defaults(run=run_eval)
    send_cmd = commands.add_parser("send", help="отправить сообщение из out/ в Telegram")
    send_cmd.add_argument("--message", default="message.json")
    send_cmd.set_defaults(run=send_message)

    args = parser.parse_args()
    configure_logging()
    load_env(Path(".env"))
    run_with_health(args)


if __name__ == "__main__":
    main()
