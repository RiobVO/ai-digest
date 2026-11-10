import logging
import subprocess

from digest import cover


def test_cover_escapes_headline_and_shows_the_date_in_the_colour_of_the_day():
    from digest.mood import MOODS
    from digest.render import NBSP

    html = cover.cover_html("Модели <script> и чипы", "19.10", "alarm")
    # Заголовок набран, как на странице: союз не повисает в конце строки
    assert f"<h1>Модели &lt;script&gt; и{NBSP}чипы</h1>" in html and "<script>" not in html
    assert "<b>19.10</b>" in html
    # Дата и круги — цветом дня; у обычного дня и у итога недели это цвет текста
    assert html.count(MOODS["alarm"].color) == 2 and MOODS["calm"].color not in html.replace("color:#151515;font-family", "")
    assert cover.cover_html("H", "45").count(f"color:{MOODS['calm'].color}") >= 2


def test_cover_is_set_in_the_page_fonts_and_keeps_the_headline_within_three_lines():
    html = cover.cover_html("H", "19.10")
    # display=block: Chrome снимает кадр, когда шрифт применён, а не запасным шрифтом (FONT_WAIT_MS даёт на это время)
    assert "family=Sofia+Sans+Extra+Condensed" in html and "display=block" in html and "Inter" not in html
    assert "-webkit-line-clamp:3" in html and cover.FONT_WAIT_MS >= 5000
    # Отвергнутого пейзажа и прежних плашек на картинке нет
    assert "svg+xml" not in html and "мин чтения" not in html


def test_missing_chrome_skips_the_cover_without_failing_the_issue(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(cover, "find_chrome", lambda: None)
    with caplog.at_level(logging.ERROR):
        assert cover.make_cover("H", "19.10", tmp_path / "og" / "x.png") is False
    assert "Chrome" in caplog.text


def test_crashed_chrome_skips_the_cover(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(cover, "find_chrome", lambda: "chrome")

    def crash(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "chrome")

    monkeypatch.setattr(cover.subprocess, "run", crash)
    with caplog.at_level(logging.ERROR):
        assert cover.make_cover("H", "19.10", tmp_path / "x.png") is False
    assert "обложка" in caplog.text


def test_unwritable_cover_folder_skips_the_cover(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(cover, "find_chrome", lambda: "chrome")
    blocker = tmp_path / "og"
    blocker.write_text("файл на месте папки")
    with caplog.at_level(logging.ERROR):
        assert cover.make_cover("H", "19.10", blocker / "x.png") is False
    assert "обложка" in caplog.text
