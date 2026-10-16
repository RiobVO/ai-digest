from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    source: str
    title: str
    url: str
    category: str
    published: datetime | None = None
    summary: str = ""
    # Сигнал популярности из источника: голоса HF Papers, очки HN и т.п.
    score: int | None = None
    # Склеенные дубли: (источник, url)
    also: list[tuple[str, str]] = field(default_factory=list)
    # Самое громкое обсуждение на HN: показывает, насколько событие обсуждают, а не только пишут о нём
    hn_points: int = 0
    hn_comments: int = 0
    hn_url: str = ""
