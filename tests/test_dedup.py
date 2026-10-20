from digest.dedup import canonical_key, dedupe
from digest.models import Item


def make(source, title, url, score=None):
    return Item(source=source, title=title, url=url, category="news", score=score)


def test_tracking_params_and_www_are_ignored():
    a = canonical_key("https://www.example.com/post/?utm_source=x&ref=hn#top")
    b = canonical_key("https://example.com/post")
    assert a == b


def test_arxiv_abs_pdf_and_versions_collapse_to_one_id():
    keys = {
        canonical_key("https://arxiv.org/abs/2510.01234v2"),
        canonical_key("http://arxiv.org/pdf/2510.01234"),
        canonical_key("https://huggingface.co/papers/2510.01234"),
    }
    assert keys == {"arxiv:2510.01234"}


def test_same_url_from_two_sources_merges_and_keeps_alternates():
    items = [
        make("openai", "Introducing GPT-6", "https://openai.com/index/gpt-6/"),
        make("hn", "Introducing GPT-6", "https://openai.com/index/gpt-6?utm_source=hn", score=812),
    ]
    out = dedupe(items)
    assert len(out) == 1
    assert out[0].source == "openai"
    assert [s for s, _ in out[0].also] == ["hn"]
    assert out[0].score == 812


def test_near_identical_titles_from_different_urls_merge():
    items = [
        make("techcrunch", "Meta releases Llama 5 with open weights", "https://techcrunch.com/a"),
        make("verge", "Meta releases Llama 5, with open weights!", "https://theverge.com/b"),
    ]
    assert len(dedupe(items)) == 1


def test_different_stories_stay_separate():
    items = [
        make("gh", "vLLM v0.12.0", "https://github.com/vllm-project/vllm/releases/tag/v0.12.0"),
        make("gh", "Ollama v0.9.0", "https://github.com/ollama/ollama/releases/tag/v0.9.0"),
    ]
    assert len(dedupe(items)) == 2


def test_similar_titles_within_one_source_are_not_fuzzy_merged():
    items = [
        make("gh_vllm", "vLLM v0.12.0", "https://github.com/vllm-project/vllm/releases/tag/v0.12.0"),
        make("gh_vllm", "vLLM v0.12.1", "https://github.com/vllm-project/vllm/releases/tag/v0.12.1"),
    ]
    assert len(dedupe(items)) == 2


def test_coverage_counts_distinct_sources_of_a_story():
    from digest.dedup import coverage
    merged = dedupe([
        make("techcrunch", "Meta releases Llama 5 with open weights", "https://techcrunch.com/a"),
        make("verge", "Meta releases Llama 5, with open weights!", "https://theverge.com/b"),
        make("hn", "Meta releases Llama 5", "https://techcrunch.com/a"),
    ])
    assert coverage(merged[0]) == 3


def test_merged_story_keeps_loudest_hn_discussion():
    a = make("techcrunch", "Meta releases Llama 5 with open weights", "https://techcrunch.com/a")
    b = make("hn", "Meta releases Llama 5", "https://techcrunch.com/a")
    b.hn_points, b.hn_comments, b.hn_url = 300, 90, "https://news.ycombinator.com/item?id=1"
    merged = dedupe([a, b])[0]
    assert (merged.hn_points, merged.hn_comments, merged.hn_url) == (300, 90, "https://news.ycombinator.com/item?id=1")


def test_aggregator_yields_primary_to_direct_link_of_the_same_story():
    # Google News стоит в sources.toml выше изданий и индексирует их же статьи с теми же заголовками
    items = [
        make("The Verge", "ChatGPT can now read your Chrome tabs", "https://news.google.com/rss/articles/abc"),
        make("verge", "ChatGPT can now read your Chrome tabs", "https://www.theverge.com/ai/1012510"),
    ]
    out = dedupe(items)
    assert len(out) == 1
    assert out[0].url == "https://www.theverge.com/ai/1012510"
    assert [s for s, _ in out[0].also] == ["The Verge"]


def test_open_article_beats_a_paywalled_one_even_when_listed_later():
    # Techmeme стоит в списке выше изданий и ведёт на оригинал — часто Bloomberg; основной должна стать открытая статья
    paywalled = Item(source="techmeme", title="Nvidia agrees to buy Arm for $40 billion", url="https://www.bloomberg.com/a",
                     category="news")
    open_one = Item(source="the_guardian", title="Nvidia agrees to buy Arm for $40 billion", url="https://www.theguardian.com/a",
                    category="news")
    [merged] = dedupe([paywalled, open_one])
    assert merged.url == "https://www.theguardian.com/a" and [s for s, _ in merged.also] == ["techmeme"]
