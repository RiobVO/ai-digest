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


GNEWS_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>OpenAI ships GPT-6 - Reuters</title><link>https://news.google.com/rss/articles/abc</link>
<pubDate>Fri, 16 Oct 2026 08:00:00 GMT</pubDate><description>&lt;a href="x"&gt;junk&lt;/a&gt;</description>
<source url="https://www.reuters.com">Reuters</source></item>
</channel></rss>"""


def test_google_news_takes_publisher_as_source_and_strips_it_from_title():
    src = {"name": "gnews_ai", "category": "news", "publisher_in_title": True}
    item = parse_feed(GNEWS_RSS, src)[0]
    assert item.title == "OpenAI ships GPT-6"
    assert item.source == "Reuters"
    assert item.summary == ""


def test_hn_hits_carry_points_comments_and_thread():
    payload = {"hits": [{"title": "GPT-6", "url": "https://openai.com/x", "points": 540, "num_comments": 212,
                         "objectID": "7", "created_at": "2026-10-16T01:00:00Z"}]}
    item = parse_hn(payload, {"name": "hn", "category": "community"})[0]
    assert (item.hn_points, item.hn_comments) == (540, 212)
    assert item.hn_url == "https://news.ycombinator.com/item?id=7"


def test_hn_discussion_picks_the_loudest_thread_for_url():
    from digest.fetch import parse_discussion
    payload = {"hits": [{"points": 12, "num_comments": 3, "objectID": "1"},
                        {"points": 410, "num_comments": 150, "objectID": "2"}]}
    assert parse_discussion(payload) == (410, 150, "https://news.ycombinator.com/item?id=2")
    assert parse_discussion({"hits": []}) is None


TECHMEME_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Intuit buys Sequence (The Times)</title><link>https://www.techmeme.com/261016/p13#a261016p13</link>
<pubDate>Fri, 16 Oct 2026 08:00:00 GMT</pubDate><description><![CDATA[<p><a href="https://www.techmeme.com/261016/p13#a261016p13">
<img src="https://www.techmeme.com/img/x.jpg"></a></p><p><a href="https://www.thetimes.com/">The Times</a>:
<a href="https://www.thetimes.com/business/article/intuit-sequence">Intuit buys Sequence</a></p>]]></description></item>
<item><title>Only Techmeme links here</title><link>https://www.techmeme.com/261016/p14#a261016p14</link>
<pubDate>Fri, 16 Oct 2026 08:00:00 GMT</pubDate><description><![CDATA[<a href="https://www.techmeme.com/261016/p14">x</a>]]></description></item>
</channel></rss>"""


def test_techmeme_entry_points_to_original_article_not_its_cluster_page():
    src = {"name": "techmeme", "category": "news", "original_link": True}
    items = parse_feed(TECHMEME_RSS, src)
    # Главная издания (путь «/») — не статья; берём первую внешнюю ссылку с путём
    assert items[0].url == "https://www.thetimes.com/business/article/intuit-sequence"
    assert items[1].url == "https://www.techmeme.com/261016/p14#a261016p14"


def test_article_texts_skip_aggregators_and_treat_stub_pages_as_empty(monkeypatch):
    import httpx
    from digest import fetch
    requested = []

    def handler(request):
        requested.append(str(request.url))
        body = "<article>" + "Полный текст статьи. " * 40 + "</article>" if "long" in request.url.path else "<p>Google News</p>"
        return httpx.Response(200, text=body)

    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    texts = fetch.fetch_articles(["https://news.google.com/rss/articles/abc", "https://x.io/stub", "https://x.io/long"])
    assert texts["https://news.google.com/rss/articles/abc"] == ""
    assert texts["https://x.io/stub"] == ""
    assert texts["https://x.io/long"].startswith("Полный текст статьи.")
    assert requested == ["https://x.io/stub", "https://x.io/long"]


def test_items_remember_the_site_of_their_feed(monkeypatch):
    import httpx
    from digest import fetch
    rss = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
           b'<item><title>Own post</title><link>https://www.example.com/post</link></item>'
           b'<item><title>Linked elsewhere</title><link>https://evil.example/x</link></item></channel></rss>')
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=rss)), **kw))
    src = {"name": "blog", "type": "rss", "category": "engineer", "url": "https://www.example.com/feed"}
    items, failures = fetch.fetch_all([src], datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert failures == [] and {i.feed_host for i in items} == {"example.com"}
