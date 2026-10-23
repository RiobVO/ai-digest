import re
from datetime import datetime, timezone

from digest.llm import Digest, Entry
from digest.models import Item
from digest.render import render_archive, render_page, render_story_pages, render_tips
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


def test_cover_image_goes_into_link_preview_tags_only_when_present():
    page = render_page(sample(), STATS, WHEN, failures=[], og_image="https://x.io/og/2026-10-16.png")
    assert '<meta property="og:image" content="https://x.io/og/2026-10-16.png">' in page
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    assert "og:image" not in render_page(sample(), STATS, WHEN, failures=[])


def test_pages_link_the_home_screen_icon_and_manifest():
    for page in (render_page(sample(), STATS, WHEN, failures=[]), render_archive([], WHEN)):
        assert '<link rel="manifest" href="manifest.webmanifest">' in page
        assert '<link rel="apple-touch-icon" href="icon-180.png">' in page


def test_reading_progress_slot_sits_in_the_header_line():
    page = render_page(sample(), STATS, WHEN, failures=[], feedback_url="https://fb.example")
    assert '<span class="progress" hidden></span>' in page and "прочитано" in page


def test_telegram_message_is_escaped_linked_and_short():
    text = build_message(sample(), "https://riobvo.github.io/ai-digest/2026-10-16.html", WHEN)
    assert "&lt;Muse&gt;" in text
    assert '<a href="https://riobvo.github.io/ai-digest/2026-10-16.html">Разбор целиком</a>' in text
    assert len(text) <= TG_LIMIT


def test_read_more_expands_inline_with_source_link():
    from digest.llm import Details
    digest = sample()
    digest.entries[1].details = Details("Суть <b>статьи</b>.", ["Пункт 1"], "Код на GitHub.", "Только английский.",
                                        ["pip install x"])
    page = render_page(digest, STATS, WHEN, failures=[])
    block = page[page.index("Суть"):]
    assert "Суть &lt;b&gt;статьи&lt;/b&gt;." in page
    assert "<summary" in page[:page.index("Суть")]
    assert "Пункт 1" in block and "Только английский." in block
    assert 'class="term"' in block and "pip install x" in block and "Код на GitHub." in block
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


def test_headline_long_story_name_is_left_out_of_the_tag():
    digest = sample()
    top = digest.entries[0]
    top.story, top.story_name, top.story_day = "s1", "Disney забирает более 60 сотрудников Character.AI за $180…", 2
    top.story_prev = [("2026-10-18", "Disney забирает более 60 сотрудников Character.AI за $180 млн")]
    page = render_page(digest, STATS, WHEN, failures=[])
    assert '<a class="tag story-tag" href="story-s1.html">Сюжет · <span class="story-day">2-й день</span></a>' in page


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


def test_header_shows_event_count_and_reading_time():
    digest = sample()
    digest.entries[0].summary = "слово " * 900
    page = render_page(digest, STATS, WHEN, failures=[])
    assert "2 события · ≈5 мин чтения" in page


def test_source_icon_only_for_web_links():
    page = render_page(sample(), STATS, WHEN, failures=[])
    assert 'src="https://icons.duckduckgo.com/ip3/hf.co.ico"' in page
    digest = sample()
    digest.entries[0].item.url = "javascript:alert(1)"
    assert "ip3/.ico" not in render_page(digest, STATS, WHEN, failures=[])


def test_try_today_lists_commands_linked_to_their_cards():
    from digest.llm import Details
    digest = sample()
    digest.entries[0].id = "20261016-0"
    digest.entries[0].details = Details("Суть.", [], "Веса на HF.", "", ["ollama run muse <x>"])
    page = render_page(digest, STATS, WHEN, failures=[])
    block = page[page.index("Попробовать сегодня"):]
    assert 'href="#e-20261016-0"' in block and "ollama run muse &lt;x&gt;" in block
    assert 'id="e-20261016-0"' in page
    assert "Попробовать сегодня" not in render_page(sample(), STATS, WHEN, failures=[])


def test_try_today_skips_commands_already_shown():
    from digest.llm import Details
    digest = sample()
    for n, entry in enumerate(digest.entries):
        entry.id = f"20261016-{n}"
        entry.details = Details("Суть.", [], "", "", ["pip install -U diffusers"])
    page = render_page(digest, STATS, WHEN, failures=[])
    block = page[page.index("Попробовать сегодня"):page.index('id="papers"')]
    assert block.count("pip install -U diffusers") == 1


def test_week_summary_uses_correct_plural_forms():
    page = render_page(sample(), {"issues": 1, "events": 3}, WHEN, failures=[], kind="week", period="10–16 октября")
    assert "<b>1</b><span>выпуск</span>" in page and "<b>3</b><span>события</span>" in page
    assert "<b>2</b><span>сюжета</span>" in page


def test_buzz_badge_shows_coverage_and_links_hn_thread():
    digest = sample()
    item = digest.entries[0].item
    item.also = [("verge", "https://v/1"), ("Reuters", "https://r/1")]
    item.hn_points, item.hn_comments, item.hn_url = 540, 212, "https://news.ycombinator.com/item?id=9"
    page = render_page(digest, STATS, WHEN, failures=[])
    assert "3 источника" in page and "HN 540" in page and "212 комм." in page
    assert 'href="https://news.ycombinator.com/item?id=9"' in page


def test_tiny_hn_thread_is_not_shown_as_discussion():
    digest = sample()
    digest.entries[0].item.hn_points, digest.entries[0].item.hn_url = 2, "https://news.ycombinator.com/item?id=1"
    assert "HN 2" not in render_page(digest, STATS, WHEN, failures=[])


def test_also_line_uses_readable_source_names():
    digest = sample()
    digest.entries[0].item.also = [("gh_transformers", "https://g/1"), ("r_localllama", "https://r/1"), ("Reuters", "https://x/1")]
    page = render_page(digest, STATS, WHEN, failures=[])
    assert "также: GitHub · transformers, Reddit · LocalLLaMA, Reuters" in page


def test_telegram_line_shows_loud_coverage():
    digest = sample()
    digest.entries[0].item.also = [("verge", "https://v/1"), ("Reuters", "https://r/1")]
    text = build_message(digest, "https://x.io/p.html", WHEN)
    assert "3 издания" in text.splitlines()[3]


def test_evening_issue_is_labelled_in_crumbs():
    page = render_page(sample(), STATS, WHEN, failures=[], slot="evening")
    assert "16 октября · вечер" in page


def test_evening_page_lists_missed_morning_news_with_links():
    missed = [{"id": "20261017-1", "title": "Пропущенное <важное>"}]
    page = render_page(sample(), STATS, WHEN, failures=[], slot="evening", missed=missed, morning_page="2026-10-17.html")
    block = page[page.index("Утром ты пропустил"):]
    assert 'href="2026-10-17.html#e-20261017-1"' in block and "Пропущенное &lt;важное&gt;" in block


def test_cards_carry_entry_id_for_read_marks_when_feedback_is_on():
    digest = sample()
    digest.entries[0].id, digest.entries[1].id = "20261016-0", "20261016-1"
    page = render_page(digest, STATS, WHEN, failures=[], feedback_url="https://fb.example.workers.dev")
    assert 'data-entry="20261016-0"' in page and 'data-entry="20261016-1"' in page
    assert "/read?e=" in page
    # Клик по первоисточнику уводит со страницы — без keepalive браузер оборвёт отметку
    assert "keepalive: true" in page[page.index("/read?e="):][:200]


def test_telegram_evening_message_mentions_missed_morning_news():
    missed = [{"id": "20261017-1", "title": "Пропущенное"}]
    text = build_message(sample(), "https://x.io/e.html", WHEN, label="Вечерний дайджест", missed=missed,
                         morning_url="https://x.io/2026-10-17.html")
    assert "Утром ты пропустил" in text
    assert '<a href="https://x.io/2026-10-17.html#e-20261017-1">Пропущенное</a>' in text


def test_brief_lists_every_entry_with_a_link_to_its_card():
    digest = sample()
    digest.entries[0].id, digest.entries[1].id = "20261016-0", "20261016-1"
    page = render_page(digest, STATS, WHEN, failures=[])
    block = page[page.index('id="brief"'):]
    block = block[:block.index("</section>")]
    assert block.index('href="#e-20261016-0"') < block.index('href="#e-20261016-1"')
    assert "Meta выпустила &lt;Muse&gt; &amp; co" in block
    # Без id записей ссылаться не на что — блока нет (итог недели)
    assert 'id="brief"' not in render_page(sample(), STATS, WHEN, failures=[])


def test_engineer_posts_render_as_their_own_section_with_nav():
    digest = sample()
    post = Item(source="armin_ronacher", title="Agents", url="https://lucumr.pocoo.org/x", category="engineer")
    digest.entries.append(Entry(post, "voices", 6, "Ронахер о вкусе агентов", "Мысль.", ""))
    page = render_page(digest, STATS, WHEN, failures=[])
    block = page[page.index('id="voices"'):]
    assert "Что пишут инженеры" in block and "Ронахер о вкусе агентов" in block
    assert 'href="#voices"' in page


def test_empty_explanation_shows_only_the_source_link():
    from digest.llm import Details
    digest = sample()
    # Текста первоисточника не было: модель честно вернула пустой разбор — кнопка «Подробнее» не нужна
    digest.entries[0].details = Details("", [], "", "", [])
    digest.entries[1].details = Details("", ["Факт из ленты"], "", "", [])
    page = render_page(digest, STATS, WHEN, failures=[])
    hero = page[page.index('id="main-story"'):page.index('id="papers"')]
    assert "<details" not in hero and 'href="https://hf.co/blog/muse"' in hero
    assert "Факт из ленты" in page and '<p class="more-lead"></p>' not in page


def test_engineer_post_never_becomes_the_lead_story():
    post = Item(source="armin_ronacher", title="Agents", url="https://lucumr.pocoo.org/x", category="engineer")
    digest = Digest("h", "l", [Entry(post, "voices", 9, "Ронахер о вкусе агентов", "Мысль.", "")])
    page = render_page(digest, STATS, WHEN, failures=[])
    assert 'id="main-story"' not in page
    assert "Ронахер о вкусе агентов" in page[page.index('id="voices"'):]


def test_every_model_section_has_a_label_a_row_and_an_icon():
    # Разделы — контракт между схемой Gemini и вёрсткой: раздел без строки в ROW_SECTIONS молча пропал бы со страницы
    from digest.llm import SECTIONS
    from digest.render import ICONS, ROW_SECTIONS, SECTION_LABELS
    rows = {section: anchor for section, anchor, _, _ in ROW_SECTIONS}
    for section in SECTIONS:
        assert section in SECTION_LABELS
        if section != "paper":  # статьи идут отдельной колонкой
            assert section in rows and rows[section] in ICONS


def test_security_news_get_their_own_section():
    leak = Item(source="bleepingcomputer", title="Bank leak", url="https://bleepingcomputer.com/x", category="security")
    digest = Digest("День", "Лид", [sample().entries[0], Entry(leak, "security", 7, "Утечка в банке", "Факты.", "")])
    page = render_page(digest, STATS, WHEN, failures=[])
    assert 'id="security"' in page and "Безопасность" in page and "Утечка в банке" in page


def _with_extras() -> Digest:
    from digest.llm import Mention
    digest = sample()
    tip = Item(source="claude_dev", title="Effort", url="https://claude.dev/blog/effort", category="lab")
    digest.entries.append(Entry(tip, "tips", 7, "Какой effort ставить в Claude Code", "Приём.", ""))
    shop = Item(source="the_register", title="Shop", url="https://www.theregister.com/shop", category="news",
                also=[("bleepingcomputer", "https://www.bleepingcomputer.com/shop")])
    digest.more = [Mention(shop, "Сеть <магазинов> легла после атаки"),
                   Mention(Item(source="hn", title="x", url="javascript:alert(1)", category="community"), "Без ссылки")]
    return digest


def test_also_today_lists_one_liners_between_events_and_extras():
    page = render_page(_with_extras(), STATS, WHEN, failures=[])
    block = page[page.index('id="also-today"'):]
    block = block[:block.index("</section>")]
    assert "Сеть &lt;магазинов&gt; легла после атаки" in block and 'href="https://www.theregister.com/shop"' in block
    assert "The Register" in block and "2 источника" in block
    assert "Без ссылки" in block and "javascript:" not in page
    assert 'href="#also-today"' in page
    # Следующий ярус новостей идёт после событий дня, но до лайфхаков и авторских постов
    assert page.index('id="main-story"') < page.index('id="also-today"') < page.index('id="tips"')
    assert 'id="also-today"' not in render_page(sample(), STATS, WHEN, failures=[])


def test_lifehack_never_becomes_the_lead_story():
    tip = Item(source="claude_dev", title="Effort", url="https://claude.dev/blog/effort", category="lab")
    digest = Digest("h", "l", [Entry(tip, "tips", 9, "Какой effort ставить в Claude Code", "Приём.", "")])
    page = render_page(digest, STATS, WHEN, failures=[])
    assert 'id="main-story"' not in page
    assert "Фишки" in page and "Какой effort ставить в Claude Code" in page[page.index('id="tips"'):]


def test_telegram_lists_also_today_but_leaves_tips_to_the_tip_of_the_day():
    digest = _with_extras()
    events = [Entry(Item(source="verge", title=f"e{n}", url=f"https://v/{n}", category="news"), "product", 7,
                    f"Событие {n}", "s", "") for n in range(4)]
    digest.entries[1:1] = events
    for n, entry in enumerate(digest.entries):
        entry.id = f"20261016-{n}"
    text = build_message(digest, "https://x.io/p.html", WHEN)
    # Фишки приходят отдельными сообщениями в течение дня: строка в выпуске повторяла бы их
    assert "Фишка" not in text and "Какой effort ставить в Claude Code" not in text
    assert '• <a href="https://www.theregister.com/shop">Сеть &lt;магазинов&gt; легла после атаки</a>' in text
    # Ссылку не из веба Telegram не примет — строка остаётся, но без ссылки
    assert "• Без ссылки" in text and "javascript:" not in text
    assert text.index("<b>5.</b>") < text.index("Ещё за день") < text.index("Разбор целиком")


def test_telegram_trims_also_today_first_and_counts_only_visible_text():
    import re
    from html import unescape
    from digest.llm import Mention
    digest = sample()
    digest.more = [Mention(Item(source="verge", title="x", url=f"https://example.com/{'a' * 150}/{n}", category="news"),
                           f"Новость номер {n} " + "ж" * 80) for n in range(40)]
    text = build_message(digest, "https://x.io/p.html", WHEN)
    # Лимит Telegram — 4096 знаков после разбора разметки: адреса ссылок и теги в него не входят
    assert len(unescape(re.sub(r"<[^>]+>", "", text))) <= TG_LIMIT < len(text)
    assert "<b>1.</b>" in text and "Новость номер 30 " in text and "Разбор целиком" in text
    assert "Новость номер 39 " not in text


def test_cards_show_the_event_status_but_not_the_internal_score():
    digest = sample()
    digest.entries[0].status = "available"
    digest.entries[1].status = "rumor"
    page = render_page(digest, STATS, WHEN, failures=[])
    # Оценка нужна отбору, а читателю цифра «8.6 / 10» ничего не говорит
    assert "/ 10" not in page and "важность" not in page.lower()
    assert ">доступно<" in page and ">слух<" in page


def test_trick_card_has_a_copy_block_with_the_verbatim_command():
    tip = Item(source="gh_claude_code", title="v2", url="https://github.com/anthropics/claude-code/releases/v2",
               category="release")
    entry = Entry(tip, "tips", 6, "Автонастройка auto mode", "Запусти команду.", "Подстроит режим под проект.")
    entry.copy = "/auto-mode-setup <proj>"
    page = render_page(Digest("h", "l", [entry]), STATS, WHEN, failures=[])
    block = page[page.index('id="tips"'):]
    assert "/auto-mode-setup &lt;proj&gt;" in block and "Скопировать" in block


TIP_ISSUES = [
    {"date": "2026-10-21", "slot": "evening", "headline": "h", "page": "2026-10-21-evening.html", "entries": [
        {"id": "20261021-3", "title": "Событие дня", "section": "model", "source": "openai", "url": "https://openai.com/x",
         "story": "s1"},
        {"id": "20261021-21", "title": "LoRA <на лету>", "section": "tips", "source": "r_localllama",
         "url": "https://www.reddit.com/r/LocalLLaMA/x", "story": "s79", "summary": "Подключи адаптер.",
         "why": "Быстро.", "copy": "", "topic": "local"},
        {"id": "20261021-22", "title": "Песочница", "section": "tips", "source": "gh_claude_code",
         "url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.305", "story": "s80",
         "summary": "Добавь параметр.", "why": "Chromium в песочнице.", "copy": "sandbox.allowNestedUserNamespaces",
         "topic": "claude_code"}]},
    # Фишка из архива до появления арсенала: ни темы, ни текста — всё равно должна найтись
    {"date": "2026-10-20", "slot": "morning", "headline": "h2", "page": "2026-10-20.html", "entries": [
        {"id": "20261020-9", "title": "Старая фишка", "section": "tips", "source": "hn", "url": "https://x.io/t",
         "story": ""}]},
]


def _topic_section(page: str, topic: str) -> str:
    start = page.index(f'id="t-{topic}"')
    return page[start:page.index("</section>", start)]


def test_arsenal_groups_every_archived_tip_by_topic():
    page = render_tips(TIP_ISSUES, WHEN, feedback_url="https://fb.dev")
    for tip_id, topic in (("20261021-21", "local"), ("20261021-22", "claude_code"), ("20261020-9", "other")):
        assert f'data-tip="{tip_id}"' in _topic_section(page, topic)
    assert "Событие дня" not in page and "LoRA &lt;на лету&gt;" in page
    # Карточка ведёт в свой выпуск
    assert 'href="2026-10-21-evening.html#e-20261021-22"' in page


def test_arsenal_copy_button_carries_exactly_the_verified_text():
    page = render_tips(TIP_ISSUES, WHEN, feedback_url="https://fb.dev")
    assert re.findall(r"<code>(.*?)</code>", page) == ["sandbox.allowNestedUserNamespaces"]
    assert page.count('class="copy"') == 1
    assert "Команда не подтверждена источником" in _topic_section(page, "local")


def test_arsenal_checkbox_needs_the_worker_and_empty_archive_is_honest():
    assert 'class="tried"' not in render_tips(TIP_ISSUES, WHEN)
    page = render_tips(TIP_ISSUES, WHEN, feedback_url="https://fb.dev")
    assert page.count('class="tried"') == 3 and '"https://fb.dev"' in page
    empty = render_tips([], WHEN)
    assert "Новые фишки появятся с выпусками" in empty and "data-tip" not in empty


STORY_ISSUES = [
    {"date": "2026-10-19", "slot": "evening", "headline": "h3", "page": "2026-10-19-evening.html", "entries": [
        {"id": "20261019-14", "title": "Character.AI передаёт команду Disney", "section": "business", "source": "verge",
         "url": "https://v/3", "story": "s39"}]},
    {"date": "2026-10-18", "slot": "evening", "headline": "h2", "page": "2026-10-18-evening.html", "entries": [
        {"id": "20261018-12", "title": "Character.AI подал на банкротство", "section": "business", "source": "techcrunch",
         "url": "https://t/2", "story": "s39"},
        {"id": "20261018-13", "title": "Однодневка", "section": "product", "source": "verge", "url": "https://v/x",
         "story": "s40"}]},
    {"date": "2026-10-18", "slot": "morning", "headline": "h1", "page": "2026-10-18.html", "entries": [
        {"id": "20261018-2", "title": "Disney забирает <команду> Character.AI", "section": "business",
         "source": "the_decoder", "url": "https://d/1", "story": "s39"}]},
]
STORIES = {"s39": {"name": "Банкротство Character.AI", "events": []}, "s40": {"name": "Однодневка", "events": []}}


def test_story_page_chronology_matches_the_archive():
    pages = render_story_pages(STORY_ISSUES, STORIES, WHEN)
    # Однодневный сюжет страницы не получает: хронологии из одного события нет
    assert list(pages) == ["story-s39.html"]
    page = pages["story-s39.html"]
    assert "Банкротство Character.AI" in page
    order = [page.index(t) for t in ("Disney забирает &lt;команду&gt; Character.AI", "Character.AI подал на банкротство",
                                     "Character.AI передаёт команду Disney")]
    assert order == sorted(order)
    for issue, entry_id in (("2026-10-18.html", "20261018-2"), ("2026-10-18-evening.html", "20261018-12"),
                            ("2026-10-19-evening.html", "20261019-14")):
        assert f'href="{issue}#e-{entry_id}"' in page
    assert "Однодневка" not in page


def test_nameless_story_page_is_titled_by_its_first_event():
    stories = {"s39": {"name": "", "events": []}}
    page = render_story_pages(STORY_ISSUES, stories, WHEN)["story-s39.html"]
    assert "<h1>Disney забирает &lt;команду&gt; Character.AI</h1>" in page



def test_every_source_in_the_config_has_a_readable_label():
    import tomllib
    from pathlib import Path

    from digest.render import SOURCE_LABELS
    # Без подписи на странице видно техническое имя («thn», «securelist_ru») вместо издания. Google News подписывает
    # запись изданием из заголовка (publisher_in_title) — ему подпись не нужна
    config = tomllib.loads((Path(__file__).parent.parent / "sources.toml").read_text(encoding="utf-8"))
    assert [s["name"] for s in config["source"]
            if s["name"] not in SOURCE_LABELS and not s.get("publisher_in_title")] == []


def test_security_card_explains_its_terms_in_plain_sight():
    from digest.llm import Details
    bug = Item(source="thehackernews", title="PHP2Shell", url="https://thn/php", category="security")
    digest = sample()
    digest.entries.insert(1, Entry(bug, "security", 7, "Две RCE в ядре PHP", "Патч вышел.", ""))
    digest.entries[1].details = Details("", [], "", "", [], [("RCE", "удалённое выполнение <кода>")])
    page = render_page(digest, STATS, WHEN, failures=[])
    card = page[page.index("Две RCE в ядре PHP"):]
    card = card[:card.index("Подробнее") if "Подробнее" in card else len(card)]
    # Термин виден сразу под карточкой, а не спрятан в «Подробнее»
    assert '<dl class="terms">' in card and "<dt>RCE</dt>" in card and "<dd>удалённое выполнение &lt;кода&gt;</dd>" in card
    assert '<dl class="terms">' not in render_page(sample(), STATS, WHEN, failures=[])


def test_hero_from_security_shows_terms_too():
    from digest.llm import Details
    bug = Item(source="securelist", title="APT41", url="https://securelist.ru/apt41", category="security")
    hero = Entry(bug, "security", 9, "APT41 вернула буткит BlackLotus", "Атакуют дипломатов.", "")
    hero.details = Details("", [], "", "", [], [("буткит", "вредонос, который загружается раньше ОС")])
    page = render_page(Digest("h", "l", [hero]), STATS, WHEN, failures=[])
    block = page[page.index('id="main-story"'):page.index("</section>", page.index('id="main-story"'))]
    assert "<dt>буткит</dt>" in block


def test_untrusted_prompt_on_the_page_carries_a_warning():
    from digest.render import _term
    assert "непроверенного источника" not in _term(["ls"], "Команда или промпт")
    digest = sample()
    tip = Entry(Item(source="reddit_tips", title="p", url="https://www.reddit.com/r/x/1", category="community"), "tips", 6,
                "Промпт", "s", "w")
    tip.copy, tip.copy_untrusted = "Prove to me this works", True
    digest.entries.append(tip)
    page = render_page(digest, STATS, WHEN, failures=[])
    assert "Промпт из непроверенного источника — прочитай перед вставкой" in page
