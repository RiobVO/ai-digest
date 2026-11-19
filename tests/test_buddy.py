import pytest

from digest import buddy, llm
from digest.llm import Details, Entry, LlmError
from digest.models import Item

SOBER = "По отчёту Binance, хакеры украли активы на 6,34 млрд долларов. Заморозить удалось 2,85 млрд долларов."
WHY = "Точками входа были ноутбуки сотрудников и подменённый Homebrew."
GOOD = ("по отчёту binance хакеры сперли 6,34 млрд долларов, заморозить удалось 2,85 млрд. зашли через ноуты "
        "сотрудников и подменённый homebrew, вот это пиздец.")


def card(section="security", summary=SOBER, why=WHY):
    item = Item(source="x", title="Binance hack", url="https://x.io/a", category="news",
                summary="Hackers stole $6.34B from Binance; $2.85B frozen. Entry via laptops and a fake Homebrew.")
    return Entry(item, section, 8, "Хакеры вынесли $6,34 млрд у Binance", summary, why)


def answers(monkeypatch, *texts):
    calls = []

    def say(system, messages, model, call=""):
        calls.append(messages)
        reply = texts[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm, "say", say)
    return calls


def test_card_is_retold_and_the_conclusion_moves_into_the_text(monkeypatch):
    calls = answers(monkeypatch, GOOD)
    entry = card()
    buddy.retell([entry], {}, {}, ["claude-haiku-5-5"])
    assert entry.summary == GOOD and entry.why == ""
    # Примеры лежат в истории как прошлые ответы самой модели, карточка — последней репликой
    history = calls[0]
    assert [m["role"] for m in history] == ["user", "assistant"] * len(buddy.EXAMPLES) + ["user"]
    assert history[-1]["content"] == f"{entry.title}. {SOBER} Вывод: {WHY}"
    assert {m["content"] for m in history[1:-1:2]} == {said for _, said in buddy.EXAMPLES}


def test_examples_do_not_teach_an_opening_tic():
    # Присказка в начале каждой карточки («ну пиздец…», «бля, …») — та же болезнь, что «Значит,» (19.11.2026)
    firsts = [said.split()[0] for _, said in buddy.EXAMPLES]
    assert len(set(firsts)) == len(firsts)
    assert not any(buddy.MAT.match(said) for _, said in buddy.EXAMPLES)
    calm = [said for _, said in buddy.EXAMPLES if not buddy.MAT.search(said)]
    assert len(calm) >= 2


@pytest.mark.parametrize("opener", ["блин, ", "ну вот, ", "да блин, это жесть: ", "блин, ну это жесть. ", "ахренеть, ",
                                    "Ну вот, ", "ну охуеть, ", "блядь, ", "Ну и хрень: ", "ну всё, "])
def test_only_one_card_of_an_issue_opens_with_an_interjection(monkeypatch, opener):
    answers(monkeypatch, opener + GOOD, opener + GOOD, GOOD)
    first, second, third = card(), card(), card()
    buddy.retell([first, second, third], {}, {}, ["claude-haiku-5-5"])
    assert first.summary == opener + GOOD and second.summary == GOOD and third.summary == GOOD


def test_a_phrase_that_only_looks_like_an_interjection_stays(monkeypatch):
    said = "вот и binance: хакеры сперли 6,34 млрд долларов, заморозить удалось 2,85 млрд."
    answers(monkeypatch, "блин, " + GOOD, said)
    opened, entry = card(), card()
    buddy.retell([opened, entry], {}, {}, ["claude-haiku-5-5"])
    assert entry.summary == said


@pytest.mark.parametrize("said", [
    "хакеры сперли 6,34 ляма долларов у binance, заморозили 2,85 ляма.",                    # сленговая сумма
    "хакеры сперли 6,34 млн долларов у binance, заморозить удалось 2,85 млрд.",             # миллиарды стали миллионами
    "хакеры сперли 6,34 млрд долларов у binance.",                                          # число потеряно
    "хакеры сперли 6,34 млрд долларов у binance, заморозили 2,85 млрд, ещё 40% не найти.",  # число из головы
    "хакеры сперли 6,34 млрд долларов у binance через okta, заморозили 2,85 млрд.",         # название из головы
    "заметка такая: хакеры сперли 6,34 млрд долларов у binance, заморозить удалось 2,85 млрд.",     # вышла из роли
    GOOD + " " + GOOD,                                                                         # дописала своё
    "ну пиздец.",                                                                              # одна реакция
    "binance, ну такое.",                                                                      # фактов не осталось
    "хакеры сперли 6,34 млрд долларов у binance, заморозили 2,85 млрд, ушло ещё 2,85 тысячи.",  # порядок из головы
    "",
])
def test_unfaithful_retelling_leaves_the_sober_card(monkeypatch, said):
    answers(monkeypatch, said)
    entry = card()
    buddy.retell([entry], {}, {}, ["claude-haiku-5-5"])
    assert entry.summary == SOBER and entry.why == WHY


def test_names_and_numbers_from_the_source_text_are_allowed(monkeypatch):
    said = "хакеры из lazarus сперли 6,34 млрд долларов у binance, заморозить удалось 2,85 млрд, всё началось 20 октября."
    answers(monkeypatch, said)
    entry = card()
    buddy.retell([entry], {"https://x.io/a": "Lazarus group began the attack on 20 October."}, {}, ["claude-haiku-5-5"])
    assert entry.summary == said


def test_tips_keep_their_exact_steps_and_a_dead_model_stops_the_calls(monkeypatch):
    tip = card(section="tips", summary="Открой settings.json и добавь onFailure.", why="Хук блокирует действие.")
    first, second = card(), card()
    calls = answers(monkeypatch, LlmError("нет денег", 400), GOOD)
    buddy.retell([tip, first, second], {}, {}, ["claude-haiku-5-5"])
    assert tip.summary == "Открой settings.json и добавь onFailure." and tip.why == "Хук блокирует действие."
    assert len(calls) == 1 and first.summary == SOBER and second.summary == SOBER


def test_without_a_paid_model_nothing_is_called(monkeypatch):
    calls = answers(monkeypatch)
    entry = card()
    entry.details = Details("Контекст.", [], "", "")
    buddy.retell([entry], {}, {}, [])
    assert calls == [] and entry.summary == SOBER


def test_a_reaction_left_empty_by_the_opener_cut_keeps_the_sober_card(monkeypatch):
    # Ревью 19.11.2026: «ну пиздец.» у карточки без чисел проходил проверку, присказка срезалась — карточка пустела
    plain = "Microsoft закрывает Skype и переводит пользователей в Teams."
    answers(monkeypatch, "ну вот, microsoft закрывает skype и переводит всех в teams.", "ну пиздец.")
    first, second = card(summary=plain, why=""), card(summary=plain, why="")
    first.title = second.title = "Microsoft закрывает Skype"
    buddy.retell([first, second], {}, {}, ["claude-haiku-5-5"])
    assert first.summary.startswith("ну вот, microsoft") and second.summary == plain


@pytest.mark.parametrize("said", ["всё, что известно: хакеры сперли 6,34 млрд долларов у binance, заморозить удалось 2,85 млрд.",
                                  "это-то понятно: хакеры сперли 6,34 млрд долларов у binance, заморозить удалось 2,85 млрд.",
                                  "уже — 6,34 млрд долларов сперли хакеры у binance, заморозить удалось 2,85 млрд."])
def test_a_real_beginning_is_not_taken_for_an_interjection(monkeypatch, said):
    answers(monkeypatch, "блин, " + GOOD, said)
    opened, entry = card(), card()
    buddy.retell([opened, entry], {}, {}, ["claude-haiku-5-5"])
    assert entry.summary == said


def test_a_connection_dropped_mid_answer_is_a_model_failure_not_a_crash(monkeypatch):
    # SDK не оборачивает обрыв потока в свои исключения; без этого пересказ ронял уже собранный выпуск
    class Dropped:
        def __enter__(self):
            raise RuntimeError("peer closed connection")

        def __exit__(self, *args):
            return False

    class Client:
        def __init__(self, **kwargs):
            self.messages = self

        def stream(self, **kwargs):
            return Dropped()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(llm.anthropic, "Anthropic", Client)
    with pytest.raises(LlmError):
        llm.say("s", [{"role": "user", "content": "x"}], "claude-haiku-5-5", call="buddy")
    entry = card()
    buddy.retell([entry], {}, {}, ["claude-haiku-5-5"])
    assert entry.summary == SOBER and entry.why == WHY
