from datetime import datetime, timezone

from digest.fetch import parse_feed, parse_hf_papers, parse_hn, parse_html

RELEASES_ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>v0.40.6-rc1</title><link href="https://github.com/o/o/releases/tag/v0.40.6-rc1"/><updated>2026-10-16T05:00:00Z</updated></entry>
<entry><title>v0.40.5</title><link href="https://github.com/o/o/releases/tag/v0.40.5"/><updated>2026-10-15T05:00:00Z</updated></entry>
<entry><title>v0.40.4</title><link href="https://github.com/o/o/releases/tag/v0.40.4"/><updated>2026-10-14T05:00:00Z</updated></entry>
</feed>"""


def test_latest_only_keeps_newest_stable_release():
    src = {"name": "gh_ollama", "category": "release", "title_prefix": "Ollama", "latest_only": True}
    items = parse_feed(RELEASES_ATOM, src)
    assert [i.title for i in items] == ["Ollama v0.40.5"]

HF_PAYLOAD = [
    {"paper": {"id": "2610.22362", "title": "CoDA: Co-Evolution", "upvotes": 41,
               "ai_summary": "Short AI summary.", "summary": "Long abstract.",
               "publishedAt": "2026-10-14T20:00:00.000Z"}},
    {"paper": {"id": "2610.00001", "title": "Low signal paper", "upvotes": 1,
               "summary": "Abstract.", "publishedAt": "2026-10-14T20:00:00.000Z"}},
]


def test_hf_papers_filters_by_upvotes_and_uses_arxiv_url():
    items = parse_hf_papers(HF_PAYLOAD, {"name": "hf_papers", "category": "paper", "min_upvotes": 5})
    assert len(items) == 1
    item = items[0]
    assert item.url == "https://arxiv.org/abs/2610.22362"
    assert item.score == 41
    assert item.summary == "Short AI summary."
    assert item.published == datetime(2026, 10, 14, 20, tzinfo=timezone.utc)


def test_hn_story_without_url_points_to_discussion():
    payload = {"hits": [{"title": "Ask HN: LLM evals?", "url": None, "points": 120,
                         "objectID": "42", "created_at": "2026-10-14T23:59:37Z"}]}
    items = parse_hn(payload, {"name": "hn", "category": "community"})
    assert items[0].url == "https://news.ycombinator.com/item?id=42"
    assert items[0].score == 120


ANTHROPIC_HTML = """
<a href="/news/cyber-verification-program" class="x"><div>Expanding the Cyber Verification Program</div>
<span>Announcements</span><span>Oct 6, 2026</span></a>
<a href="/news/2026-us-midterms"><span>Oct 14, 2026</span><span>Policy</span><h3>Preparing for the 2026 US midterms</h3></a>
<a href="/news/2026-us-midterms">Read more</a>
<a href="/careers">Careers</a>
"""


def test_html_extracts_unique_links_with_clean_titles():
    src = {"name": "anthropic", "category": "lab", "url": "https://www.anthropic.com/news",
           "link_pattern": r"/news/[a-z0-9-]+", "strip_words": ["Announcements", "Policy"]}
    items = parse_html(ANTHROPIC_HTML, src)
    assert [(i.url, i.title) for i in items] == [
        ("https://www.anthropic.com/news/cyber-verification-program", "Expanding the Cyber Verification Program"),
        ("https://www.anthropic.com/news/2026-us-midterms", "Preparing for the 2026 US midterms"),
    ]
    assert all(i.published is None for i in items)


def test_article_text_drops_scripts_nav_and_caps_length():
    from digest.fetch import extract_text
    html = ("<html><head><style>.x{}</style><script>var a=1;</script></head><body>"
            "<nav>Menu Home About</nav><article><h1>Title</h1><p>First &amp; main paragraph.</p></article>"
            "<footer>© 2026</footer></body></html>")
    text = extract_text(html, limit=1000)
    assert text == "Title First & main paragraph."
    assert len(extract_text("<p>" + "word " * 5000 + "</p>", limit=200)) <= 200
