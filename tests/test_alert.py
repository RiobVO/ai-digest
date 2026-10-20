from datetime import datetime, timedelta, timezone

from digest.alert import (AlertState, build_alert, choose, find_candidates, is_quiet, publishers, split_undated)
from digest.models import Item

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def item(source, title, url, category="news", **kw):
    return Item(source=source, title=title, url=url, category=category, published=NOW - timedelta(hours=1), **kw)


def test_aggregators_do_not_count_as_independent_publishers():
    story = item("techmeme", "GPT-6 is out", "https://openai.com/gpt-6",
                 also=[("hn", "https://openai.com/gpt-6"), ("Reuters", "https://r/1"), ("verge", "https://v/1")])
    assert publishers(story) == 2


def test_candidates_are_official_posts_loud_stories_or_hot_hn():
    items = [
        item("openai", "Introducing GPT-6", "https://openai.com/gpt-6", category="lab"),
        item("verge", "Small gadget review", "https://v/2"),
        item("hn", "Rust 2.0 released", "https://rust-lang.org/2", hn_points=450),
        item("Reuters", "Nvidia buys Arm", "https://r/9", also=[("Bloomberg", "https://b/9"), ("CNBC", "https://c/9")]),
    ]
    found = find_candidates(items, AlertState({}), NOW)
    assert {c.title for c in found} == {"Introducing GPT-6", "Rust 2.0 released", "Nvidia buys Arm"}


def test_story_already_alerted_is_never_a_candidate_again():
    state = AlertState({})
    gpt = item("openai", "Introducing GPT-6", "https://openai.com/gpt-6", category="lab")
    state.mark_sent(gpt, "GPT-6", NOW)
    again = item("Reuters", "Introducing GPT-6", "https://openai.com/gpt-6?utm_source=x",
                 also=[("Bloomberg", "https://b/1"), ("CNBC", "https://c/1")])
    assert find_candidates([again], state, NOW + timedelta(hours=2)) == []


def test_rejected_story_waits_for_cooldown_unless_it_got_much_louder():
    state = AlertState({})
    quiet = item("openai", "Minor policy update", "https://openai.com/policy", category="lab")
    state.mark_rejected(quiet, NOW)
    assert find_candidates([quiet], state, NOW + timedelta(hours=1)) == []
    louder = item("openai", "Minor policy update", "https://openai.com/policy", category="lab",
                  also=[("Reuters", "https://r/2"), ("Bloomberg", "https://b/2"), ("CNBC", "https://c/2")])
    assert len(find_candidates([louder], state, NOW + timedelta(hours=1))) == 1
    assert len(find_candidates([quiet], state, NOW + timedelta(hours=7))) == 1


def test_undated_posts_seen_on_first_run_are_a_baseline_not_news():
    state = AlertState({})
    old = Item(source="anthropic", title="Old post", url="https://anthropic.com/news/old", category="lab")
    assert split_undated([old], state, NOW) == []
    new = Item(source="anthropic", title="New model", url="https://anthropic.com/news/new", category="lab")
    fresh = split_undated([old, new], state, NOW + timedelta(hours=1))
    assert [i.title for i in fresh] == ["New model"]


def test_undated_post_still_on_the_blog_is_never_forgotten(tmp_path):
    path = tmp_path / "alerts.json"
    state = AlertState({"primed": True}, path)

    def post() -> Item:
        return Item(source="anthropic", title="Claude 9", url="https://anthropic.com/news/c9", category="lab")

    split_undated([post()], state, NOW)
    later = NOW + timedelta(days=40)
    split_undated([post()], state, later)
    state.save(later)
    reloaded = AlertState.load(path)
    assert split_undated([post()], reloaded, later + timedelta(hours=1)) == []


def test_new_undated_post_stays_in_window_until_judged():
    state = AlertState({"primed": True})

    def post() -> Item:
        return Item(source="anthropic", title="New model", url="https://anthropic.com/news/new", category="lab")

    assert len(split_undated([post()], state, NOW)) == 1
    # Не уместился в пятёрку кандидатов или модель его ещё не решала — через час он снова в окне
    assert len(split_undated([post()], state, NOW + timedelta(hours=1))) == 1
    assert split_undated([post()], state, NOW + timedelta(hours=7)) == []


def test_daily_cap_counts_alerts_by_tashkent_date():
    state = AlertState({})
    late = datetime(2026, 10, 16, 18, 30, tzinfo=timezone.utc)  # 23:30 в Ташкенте
    state.mark_sent(item("openai", "A", "https://openai.com/a", category="lab"), "A", late)
    assert state.sent_today(late) == 1
    assert state.sent_today(late + timedelta(hours=1)) == 0  # в Ташкенте уже 17 октября


def test_quiet_hours_in_tashkent():
    assert is_quiet(datetime(2026, 10, 16, 20, tzinfo=timezone.utc))  # 01:00 Ташкент
    assert not is_quiet(datetime(2026, 10, 16, 6, tzinfo=timezone.utc))  # 11:00 Ташкент


def test_model_choice_takes_first_urgent_and_rejects_the_rest_it_judged():
    cands = [item("openai", "A", "https://openai.com/a", category="lab"),
             item("openai", "B", "https://openai.com/b", category="lab"),
             item("openai", "C", "https://openai.com/c", category="lab")]
    raw = {"items": [{"index": 0, "urgent": False, "title": "", "text": ""},
                     {"index": 1, "urgent": True, "title": "Б <срочно>", "text": "Текст."},
                     {"index": 9, "urgent": True, "title": "мусор", "text": ""}]}
    picked, rejected = choose(raw, cands)
    assert picked == (cands[1], "Б <срочно>", "Текст.")
    assert rejected == [cands[0]]


def test_alert_message_is_escaped_and_links_source_and_thread():
    story = item("openai", "GPT-6", "https://openai.com/gpt-6", category="lab",
                 also=[("Reuters", "https://r/1"), ("Bloomberg", "https://b/1")], hn_points=900, hn_comments=400,
                 hn_url="https://news.ycombinator.com/item?id=5")
    text = build_alert(story, "OpenAI выкатила <GPT-6>", "Коротко & по делу.")
    assert "&lt;GPT-6&gt;" in text and "Коротко &amp; по делу." in text
    assert '<a href="https://openai.com/gpt-6">' in text
    assert '<a href="https://news.ycombinator.com/item?id=5">' in text
    assert "3 издания" in text and "HN 900" in text


def test_alert_drops_non_web_links():
    story = item("openai", "X", "javascript:alert(1)", category="lab")
    assert "javascript:" not in build_alert(story, "X", "y")


def test_silent_send_disables_notification(monkeypatch):
    import httpx
    from digest import telegram
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(json)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(telegram.httpx, "post", fake_post)
    telegram.send("t", "https://x.io", "tok", "1", silent=True)
    assert sent["disable_notification"] is True


def test_issue_link_preview_is_large_and_alert_preview_stays_small(monkeypatch):
    import httpx
    from digest import telegram
    sent = []

    def fake_post(url, json, timeout):
        sent.append(json["link_preview_options"])
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(telegram.httpx, "post", fake_post)
    telegram.send("t", "https://x.io/issue.html", "tok", "1", large_preview=True)
    telegram.send("t", "https://news.example/a", "tok", "1")
    assert sent[0].get("prefer_large_media") is True and "prefer_small_media" not in sent[0]
    assert sent[1].get("prefer_small_media") is True
