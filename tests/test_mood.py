import pytest

from digest import cover
from digest.mood import MOODS, mood

# Разделы реальных выпусков из state/issues.json в порядке выпуска (первое событие — герой)
ALARM_2110_EVENING = ["security", "security", "business", "security", "product", "security", "dev", "business",
                      "business", "model", "voices", "tips", "tips"]
RELEASE_1710_EVENING = ["model", "business", "model", "business", "model", "business", "business", "dev"]
QUIET_2010_MORNING = ["business", "business", "dev", "business", "product", "dev", "business", "product", "voices"]
CALM_1610 = ["business", "business", "product", "business", "model", "business", "product", "dev", "business"]


@pytest.mark.parametrize(("sections", "expected"), [
    (ALARM_2110_EVENING, "alarm"),
    (RELEASE_1710_EVENING, "release"),
    # 9 записей, но одна — авторский пост: событий 8, безопасности нет — тихий день
    (QUIET_2010_MORNING, "quiet"),
    (CALM_1610, "calm"),
    # Фишка и пост не бывают героем: герой — первое событие
    (["tips", "voices", "model", "business"], "release"),
    (["business", "security", "security", "security", "dev", "dev", "dev", "dev", "dev", "dev"], "alarm"),
])
def test_mood_comes_from_sections_without_the_model(sections, expected):
    assert mood(sections) == expected


def test_three_archive_issues_get_three_different_moods_on_page_and_cover():
    from datetime import datetime, timezone

    from digest.llm import Digest, Entry
    from digest.models import Item
    from digest.render import render_page

    covers, pages = set(), set()
    for sections in (ALARM_2110_EVENING, RELEASE_1710_EVENING, QUIET_2010_MORNING):
        entries = [Entry(Item(source="x", title=f"t{n}", url=f"https://x.io/{n}", category="news"), s, 7, f"t{n}", "s", "")
                   for n, s in enumerate(sections)]
        page = render_page(Digest("h", "l", entries), {"raw": 1, "new": 1, "merged": 1},
                           datetime(2026, 10, 21, 3, tzinfo=timezone.utc), failures=[])
        name = mood(sections)
        assert f'data-mood="{name}"' in page and MOODS[name].label in page and MOODS[name].sky in page
        pages.add(name)
        html = cover.cover_html("H", "21 октября", "9 событий", name)
        assert MOODS[name].sky in html
        covers.add(html)
    assert len(pages) == 3 and len(covers) == 3


def test_calm_mood_keeps_the_landscape_untouched_and_cover_still_finds_it():
    assert MOODS["calm"].sky == "transparent"
    # cover.py вырезает пейзаж из page.html регуляркой: тон настроения — отдельный слой, а не правка SVG
    assert cover.LANDSCAPE in cover.cover_html("H", "d", "s", "alarm")


def test_weekly_summary_has_no_daily_mood():
    from datetime import datetime, timezone

    from digest.llm import Digest, Entry
    from digest.models import Item
    from digest.render import render_page

    # Пять бизнес-сюжетов недели — не «тихий день»: у сводки за 140 событий настроения дня нет
    entries = [Entry(Item(source="x", title=f"t{n}", url=f"https://x.io/{n}", category="news"), "business", 7, f"t{n}",
                     "s", "") for n in range(5)]
    page = render_page(Digest("h", "l", entries), {"issues": 14, "events": 140}, datetime(2026, 10, 25, 3, tzinfo=timezone.utc),
                       failures=[], kind="week", period="19–25 октября")
    assert 'data-mood="calm"' in page and "тихий день" not in page
