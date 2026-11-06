from datetime import datetime, timezone

from digest import evaluate
from digest.evaluate import (forecast_hits, invented_numbers, load_snapshot, officialese_hits, repeats, report,
                             save_snapshot, slop_hits, template_hits, verify_details, verify_numbers)
from digest.llm import Details, Digest, Entry, Mention
from digest.models import Item

NOW = datetime(2026, 10, 20, 3, tzinfo=timezone.utc)


def test_empty_evaluations_and_officialese_are_counted_apart():
    # Пустая оценка ничего не сообщает о новости; канцелярит — только тяжёлый стиль при нормальном смысле
    text = "Это ключевая новость для индустрии: компания представила модель, которая является лидером."
    assert set(slop_hits(text)) == {"ключевая", "для индустрии"}
    assert set(officialese_hits(text)) == {"представила", "является"}
    assert slop_hits("DeepSeek выложила DeepInfer под MIT") == []


def test_numbers_absent_from_the_source_are_flagged_as_invented():
    source = "Anthropic Q3 revenue hits $16.8B, 2,000 employees, 3x growth"
    assert invented_numbers("Выручка $16,8 млрд, сотрудников 2000, рост в 3 раза", source) == []
    assert invented_numbers("Выручка 17 млрд, рост 40%", source) == ["17", "40"]
    # Русская запись тысяч через пробел — то же число, что «250,000» в английском источнике
    assert invented_numbers("штраф до 250 000 евро", "a fine of up to €250,000") == []
    # «0,015» — русская дробь, а не английские тысячи
    assert invented_numbers("0,015 доллара за кэш", "$0.015 cached input") == []


def test_forecasts_are_counted_separately_from_facts():
    # Прогноз в «почему важно» без опоры на источник — домысел, который меняет смысл новости
    assert forecast_hits("Поисковикам придётся выключить ответы ИИ, и всё станет дешевле на годы вперёд") == [
        "придётся", "станет дешевле", "на годы вперёд"]
    assert forecast_hits("Суд обязал Google удалить три текста") == []


def test_why_that_retells_the_summary_counts_as_a_repeat():
    summary = "DeepSeek выложила в open source собственный движок инференса для своих моделей"
    assert repeats("Собственный движок инференса DeepSeek теперь в open source", summary)
    assert not repeats("Запускать их модели у себя станет дешевле, чем через API", summary)


def test_snapshot_round_trip_keeps_items_and_article_texts(tmp_path):
    item = Item(source="openai", title="New model", url="https://openai.com/x", category="lab", published=NOW,
                summary="desc", also=[("verge", "https://verge.com/x")], hn_points=300)
    path = tmp_path / "snap.json"
    save_snapshot(path, [item], NOW, texts={"https://openai.com/x": "full text"})
    items, when, texts = load_snapshot(path)
    assert items == [item] and when == NOW and texts == {"https://openai.com/x": "full text"}


def test_report_sums_problems_across_the_issue():
    item = Item(source="verge", title="Nvidia buys startup for $2B", url="https://verge.com/n", category="news",
                summary="The deal is worth $2B")
    good = Entry(item, "business", 7, "Nvidia купила стартап за $2 млрд", "Сделка на $2 млрд.", "Рынок чипов сужается.")
    bad = Entry(item, "business", 9, "Ключевая сделка", "Nvidia купила стартап за $5 млрд.",
                "Nvidia купила стартап за $5 млрд.")
    bad.details = Details("Это является важным шагом.", ["рост 30%"], "", "")
    result = report(Digest("День", "Лид является", [good, bad]), {item.url: "The deal is worth $2B"})
    assert result["entries"] == 2 and result["high_importance"] == 1
    assert result["invented_numbers"] == 2  # 5 в карточке, 30 в разборе
    # «Ключевая», «важным шагом»; «является» в разборе и в лиде — заголовок и лид считаются, как у пустых оценок
    assert result["slop"] == 2 and result["officialese"] == 2
    assert result["why_repeats"] == 1


def test_why_templates_and_guessed_motives_are_counted():
    # «Почему важно» по шаблону подходит к любой новости своего типа, а мотив без опоры на запись — догадка
    why = "Случай наглядно показывает риски, а шаг направлен на защиту от критики; авария продемонстрировала слабость"
    assert template_hits(why) == ["наглядно", "показывает", "направлен на", "продемонстрировала"]
    assert template_hits("Инцидент в очередной раз доказывает, что шаг призван снизить давление") == [
        "очередной", "доказывает", "призван"]
    assert template_hits("Значит, с 1 марта платежи через v1 не пройдут — переезжай на v2") == []


def test_report_counts_also_today_lifehacks_and_why_templates():
    item = Item(source="verge", title="t", url="https://verge.com/n", category="news")
    event = Entry(item, "security", 7, "Атака", "Сеть магазинов легла.", "Случай показывает риски.")
    tip = Entry(item, "tips", 6, "Приём", "Как сделать.", "Значит, можно быстрее.")
    result = report(Digest("День", "Лид", [event, tip], more=[Mention(item, "Ещё одна новость")]), {})
    assert result["more"] == 1 and result["tips"] == 1 and result["why_templates"] == 1


def test_also_today_lines_are_checked_for_slop_and_invented_numbers():
    # Строка «Ещё за день» — тоже текст для читателя: без проверки сбой второго вызова выглядел бы в eval чистым
    item = Item(source="verge", title="Startup raises $40M", url="https://verge.com/n", category="news")
    line = Mention(item, "Уникальная революционная модель получила 999999999 баллов, что является рекордом")
    result = report(Digest("День", "Лид", [], more=[line]), {})
    assert result["entries"] == 0 and result["more"] == 1
    assert result["slop"] >= 1 and result["officialese"] == 1 and result["invented_numbers"] == 1
    assert report(Digest("День", "Лид", [], more=[Mention(item, "Стартап привлёк $40 млн")]), {})["invented_numbers"] == 0


def _card(title, summary, why="", url="https://x.io/a", feed_title="", feed_summary=""):
    return Entry(Item(source="x", title=feed_title, url=url, category="news", summary=feed_summary), "model", 7,
                 title, summary, why)


def _no_model(*args, **kwargs):
    raise AssertionError("модель не должна вызываться")


def test_clean_cards_are_left_alone_without_a_model_call(monkeypatch):
    monkeypatch.setattr(evaluate, "generate_first", _no_model)
    card = _card("Acme выпустила Orbit 2", "Модель 14B берёт 71% на бенчмарке.", "Влезает в 24 ГБ.",
                 feed_title="Acme ships Orbit 2, a 14B model", feed_summary="71% on the benchmark, runs on 24 GB")
    verify_numbers([card], {}, {}, ["lite"], "k")
    assert (card.title, card.summary, card.why) == ("Acme выпустила Orbit 2", "Модель 14B берёт 71% на бенчмарке.",
                                                     "Влезает в 24 ГБ.")


def test_sentence_with_an_invented_number_is_cut_when_another_sentence_remains(monkeypatch):
    # Lite дописывает детали из памяти («770 млрд параметров», «за 30 часов» — 05.11.2026): предложение с таким
    # числом убираем, если без него описание не пустеет; why — одно предложение, с чужим числом оно уходит целиком
    monkeypatch.setattr(evaluate, "generate_first", _no_model)
    card = _card("Tencent выпустила Hy4", "Tencent выпустила открытую модель Hy4. У неё 770 млрд параметров.",
                 "Значит, через 2027 год локалка станет сильнее.", feed_title="Tencent releases Hy4 open model")
    verify_numbers([card], {"https://x.io/a": "Tencent released Hy4, an open model for agents."}, {}, ["lite"], "k")
    assert card.summary == "Tencent выпустила открытую модель Hy4."
    assert card.why == ""


def test_single_sentence_summary_is_rewritten_by_the_model(monkeypatch):
    calls = []

    def fake(prompt, schema, models, api_key, call=""):
        calls.append((prompt, models, call))
        return {"items": [{"id": 0, "title": "Хакеры украли у Binance $6,3 млрд",
                           "summary": "Binance подтвердила кражу активов на $6,3 млрд после фишинга инженера.",
                           "why": "Так что проверяй, кого нанимаешь."}]}

    monkeypatch.setattr(evaluate, "generate_first", fake)
    card = _card("Хакеры украли у Binance $6,34 млрд", "Binance подтвердила кражу активов на $6,34 млрд после фишинга инженера.",
                 "Так что проверяй, кого нанимаешь.", feed_title="Fake job interview led to $6.3B Binance heist")
    verify_numbers([card], {"https://x.io/a": "Binance said $6.3B was stolen after an engineer was phished."}, {},
                   ["lite"], "k")
    assert card.title == "Хакеры украли у Binance $6,3 млрд"
    assert card.summary == "Binance подтвердила кражу активов на $6,3 млрд после фишинга инженера."
    prompt, models, call = calls[0]
    assert models == ["lite"] and call == "numbers"
    assert "6.34" in prompt and "$6.3B" in prompt and "id=0" in prompt


def test_rewrite_that_still_invents_or_fails_leaves_the_card_as_is(monkeypatch, caplog):
    import logging
    from digest.llm import LlmError
    original = ("Zyxel закрыла дыру", "Патч вышел через 10 дней после первого.", "")
    monkeypatch.setattr(evaluate, "generate_first", lambda *a, **kw: {"items": [
        {"id": 0, "title": "Zyxel закрыла дыру", "summary": "Патч вышел через 12 дней после первого.", "why": ""}]})
    card = _card(*original, feed_title="Zyxel fixes GS1900 zero-day after a failed first patch")
    with caplog.at_level(logging.WARNING, logger="digest.evaluate"):
        verify_numbers([card], {}, {}, ["lite"], "k")
    assert (card.title, card.summary, card.why) == original and "осталось" in caplog.text

    def down(*a, **kw):
        raise LlmError("Gemini lite ответил 503", 503)

    monkeypatch.setattr(evaluate, "generate_first", down)
    card = _card(*original, feed_title="Zyxel fixes GS1900 zero-day after a failed first patch")
    verify_numbers([card], {}, {}, ["lite"], "k")
    assert (card.title, card.summary, card.why) == original


def test_numbers_from_merged_duplicate_records_count_as_known(monkeypatch):
    # Событие склеено из нескольких записей: число из описания дубля — тоже из записи, а не из памяти модели
    monkeypatch.setattr(evaluate, "generate_first", _no_model)
    card = _card("SoftBank не купит ABB за $5,4 млрд", "Китай заблокировал сделку на $5,4 млрд.",
                 feed_title="China blocks SoftBank's purchase of ABB robotics")
    card.item.also.append(("verge", "https://verge.io/abb"))
    records = {"https://verge.io/abb": "SoftBank's $5.4B ABB robotics deal blocked by China"}
    verify_numbers([card], {}, records, ["lite"], "k")
    assert card.summary == "Китай заблокировал сделку на $5,4 млрд."


def test_report_counts_numbers_from_duplicate_records_as_known():
    # Эталон у метрики и у защиты один (ревью 06.11): иначе verify_numbers считает карточку чистой, а report — нет
    item = Item(source="x", title="China blocks SoftBank's purchase of ABB robotics", url="https://x.io/abb", category="news",
                also=[("verge", "https://verge.io/abb")])
    entry = Entry(item, "business", 7, "SoftBank не купит ABB", "Китай заблокировал сделку на $5,4 млрд.", "")
    records = {"https://verge.io/abb": "SoftBank's $5.4B ABB robotics deal blocked by China"}
    assert report(Digest("День", "Лид", [entry]), {})["invented_numbers"] == 1
    assert report(Digest("День", "Лид", [entry]), {}, records)["invented_numbers"] == 0


def test_detail_points_and_sentences_with_invented_numbers_are_dropped():
    # Регрессия 06.11.2026: после защиты карточек остаток выдуманных чисел («20, 70», «3000») сидел в пунктах разбора.
    # Разбор — надстройка: пункт или предложение с чужим числом просто убираем, переписывать нечего
    card = _card("Acme выпустила Orbit 2", "Модель 14B.", feed_title="Acme ships Orbit 2, a 14B model")
    card.details = Details("Acme шла к этому давно. Обучение заняло 30 часов. Веса открыты.",
                           ["Контекст 128K", "Берёт 71% на бенчмарке", "Лицензия Apache 2.0"],
                           "Веса на Hugging Face, нужны 24 ГБ.", "Только английский.")
    source = {"https://x.io/a": "Orbit 2 has a 128K context, is licensed under Apache 2.0 and ships in English only."}
    verify_details([card], source, {})
    assert card.details.what == "Acme шла к этому давно. Веса открыты."
    assert card.details.points == ["Контекст 128K", "Лицензия Apache 2.0"]
    assert card.details.try_it == ""
    assert card.details.caveats == "Только английский."


def test_clean_details_and_cards_without_details_are_left_alone():
    clean = _card("Acme выпустила Orbit 2", "Модель 14B.", feed_title="Acme ships Orbit 2, a 14B model")
    clean.details = Details("Контекст.", ["Модель 14B"], "", "", ["pip install orbit"], [("RCE", "удалённый запуск кода")])
    bare = _card("Acme выпустила Orbit 2", "Модель 14B.", feed_title="Acme ships Orbit 2, a 14B model")
    verify_details([clean, bare], {}, {})
    assert clean.details == Details("Контекст.", ["Модель 14B"], "", "", ["pip install orbit"],
                                    [("RCE", "удалённый запуск кода")])
    assert bare.details is None
