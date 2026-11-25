import json
from datetime import date

from digest.llm import Entry
from digest.memory import Archive, Stories, preferences_prompt, record_issue, resolve_votes
from digest.models import Item


def entry(title, section="model", source="hf_blog", story="", story_name=""):
    e = Entry(Item(source=source, title=title, url=f"https://x/{title}", category="lab"), section, 8, title, "s", "")
    e.story, e.story_name = story, story_name
    return e


def test_archive_assigns_stable_entry_ids_and_keeps_newest_first(tmp_path):
    archive, stories = Archive(tmp_path / "issues.json"), Stories(tmp_path / "stories.json")
    first = [entry("A"), entry("B")]
    record_issue(archive, stories, date(2026, 10, 15), "День 1", "2026-10-15.html", first)
    second = [entry("C")]
    record_issue(archive, stories, date(2026, 10, 16), "День 2", "2026-10-16.html", second)
    archive.save()
    assert [e.id for e in first] == ["20261015-0", "20261015-1"]
    reloaded = Archive(tmp_path / "issues.json")
    assert [i["date"] for i in reloaded.issues] == ["2026-10-16", "2026-10-15"]
    assert reloaded.entry("20261015-1")["title"] == "B"


def test_rebuilding_the_same_day_replaces_the_issue(tmp_path):
    archive = Archive(tmp_path / "issues.json")
    archive.add(date(2026, 10, 16), "v1", "2026-10-16.html", [entry("A")])
    archive.add(date(2026, 10, 16), "v2", "2026-10-16.html", [entry("B")])
    assert [i["headline"] for i in archive.issues] == ["v2"]


def test_votes_resolve_to_titles_and_last_vote_wins(tmp_path):
    archive = Archive(tmp_path / "issues.json")
    record_issue(archive, Stories(tmp_path / "s.json"), date(2026, 10, 16), "h", "p.html",
                 [entry("Llama 5", source="meta_ai"), entry("Promo", section="industry")])
    votes = [
        {"id": "20261016-0", "v": "up", "at": "2026-10-16T09:00:00Z"},
        {"id": "20261016-1", "v": "up", "at": "2026-10-16T09:00:00Z"},
        {"id": "20261016-1", "v": "down", "at": "2026-10-16T10:00:00Z"},
        {"id": "19990101-0", "v": "up", "at": "2026-10-16T10:00:00Z"},
    ]
    resolved = resolve_votes(votes, archive)
    assert [(r["title"], r["v"]) for r in resolved] == [("Llama 5", "up"), ("Promo", "down")]
    prompt = preferences_prompt(resolved)
    assert "Llama 5" in prompt.split("Не зашло")[0] and "Promo" in prompt.split("Не зашло")[1]


def test_votes_are_a_hint_not_a_rule():
    # Один 👎 статье не должен выключать целую тему или топить крупное событие
    prompt = preferences_prompt([{"title": "Paper", "section": "paper", "source": "hf_papers", "v": "down", "at": "1"}])
    assert "подсказка, а не правило" in prompt


def test_empty_feedback_gives_empty_prompt():
    assert preferences_prompt([]) == ""


def test_stories_continue_and_count_days(tmp_path):
    stories = Stories(tmp_path / "stories.json")
    day1 = [entry("Nemotron 3.5 вышел", story="", story_name="Nemotron 3.5")]
    stories.apply(day1, date(2026, 10, 15), "2026-10-15.html")
    sid = day1[0].story
    assert sid and day1[0].story_day == 1 and day1[0].story_prev == []

    day2 = [entry("Ollama поддержала Nemotron", story=sid)]
    stories.apply(day2, date(2026, 10, 16), "2026-10-16.html")
    assert day2[0].story_day == 2
    assert day2[0].story_prev == [("2026-10-15", "Nemotron 3.5 вышел")]
    stories.save()
    active = Stories(tmp_path / "stories.json").active(date(2026, 10, 17))
    assert [s["name"] for s in active] == ["Nemotron 3.5"]


def test_long_story_name_is_cut_on_a_word_boundary(tmp_path):
    stories = Stories(tmp_path / "stories.json")
    e = entry("t", story_name="Disney забирает более 60 сотрудников Character.AI за $180 млн")
    stories.apply([e], date(2026, 10, 18), "2026-10-18.html")
    assert e.story_name == "Disney забирает более 60 сотрудников Character.AI за $180…"


def test_story_without_a_name_from_the_model_stays_nameless_instead_of_a_headline(tmp_path):
    # 21.10: у 14 сюжетов из 72 «имя» было обрезанным заголовком первой новости
    stories = Stories(tmp_path / "stories.json")
    e = entry("Disney забирает более 60 сотрудников Character.AI за $180 млн")
    stories.apply([e], date(2026, 10, 18), "2026-10-18.html")
    assert e.story and e.story_name == "" and stories.stories[e.story]["name"] == ""


def test_tricks_and_engineer_posts_do_not_start_stories(tmp_path):
    stories = Stories(tmp_path / "stories.json")
    tip, voice, event = entry("Флаг", section="tips"), entry("Пост", section="voices"), entry("Релиз", story_name="R")
    stories.apply([tip, voice, event], date(2026, 10, 21), "2026-10-21.html")
    assert tip.story == "" and voice.story == "" and event.story
    assert list(stories.stories) == [event.story]


def test_unknown_story_id_from_model_starts_a_new_story(tmp_path):
    stories = Stories(tmp_path / "stories.json")
    e = entry("X", story="s999", story_name="Икс")
    stories.apply([e], date(2026, 10, 16), "2026-10-16.html")
    assert e.story != "s999" and e.story_day == 1


def test_stale_stories_are_not_active(tmp_path):
    stories = Stories(tmp_path / "stories.json")
    stories.apply([entry("Old", story_name="Old")], date(2026, 10, 1), "2026-10-01.html")
    assert stories.active(date(2026, 10, 16)) == []


def test_fetch_feedback_sends_token_returns_votes_and_reads_and_survives_outage(monkeypatch):
    import httpx
    from digest import memory
    seen = {}
    vote = {"id": "20261016-0", "v": "up", "at": "2026-10-16T09:00:00Z"}

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"votes": [vote], "reads": [{"id": "20261016-1", "at": "2026-10-16T09:00:00Z"}]}

    def fake_get(url, headers, timeout):
        seen["url"], seen["auth"] = url, headers["Authorization"]
        return Resp()
    monkeypatch.setattr(memory.httpx, "get", fake_get)
    assert memory.fetch_feedback("https://fb.dev", "tok") == ([vote], {"20261016-1"})
    assert seen == {"url": "https://fb.dev/votes", "auth": "Bearer tok"}

    def down(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(memory.httpx, "get", down)
    # Сбой и отсутствие настройки — «неизвестно», а не «ничего не прочитано»
    assert memory.fetch_feedback("https://fb.dev", "tok") is None
    assert memory.fetch_feedback("", "") is None


def test_new_story_id_reaches_the_archive(tmp_path):
    from digest.memory import record_issue
    archive, stories = Archive(tmp_path / "i.json"), Stories(tmp_path / "s.json")
    e = entry("Nemotron вышел", story_name="Nemotron")
    record_issue(archive, stories, date(2026, 10, 15), "h", "p.html", [e])
    assert archive.entry(e.id)["story"] == e.story != ""


def test_same_day_rebuild_never_reuses_ids_and_old_votes_keep_their_meaning(tmp_path):
    from digest.memory import record_issue
    archive, stories = Archive(tmp_path / "i.json"), Stories(tmp_path / "s.json")
    a = entry("A")
    record_issue(archive, stories, date(2026, 10, 16), "v1", "p.html", [a])
    b = entry("B")
    record_issue(archive, stories, date(2026, 10, 16), "v2", "p.html", [b])
    assert a.id != b.id
    assert archive.entry(a.id)["title"] == "A"
    assert [i["headline"] for i in archive.issues] == ["v2"]
    assert [e["title"] for e in archive.issues[0]["entries"]] == ["B"]


def test_same_day_rebuild_replaces_story_events_of_that_day(tmp_path):
    from digest.memory import record_issue
    archive, stories = Archive(tmp_path / "i.json"), Stories(tmp_path / "s.json")
    record_issue(archive, stories, date(2026, 10, 16), "v1", "p.html", [entry("A", story_name="Линия A")])
    record_issue(archive, stories, date(2026, 10, 16), "v2", "p.html", [entry("B", story_name="Линия B")])
    names = [s["name"] for s in stories.active(date(2026, 10, 16))]
    assert names == ["Линия B"]


def test_evening_issues_of_the_two_a_day_era_stay_above_their_morning(tmp_path):
    # До 25.11.2026 выпусков было два в день: в архиве они остались и при добавлении нового не должны перемешаться
    path = tmp_path / "issues.json"
    old = [{"date": "2026-10-17", "slot": "morning", "headline": "Утро", "page": "2026-10-17.html", "entries": []},
           {"date": "2026-10-17", "slot": "evening", "headline": "Вечер", "page": "2026-10-17-evening.html", "entries": []}]
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    archive, stories = Archive(path), Stories(tmp_path / "stories.json")
    record_issue(archive, stories, date(2026, 10, 18), "Новый", "2026-10-18.html", [entry("A")])
    assert [i["headline"] for i in archive.issues] == ["Новый", "Вечер", "Утро"]
    # Новому выпуску метка времени суток не нужна: он в день один
    assert "slot" not in archive.issues[0]


def test_archive_keeps_tip_text_topic_and_verified_copy_for_the_arsenal(tmp_path):
    archive = Archive(tmp_path / "issues.json")
    tip = entry("Флаг песочницы", section="tips", source="gh_claude_code")
    tip.why, tip.copy, tip.topic = "Chromium в песочнице.", "sandbox.allowNestedUserNamespaces", "claude_code"
    record_issue(archive, Stories(tmp_path / "s.json"), date(2026, 10, 21), "h", "p.html", [entry("A"), tip])
    event, saved = archive.issues[0]["entries"]
    assert {k: saved[k] for k in ("summary", "why", "copy", "topic")} == {
        "summary": "s", "why": "Chromium в песочнице.", "copy": "sandbox.allowNestedUserNamespaces", "topic": "claude_code"}
    # События в архиве остаются короткими: текст карточки нужен только арсеналу фишек
    assert "summary" not in event and "copy" not in event


def test_archive_keeps_the_tip_steps_from_its_breakdown(tmp_path):
    from digest.llm import Details
    archive = Archive(tmp_path / "issues.json")
    tip = entry("Хук перед коммитом", section="tips", source="gh_claude_code")
    # Шаги разбор вытаскивает из полного текста статьи; без них фишка в Telegram — «склонируй и используй» (22.10)
    tip.details = Details("", ["Открой settings.json", "Добавь хук PreToolUse"], "", "", [])
    no_breakdown = entry("Флаг", section="tips")
    record_issue(archive, Stories(tmp_path / "s.json"), date(2026, 10, 22), "h", "p.html", [tip, no_breakdown])
    saved, bare = archive.issues[0]["entries"]
    assert saved["steps"] == ["Открой settings.json", "Добавь хук PreToolUse"] and bare["steps"] == []


def test_archive_remembers_that_a_tip_copy_is_from_an_unverified_source(tmp_path):
    archive = Archive(tmp_path / "issues.json")
    tip = entry("Промпт", section="tips", source="reddit_tips")
    tip.copy, tip.copy_untrusted = "Prove to me this works", True
    record_issue(archive, Stories(tmp_path / "s.json"), date(2026, 10, 23), "h", "p.html", [tip])
    assert archive.issues[0]["entries"][0]["copy_untrusted"] is True
