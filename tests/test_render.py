from datetime import datetime, timezone

from digest.llm import Digest, Entry
from digest.models import Item
from digest.render import render_page
from digest.telegram import TG_LIMIT, build_message

WHEN = datetime(2026, 10, 16, 3, tzinfo=timezone.utc)
STATS = {"raw": 138, "new": 118, "merged": 118}


def sample() -> Digest:
    top = Item(source="hf_blog", title="Muse", url="https://hf.co/blog/muse", category="lab",
               also=[("hn", "https://news.ycombinator.com/item?id=1")])
    paper = Item(source="hf_papers", title="Paper", url="https://arxiv.org/abs/2610.1", category="paper", score=83)
    entries = [
        Entry(top, "model", 9, "Meta выпустила <Muse> & co", "Модель на 816B.", "Можно запустить локально."),
        Entry(paper, "paper", 6, "Waypoint-OPD", "Метод дистилляции.", ""),
    ]
    noise = [Item(source="techcrunch", title="Save 50% <now>", url="https://tc/x", category="news")]
    return Digest("Открытые веса <день>", "Главное за сутки.", entries, noise)


def test_page_escapes_html_and_has_link_preview_tags():
    page = render_page(sample(), STATS, WHEN, failures=[])
    assert "<Muse>" not in page and "&lt;Muse&gt;" in page
    assert '<meta property="og:title" content="Открытые веса &lt;день&gt;">' in page
    assert "16 октября" in page


def test_page_puts_top_entry_in_lead_and_shows_noise_and_paper_votes():
    page = render_page(sample(), STATS, WHEN, failures=["verge: HTTPStatusError"])
    lead = page.index('id="main-story"')
    assert lead < page.index("Модель на 816B.") < page.index('id="papers"')
    assert "83 ▲" in page
    assert "Save 50% &lt;now&gt;" in page
    assert "verge" in page


def test_telegram_message_is_escaped_linked_and_short():
    text = build_message(sample(), "https://riobvo.github.io/ai-digest/2026-10-16.html", WHEN)
    assert "&lt;Muse&gt;" in text
    assert '<a href="https://riobvo.github.io/ai-digest/2026-10-16.html">Разбор целиком</a>' in text
    assert len(text) <= TG_LIMIT


def test_read_more_expands_inline_with_source_link():
    from digest.llm import Details
    digest = sample()
    digest.entries[1].details = Details("Суть <b>статьи</b>.", ["Пункт 1"], "pip install x", "Только английский.")
    page = render_page(digest, STATS, WHEN, failures=[])
    block = page[page.index("Суть"):]
    assert "Суть &lt;b&gt;статьи&lt;/b&gt;." in page
    assert "<summary" in page[:page.index("Суть")]
    assert "Пункт 1" in block and "pip install x" in block and "Только английский." in block
    assert 'href="https://arxiv.org/abs/2610.1"' in block


def test_non_http_urls_are_neutralized():
    digest = sample()
    digest.entries[0].item.url = "javascript:alert(document.domain)"
    digest.noise[0].url = "data:text/html,<script>x</script>"
    page = render_page(digest, STATS, WHEN, failures=[])
    assert "javascript:" not in page and "data:text" not in page


def test_telegram_message_stays_within_limit_with_huge_headline():
    digest = sample()
    digest.headline = "Я" * 5000
    digest.entries[0].why = "Ж" * 5000
    assert len(build_message(digest, "https://x.io/p.html", WHEN)) <= TG_LIMIT


def test_vote_buttons_carry_entry_ids_only_when_feedback_is_configured():
    digest = sample()
    digest.entries[0].id, digest.entries[1].id = "20261016-0", "20261016-1"
    page = render_page(digest, STATS, WHEN, failures=[], feedback_url="https://fb.example.workers.dev")
    assert 'data-id="20261016-0"' in page and 'data-id="20261016-1"' in page
    assert "https://fb.example.workers.dev" in page
    assert 'data-id="' not in render_page(digest, STATS, WHEN, failures=[])


def test_continuing_story_shows_day_and_previous_events():
    digest = sample()
    top = digest.entries[0]
    top.story_name, top.story_day = "Muse Spark", 3
    top.story_prev = [("2026-10-14", "Анонс Muse"), ("2026-10-15", "Бенчмарки <Muse>")]
    page = render_page(digest, STATS, WHEN, failures=[])
    assert "3-й день" in page and "Muse Spark" in page
    assert "Бенчмарки &lt;Muse&gt;" in page and "15 окт" in page


def test_sidebar_lists_recent_issues_and_archive_page_lists_all():
    from digest.render import render_archive
    issues = [{"date": "2026-10-16", "headline": "Сегодня", "page": "2026-10-16.html", "entries": [{}, {}]},
              {"date": "2026-10-15", "headline": "Вчера <b>", "page": "2026-10-15.html", "entries": [{}]}]
    page = render_page(sample(), STATS, WHEN, failures=[], issues=issues)
    assert 'href="2026-10-15.html"' in page and 'href="archive.html"' in page
    archive = render_archive(issues, WHEN)
    assert 'href="2026-10-16.html"' in archive and "Вчера &lt;b&gt;" in archive and "2 события" in archive


def test_telegram_numbers_items_and_keyboard_maps_numbers_to_entry_ids():
    from digest.telegram import build_keyboard
    digest = sample()
    digest.entries[0].id, digest.entries[1].id = "20261016-0", "20261016-1"
    digest.entries[1].story_name, digest.entries[1].story_day = "Waypoint", 2
    text = build_message(digest, "https://x.io/p.html", WHEN)
    assert "<b>1.</b>" in text and "<b>2.</b>" in text and "2-й день" in text
    keyboard = build_keyboard(digest)["inline_keyboard"]
    assert [b["text"] for b in keyboard[0]] == ["👍 1", "👍 2"]
    assert [b["callback_data"] for b in keyboard[1]] == ["d:20261016-0", "d:20261016-1"]
    assert all(len(b["callback_data"].encode()) <= 64 for row in keyboard for b in row)


def test_no_keyboard_without_entry_ids():
    from digest.telegram import build_keyboard
    assert build_keyboard(sample()) is None


def test_week_summary_uses_correct_plural_forms():
    page = render_page(sample(), {"issues": 1, "events": 3}, WHEN, failures=[], kind="week", period="10–16 октября")
    assert "<b>1</b><span>выпуск</span>" in page and "<b>3</b><span>события</span>" in page
    assert "<b>2</b><span>сюжета</span>" in page
