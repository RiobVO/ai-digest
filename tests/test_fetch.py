from datetime import datetime, timezone

import pytest

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

def test_latest_only_skips_nightly_and_preview_builds():
    # Gemini CLI каждую ночь выпускает nightly и раз в неделю preview; в дайджест идёт стабильный релиз
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Release v0.67.0-nightly.20261021.gd00859d6b</title><link href="https://github.com/g/c/releases/tag/a"/><updated>2026-10-21T05:00:00Z</updated></entry>
<entry><title>Release v0.66.0-preview.0</title><link href="https://github.com/g/c/releases/tag/b"/><updated>2026-10-20T23:00:00Z</updated></entry>
<entry><title>Release v0.65.0</title><link href="https://github.com/g/c/releases/tag/c"/><updated>2026-10-20T21:00:00Z</updated></entry>
</feed>"""
    items = parse_feed(atom, {"name": "gh_gemini_cli", "category": "release", "title_prefix": "Gemini CLI",
                              "latest_only": True})
    assert [i.title for i in items] == ["Gemini CLI Release v0.65.0"]


def test_prerelease_is_a_version_suffix_not_a_word_in_the_title():
    # Стабильный релиз «v0.66.0 — preview features» — не пре-релиз: иначе источник промолчал бы до следующего релиза
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>v0.66.0 \xe2\x80\x94 preview features, dev tools</title><link href="https://github.com/g/c/releases/tag/a"/><updated>2026-10-21T05:00:00Z</updated></entry>
<entry><title>v0.65.0rc2</title><link href="https://github.com/g/c/releases/tag/b"/><updated>2026-10-20T23:00:00Z</updated></entry>
</feed>"""
    items = parse_feed(atom, {"name": "gh_x", "category": "release", "latest_only": True})
    assert [i.title for i in items] == ["v0.66.0 \u2014 preview features, dev tools"]


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


GNEWS_PAGE = '<div jscontroller="aLI87" data-n-a-id="CBMiAAA" data-n-a-ts="1760680000" data-n-a-sg="AZ5r3eQsig"></div>'
GNEWS_REPLY = (')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://www.politico.com/news/2026/10/16/anthropic\\",1]",'
               'null,null,null,"generic"],["di",14],["af.httprm",13,"-23076",148]]')


def gnews_transport(requests, page=GNEWS_PAGE, reply=GNEWS_REPLY):
    import httpx

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, text=page)
        return httpx.Response(200, text=reply)
    return httpx.MockTransport(handler)


def test_chosen_google_news_link_resolves_to_the_article(monkeypatch):
    import httpx
    from digest import fetch
    from digest.models import Item
    requests = []
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, "Client", lambda **kw: real_client(transport=gnews_transport(requests), **kw))
    story = Item(source="Politico", title="t", url="https://news.google.com/rss/articles/CBMiAAA?oc=5", category="news")
    direct = Item(source="verge", title="t", url="https://www.theverge.com/x", category="news")
    fetch.resolve_gnews([story, direct])
    assert story.url == "https://www.politico.com/news/2026/10/16/anthropic"
    assert direct.url == "https://www.theverge.com/x"
    # Подпись берётся со страницы статьи, адрес — из batchexecute; прямые ссылки никуда не ходят
    assert requests == [("GET", "/rss/articles/CBMiAAA"), ("POST", "/_/DotsSplashUi/data/batchexecute")]


def test_google_news_link_stays_when_decoding_fails(monkeypatch):
    import httpx
    from digest import fetch
    from digest.models import Item
    real_client = httpx.Client
    link = "https://news.google.com/rss/articles/CBMiAAA"
    for page, reply in (("<html>no signature</html>", GNEWS_REPLY), (GNEWS_PAGE, ")]}'\n\nunexpected"),
                        (GNEWS_PAGE, GNEWS_REPLY.replace("https://www.politico.com", "javascript:alert(1)//x")),
                        # Другая структура JSON и адрес, на котором падает разбор URL, — тоже отказ одного события
                        (GNEWS_PAGE, ")]}'\n\n{}"), (GNEWS_PAGE, ")]}'\n\n[{}]"),
                        (GNEWS_PAGE, ")]}'\n\n" + "[" * 50000 + "0" + "]" * 50000),  # RecursionError в json.loads
                        (GNEWS_PAGE, GNEWS_REPLY.replace("https://www.politico.com", "https://[broken"))):
        monkeypatch.setattr(fetch.httpx, "Client",
                            lambda page=page, reply=reply, **kw: real_client(transport=gnews_transport([], page, reply), **kw))
        story = Item(source="Politico", title="t", url=link, category="news")
        fetch.resolve_gnews([story])
        assert story.url == link


def test_html_page_without_matching_links_is_a_failure_not_a_quiet_day():
    # Блог сменил вёрстку — шаблон ссылок больше не совпадает. На странице новостей ссылки есть всегда,
    # поэтому ноль — поломка: без исключения источник молча пропал бы из выпусков навсегда
    src = {"name": "anthropic", "category": "lab", "url": "https://www.anthropic.com/news",
           "link_pattern": r"/blog/[a-z0-9-]+"}
    with pytest.raises(ValueError, match="anthropic"):
        parse_html(ANTHROPIC_HTML, src)


def test_feed_without_entries_is_a_failure_not_a_quiet_day():
    # Адрес ленты начал отдавать HTML-страницу или заглушку: feedparser не падает, а возвращает ноль записей
    src = {"name": "verge", "category": "news", "url": "https://theverge.com/rss"}
    with pytest.raises(ValueError, match="verge"):
        parse_feed(b"<html><body>Moved</body></html>", src)


def test_hn_source_can_ask_for_show_hn_instead_of_all_stories(monkeypatch):
    import httpx
    from digest import fetch
    asked = []

    def handler(request):
        asked.append(request.url.params["tags"])
        return httpx.Response(200, json={"hits": []})
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    sources = [{"name": "hn", "type": "hn", "category": "community", "url": "https://hn.algolia.com/api/v1/search_by_date"},
               {"name": "hn_show", "type": "hn", "category": "community", "tags": "show_hn",
                "url": "https://hn.algolia.com/api/v1/search_by_date"}]
    fetch.fetch_all(sources, datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert asked == ["story", "show_hn"]


REDDIT_ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>My Claude Code setup</title><link href="https://www.reddit.com/r/ClaudeCode/comments/1/setup/"/>
<updated>2026-10-22T05:00:00Z</updated><content type="html">&lt;p&gt;Step one: add a PreToolUse hook. """ + "detail " * 300 + """&lt;/p&gt;</content></entry>
</feed>"""


def test_feed_keeps_the_full_post_only_for_sources_marked_body_from_feed():
    src = {"name": "reddit_tips", "category": "community"}
    # Reddit отвечает раннерам GitHub 403: полный текст поста есть только в самой ленте (22.10)
    item = parse_feed(REDDIT_ATOM.encode(), {**src, "body_from_feed": True})[0]
    assert item.body.startswith("Step one: add a PreToolUse hook.") and len(item.body) > 2000
    assert len(item.summary) <= 600
    assert parse_feed(REDDIT_ATOM.encode(), src)[0].body == ""


def test_feed_body_fills_only_articles_that_could_not_be_fetched():
    from digest.fetch import fill_from_feed
    from digest.models import Item
    blocked = Item(source="reddit", title="t", url="https://www.reddit.com/r/x/1", category="community", body="полный пост")
    fetched = Item(source="reddit", title="t", url="https://www.reddit.com/r/x/2", category="community", body="из ленты")
    texts = {blocked.url: "", fetched.url: "статья со страницы"}
    fill_from_feed(texts, [blocked, fetched])
    assert texts == {blocked.url: "полный пост", fetched.url: "статья со страницы"}
