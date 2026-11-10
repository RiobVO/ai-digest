"""Настроение выпуска по его разделам, без модели: цвет дня на странице и на обложке.

Цвета заданы здесь, одним местом для страницы и обложки (решение владельца 2026-11-10, design/REDESIGN.md):
тон неба поверх пейзажа отвергнут, день различают по одному цвету-акценту."""
from dataclasses import dataclass

# Не события: авторские посты и фишки не бывают героем и не делают день громким (как llm.EXTRA_SECTIONS)
NOT_EVENTS = ("voices", "tips")
QUIET_EVENTS = 8


@dataclass(frozen=True)
class Mood:
    label: str
    # Цвет дня на светлой бумаге и на ночной: ночью он светлее, иначе на чёрном не читается
    color: str
    night: str


# Обычный день — без своего цвета: чёрный на бумаге, светлый ночью (цвет текста)
MOODS = {
    "alarm": Mood("тревожный день", "#ff4f38", "#ff5a44"),
    "release": Mood("день релиза", "#0fa968", "#2fcf8f"),
    "quiet": Mood("тихий день", "#2b4eff", "#7f97ff"),
    "calm": Mood("", "#151515", "#ece8df"),
}


def mood(sections: list[str]) -> str:
    """alarm — герой из безопасности; release — герой-модель; quiet — событий ≤8; иначе calm.

    Числа новостей безопасности правило не считает: их три почти в каждом выпуске (SECURITY_CAP), и при пороге
    «безопасности ≥3» тревожными выходили 23 из 24 выпусков — цвет дня был бы всегда красным."""
    events = [s for s in sections if s not in NOT_EVENTS]
    hero = events[0] if events else ""
    if hero == "security":
        return "alarm"
    if hero == "model":
        return "release"
    if len(events) <= QUIET_EVENTS:
        return "quiet"
    return "calm"
