"""Обложка выпуска для превью ссылки в Telegram: HTML-карточка в стиле страницы, снятая headless Chrome."""
import logging
import os
import re
import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path
from string import Template

from digest.mood import MOODS

log = logging.getLogger(__name__)

TEMPLATES = Path(__file__).parent / "templates"
TEMPLATE = Template((TEMPLATES / "cover.html").read_text(encoding="utf-8"))
# Пейзаж берём из CSS героя страницы, а не копируем: сменится картинка на сайте — сменится и на обложке
_landscape = re.search(r'url\("data:image/svg\+xml,[^"]+"\)', (TEMPLATES / "page.html").read_text(encoding="utf-8"))
if _landscape is None:
    raise RuntimeError("в page.html не найден SVG-пейзаж героя — обложке не из чего взять фон")
LANDSCAPE = _landscape.group(0)
WIDTH, HEIGHT = 1200, 630
# Chrome снимает кадр по событию load, а Inter с Google Fonts к этому моменту может ещё не примениться:
# виртуальное время даёт шрифту догрузиться
FONT_WAIT_MS = 8000
CHROME_TIMEOUT_S = 60
# На раннерах GitHub (ubuntu-latest) Chrome предустановлен как google-chrome; локально путь задаёт CHROME_PATH
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def cover_html(headline: str, date_label: str, sub: str, mood: str = "calm") -> str:
    return TEMPLATE.substitute(landscape=LANDSCAPE, sky=MOODS[mood].sky, headline=escape(headline), date=escape(date_label),
                               sub=escape(sub))


def find_chrome() -> str | None:
    return os.environ.get("CHROME_PATH") or next(filter(None, map(shutil.which, CHROME_NAMES)), None)


def make_cover(headline: str, date_label: str, sub: str, out: Path, mood: str = "calm") -> bool:
    """Снимает обложку в out. Обложка — украшение: без неё выпуск выходит как раньше, поэтому сбой не роняет сборку."""
    chrome = find_chrome()
    if not chrome:
        log.error("Chrome не найден — выпуск выйдет без обложки")
        return False
    # Файловые сбои (нет прав на og/, кончилось место во временном каталоге) ловим так же, как сбой Chrome
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        # Пересборка того же выпуска: старая картинка не должна сойти за новую, если Chrome молча ничего не снимет
        out.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "cover.html"
            page.write_text(cover_html(headline, date_label, sub, mood), encoding="utf-8")
            # --no-sandbox: на ubuntu 24.04 AppArmor запрещает песочнице Chrome user namespaces; страница своя,
            # чужой в ней только заголовок, и он экранирован
            command = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                       f"--user-data-dir={Path(tmp) / 'profile'}", "--force-device-scale-factor=1",
                       f"--window-size={WIDTH},{HEIGHT}", f"--virtual-time-budget={FONT_WAIT_MS}",
                       f"--screenshot={out.resolve()}", page.resolve().as_uri()]
            subprocess.run(command, check=True, capture_output=True, timeout=CHROME_TIMEOUT_S)
    except (subprocess.SubprocessError, OSError) as exc:
        log.error("обложка не снята, выпуск выйдет без неё: %s", exc)
        return False
    if not out.exists():
        log.error("обложка не снята: Chrome завершился без файла %s", out)
        return False
    return True
