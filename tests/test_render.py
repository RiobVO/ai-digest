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
