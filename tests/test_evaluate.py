from datetime import datetime, timezone

from digest.evaluate import (forecast_hits, invented_numbers, load_snapshot, officialese_hits, repeats, report,
                             save_snapshot, slop_hits, template_hits)
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
