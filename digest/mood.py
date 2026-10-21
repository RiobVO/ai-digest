"""Настроение выпуска по его разделам, без модели: тон неба в пейзаже героя страницы и на обложке.

Пейзаж (SVG в page.html) не меняется — cover.py вырезает его регуляркой. Настроение — отдельный полупрозрачный слой
неба сверху вниз; тона заданы здесь, одним местом для страницы и обложки."""
from dataclasses import dataclass

# Не события: авторские посты и фишки не бывают героем и не делают день громким (как llm.EXTRA_SECTIONS)
NOT_EVENTS = ("voices", "tips")
ALARM_SECURITY = 3
QUIET_EVENTS = 8


@dataclass(frozen=True)
class Mood:
    label: str
    # Цвет неба у верхнего края; к середине пейзажа слой сходит на нет
    sky: str


MOODS = {
    "alarm": Mood("тревожный день", "rgba(104,88,120,.30)"),
    "release": Mood("день релиза", "rgba(255,183,107,.34)"),
    "quiet": Mood("тихий день", "rgba(176,196,214,.42)"),
    "calm": Mood("", "transparent"),
}


def mood(sections: list[str]) -> str:
    """alarm — герой из безопасности или безопасности ≥3; release — герой-модель; quiet — событий ≤8; иначе calm."""
    events = [s for s in sections if s not in NOT_EVENTS]
    hero = events[0] if events else ""
    if hero == "security" or events.count("security") >= ALARM_SECURITY:
        return "alarm"
    if hero == "model":
        return "release"
    if len(events) <= QUIET_EVENTS:
        return "quiet"
    return "calm"
