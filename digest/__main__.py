import argparse
import json
import logging
import os
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest.dedup import dedupe
from digest.fetch import fetch_all, fetch_articles
from digest.llm import LlmError, explain, select
from digest.memory import Archive, Stories, fetch_votes, preferences_prompt, record_issue, resolve_votes
from digest.render import DISPLAY_TZ, MONTHS, render_archive, render_page
from digest.state import SeenStore
from digest.telegram import build_keyboard, build_message, send
from digest.weekly import WEEK_DAYS, summarize_week

log = logging.getLogger("digest")
OUT = Path("out")


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


def build(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    today = now.astimezone(DISPLAY_TZ).date()
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    settings = config["digest"]
    api_key = os.environ["GEMINI_API_KEY"]
    feedback_url = os.environ.get("FEEDBACK_URL", "")

    raw, failures = fetch_all(config["source"], now)
    store = SeenStore(args.state_dir / "seen.json")
    new = store.take_new(raw, now)
    merged = dedupe(new)
    stats = {"raw": len(raw), "new": len(new), "merged": len(merged)}
    log.info("собрано %d, новых %d, после склейки %d, сбоев %d", len(raw), len(new), len(merged), len(failures))

    archive = Archive(args.state_dir / "issues.json")
    stories = Stories(args.state_dir / "stories.json")
    feedback = resolve_votes(fetch_votes(feedback_url, os.environ.get("FEEDBACK_TOKEN", "")), archive)
    log.info("оценок читателя: %d", len(feedback))

    digest = select(merged, settings["audience"], settings["model"], api_key,
                    preferences=preferences_prompt(feedback), stories=stories.active(today))
    log.info("в выпуске %d событий, в шуме %d", len(digest.entries), len(digest.noise))
    texts = fetch_articles([e.item.url for e in digest.entries])
    try:
        explain(digest.entries, texts, settings["audience"], settings["model"], api_key)
    except LlmError as exc:
        # Подробные разборы — надстройка: без них выпуск всё равно выходит, с кнопкой на первоисточник
        log.error("подробные разборы не сгенерированы: %s", exc)

    page_name = f"{today:%Y-%m-%d}.html"
    record_issue(archive, stories, today, digest.headline, page_name, digest.entries)

    page = render_page(digest, stats, now, failures, feedback_url=feedback_url, issues=archive.issues, page_name=page_name)
    args.site.mkdir(parents=True, exist_ok=True)
    for name in (page_name, "index.html"):
        (args.site / name).write_text(page, encoding="utf-8")
    (args.site / "archive.html").write_text(render_archive(archive.issues, now), encoding="utf-8")

    page_url = f"{os.environ['SITE_URL'].rstrip('/')}/{page_name}"
    _write_message("message.json", build_message(digest, page_url, now), page_url, build_keyboard(digest))
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
    digest, stats = summarize_week(archive.issues, stories.stories, today, settings["audience"], settings["model"],
                                   os.environ["GEMINI_API_KEY"])
    start = today - timedelta(days=WEEK_DAYS - 1)
    period = (f"{start.day}–{today.day} {MONTHS[today.month - 1]}" if start.month == today.month
              else f"{start.day} {MONTHS[start.month - 1]} – {today.day} {MONTHS[today.month - 1]}")
    page_name = f"week-{today:%Y-%m-%d}.html"
    page = render_page(digest, stats, now, [], issues=archive.issues, kind="week", period=period, page_name=page_name)
    args.site.mkdir(parents=True, exist_ok=True)
    (args.site / page_name).write_text(page, encoding="utf-8")
    page_url = f"{os.environ['SITE_URL'].rstrip('/')}/{page_name}"
    _write_message("weekly.json", build_message(digest, page_url, now, label="Итог недели"), page_url, None)
    log.info("итог недели: %d сюжетов, страница %s", len(digest.entries), args.site / page_name)


def send_message(args: argparse.Namespace) -> None:
    message = json.loads((OUT / args.message).read_text(encoding="utf-8"))
    send(message["text"], message["url"], os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"],
         message.get("keyboard"))
    log.info("отправлено в Telegram: %s", message["url"])


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx на INFO пишет полный URL запроса, а в URL Telegram Bot API лежит токен; лог Actions публичный
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="ИИ-дайджест")
    commands = parser.add_subparsers(required=True)
    for name, run, help_text in (("build", build, "собрать, отфильтровать, сверстать страницу и сообщение"),
                                 ("weekly", weekly, "собрать итог недели из архива")):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--config", type=Path, default=Path("sources.toml"))
        cmd.add_argument("--state-dir", type=Path, default=Path("state"))
        cmd.add_argument("--site", type=Path, default=Path("site"))
        cmd.set_defaults(run=run)
        if name == "build":
            cmd.add_argument("--dry-run", action="store_true", help="не обновлять state")
    send_cmd = commands.add_parser("send", help="отправить сообщение из out/ в Telegram")
    send_cmd.add_argument("--message", default="message.json")
    send_cmd.set_defaults(run=send_message)

    args = parser.parse_args()
    configure_logging()
    load_env(Path(".env"))
    args.run(args)


if __name__ == "__main__":
    main()
