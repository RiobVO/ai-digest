from datetime import datetime, timedelta, timezone

from digest.models import Item
from digest.state import SeenStore

NOW = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)


def item(source, url, published=NOW):
    return Item(source=source, title=url, url=url, category="news", published=published)


def test_items_are_returned_once(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    batch = [item("openai", "https://openai.com/a")]
    assert len(store.take_new(batch, NOW)) == 1
    store.save()
    again = SeenStore(tmp_path / "seen.json")
    assert again.take_new(batch, NOW) == []


def test_undated_items_of_a_new_source_are_seeded_silently(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    old = [item("anthropic", "https://anthropic.com/news/a", None)]
    assert store.take_new(old, NOW) == []
    fresh = old + [item("anthropic", "https://anthropic.com/news/b", None)]
    assert [i.url for i in store.take_new(fresh, NOW)] == ["https://anthropic.com/news/b"]


def test_dated_items_of_a_new_source_are_not_swallowed(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    assert len(store.take_new([item("mistral", "https://mistral.ai/news/x")], NOW)) == 1


def test_old_entries_are_pruned_on_save(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.take_new([item("openai", "https://openai.com/old")], NOW - timedelta(days=60))
    store.save(now=NOW)
    assert "openai.com/old" not in path.read_text(encoding="utf-8")


def test_link_still_on_page_is_not_resurrected_after_retention(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.take_new([item("anthropic", "https://anthropic.com/news/a", None)], NOW - timedelta(days=60))
    # ссылка всё ещё на странице: каждое наблюдение продлевает срок
    store.take_new([item("anthropic", "https://anthropic.com/news/a", None)], NOW)
    store.save(now=NOW)
    assert SeenStore(path).take_new([item("anthropic", "https://anthropic.com/news/a", None)], NOW) == []


def test_same_title_from_another_source_next_day_is_skipped(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    first = Item(source="openai", title="Introducing GPT-6", url="https://openai.com/gpt-6", category="lab", published=NOW)
    store.take_new([first], NOW)
    store.save(now=NOW)
    repost = Item(source="verge", title="Introducing GPT-6!", url="https://theverge.com/x", category="news", published=NOW)
    assert SeenStore(path).take_new([repost], NOW + timedelta(days=1)) == []


def test_same_title_within_one_run_is_left_for_dedupe(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    blog = Item(source="openai", title="Introducing GPT-6", url="https://openai.com/gpt-6", category="lab", published=NOW)
    hn = Item(source="hn", title="Introducing GPT-6", url="https://news.ycombinator.com/item?id=1", category="community",
              published=NOW, score=900)
    assert len(store.take_new([blog, hn], NOW)) == 2
