from datetime import date

from digest.weekly import build_week_prompt, collect_week, parse_week


def issue(day, entries):
    return {"date": day, "headline": f"h{day}", "page": f"{day}.html", "entries": entries}


def ev(eid, title, story, section="model"):
    return {"id": eid, "title": title, "section": section, "source": "hf_blog", "url": f"https://x/{eid}", "story": story}


ISSUES = [
    issue("2026-10-16", [ev("20261016-0", "Ollama поддержала Nemotron", "s1"), ev("20261016-1", "Статья", "s2", "paper")]),
    issue("2026-10-15", [ev("20261015-0", "NVIDIA выпустила Nemotron", "s1")]),
    issue("2026-10-01", [ev("20261001-0", "Давнее", "s9")]),
]
STORIES = {"s1": {"name": "Nemotron 3.5"}, "s2": {"name": "Статья X"}}


def test_collect_week_groups_by_story_and_skips_old_issues():
    groups = collect_week(ISSUES, STORIES, date(2026, 10, 16))
    assert [g["name"] for g in groups] == ["Nemotron 3.5", "Статья X"]
    assert [e["date"] for e in groups[0]["events"]] == ["2026-10-15", "2026-10-16"]


def test_week_prompt_lists_groups_with_dates():
    prompt = build_week_prompt(collect_week(ISSUES, STORIES, date(2026, 10, 16)), "dev")
    assert "[0] Nemotron 3.5" in prompt and "2026-10-15: NVIDIA выпустила Nemotron" in prompt


def test_parse_week_builds_timeline_from_archive_not_from_model():
    groups = collect_week(ISSUES, STORIES, date(2026, 10, 16))
    raw = {"headline": "Неделя Nemotron", "lead": "l", "items": [
        {"group": 0, "importance": 9, "title": "Nemotron 3.5 за неделю", "summary": "s", "narrative": "n", "why": "w"},
        {"group": 7, "importance": 5, "title": "мусор", "summary": "", "narrative": "", "why": ""},
    ]}
    digest = parse_week(raw, groups)
    assert len(digest.entries) == 1
    top = digest.entries[0]
    assert top.story_day == 2 and top.item.url == "https://x/20261016-0"
    assert top.details.points == ["15 окт — NVIDIA выпустила Nemotron", "16 окт — Ollama поддержала Nemotron"]


def test_week_maps_sections_from_older_issues():
    issues = [issue("2026-10-16", [ev("20261016-0", "Ollama", "s1", section="release"),
                                   ev("20261016-1", "Сделка", "s2", section="industry")])]
    groups = collect_week(issues, {}, date(2026, 10, 18))
    raw = {"headline": "h", "lead": "l", "items": [
        {"group": n, "importance": 8, "title": "t", "summary": "s", "why": ""} for n in range(len(groups))]}
    digest = parse_week(raw, groups)
    assert {e.section for e in digest.entries} == {"dev", "business"}


def test_week_puts_engineer_posts_after_story_lines():
    issues = [issue("2026-10-16", [ev("20261016-0", "Пост Ронахера", "s1", section="voices"),
                                   ev("20261016-1", "Сделка", "s2", section="business")])]
    groups = collect_week(issues, {}, date(2026, 10, 16))
    raw = {"headline": "h", "lead": "l", "items": [
        {"group": 0, "importance": 9, "title": "Ронахер", "summary": "s", "why": ""},
        {"group": 1, "importance": 7, "title": "Сделка", "summary": "s", "why": ""}]}
    assert [e.section for e in parse_week(raw, groups).entries] == ["business", "voices"]
