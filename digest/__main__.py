import argparse
import json
import logging
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from digest.dedup import dedupe
from digest.fetch import fetch_all, fetch_articles
from digest.llm import LlmError, explain, select
from digest.render import DISPLAY_TZ, render_page
from digest.state import SeenStore
from digest.telegram import build_message, send

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


def build(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    raw, failures = fetch_all(config["source"], now)
    store = SeenStore(args.state)
    new = store.take_new(raw, now)
    merged = dedupe(new)
    stats = {"raw": len(raw), "new": len(new), "merged": len(merged)}
    log.info("собрано %d, новых %d, после склейки %d, сбоев %d", len(raw), len(new), len(merged), len(failures))

    settings = config["digest"]
    digest = select(merged, settings["audience"], settings["model"], os.environ["GEMINI_API_KEY"])
    log.info("в выпуске %d событий, в шуме %d", len(digest.entries), len(digest.noise))
    texts = fetch_articles([e.item.url for e in digest.entries])
    try:
        explain(digest.entries, texts, settings["audience"], settings["model"], os.environ["GEMINI_API_KEY"])
    except LlmError as exc:
        # Подробные разборы — надстройка: без них выпуск всё равно выходит, с кнопкой на первоисточник
        log.error("подробные разборы не сгенерированы: %s", exc)

    page_name = f"{now.astimezone(DISPLAY_TZ):%Y-%m-%d}.html"
    page = render_page(digest, stats, now, failures)
    args.site.mkdir(parents=True, exist_ok=True)
    for name in (page_name, "index.html"):
        (args.site / name).write_text(page, encoding="utf-8")

    page_url = f"{os.environ['SITE_URL'].rstrip('/')}/{page_name}"
    OUT.mkdir(exist_ok=True)
    (OUT / "message.json").write_text(json.dumps({"text": build_message(digest, page_url, now), "url": page_url},
                                                 ensure_ascii=False), encoding="utf-8")
    log.info("страница %s, сообщение в out/message.json", args.site / page_name)
    # State сохраняем только после успешного отбора: упавший запуск не должен «съесть» новости
    if not args.dry_run:
        store.save(now=now)


def send_message(_: argparse.Namespace) -> None:
    message = json.loads((OUT / "message.json").read_text(encoding="utf-8"))
    send(message["text"], message["url"], os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
    log.info("отправлено в Telegram: %s", message["url"])


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx на INFO пишет полный URL запроса, а в URL Telegram Bot API лежит токен; лог Actions публичный
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="ИИ-дайджест")
    commands = parser.add_subparsers(required=True)
    build_cmd = commands.add_parser("build", help="собрать, отфильтровать, сверстать страницу и сообщение")
    build_cmd.add_argument("--config", type=Path, default=Path("sources.toml"))
    build_cmd.add_argument("--state", type=Path, default=Path("state/seen.json"))
    build_cmd.add_argument("--site", type=Path, default=Path("site"))
    build_cmd.add_argument("--dry-run", action="store_true", help="не обновлять state")
    build_cmd.set_defaults(run=build)
    commands.add_parser("send", help="отправить out/message.json в Telegram").set_defaults(run=send_message)

    args = parser.parse_args()
    configure_logging()
    load_env(Path(".env"))
    args.run(args)


if __name__ == "__main__":
    main()
