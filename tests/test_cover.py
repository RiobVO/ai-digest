import logging
import subprocess

from digest import cover


def test_cover_escapes_headline_and_shows_date_and_summary():
    html = cover.cover_html("Модели <script> и чипы", "19 октября · вечер", "9 событий · ≈2 мин чтения")
    assert "Модели &lt;script&gt; и чипы" in html and "<script>" not in html
    assert "19 октября · вечер" in html and "9 событий · ≈2 мин чтения" in html


def test_missing_chrome_skips_the_cover_without_failing_the_issue(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(cover, "find_chrome", lambda: None)
    with caplog.at_level(logging.ERROR):
        assert cover.make_cover("H", "19 октября", "9 событий", tmp_path / "og" / "x.png") is False
    assert "Chrome" in caplog.text


def test_crashed_chrome_skips_the_cover(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(cover, "find_chrome", lambda: "chrome")

    def crash(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "chrome")

    monkeypatch.setattr(cover.subprocess, "run", crash)
    with caplog.at_level(logging.ERROR):
        assert cover.make_cover("H", "19 октября", "9 событий", tmp_path / "x.png") is False
    assert "обложка" in caplog.text


def test_unwritable_cover_folder_skips_the_cover(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(cover, "find_chrome", lambda: "chrome")
    blocker = tmp_path / "og"
    blocker.write_text("файл на месте папки")
    with caplog.at_level(logging.ERROR):
        assert cover.make_cover("H", "19 октября", "9 событий", blocker / "x.png") is False
    assert "обложка" in caplog.text
