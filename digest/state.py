import json
from datetime import datetime, timedelta
from pathlib import Path

from digest.dedup import canonical_key, norm_title
from digest.models import Item

RETENTION = timedelta(days=45)
# Короткие заголовки («v1.2», «Chirp Pong») слишком часто совпадают случайно — по ним не отсекаем
MIN_TITLE_KEY = 12


class SeenStore:
    """Помнит, что уже публиковалось, чтобы новость не пришла дважды."""

    def __init__(self, path: Path):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.seen: dict[str, str] = data.get("seen", {})
        self.sources: set[str] = set(data.get("sources", []))

    def take_new(self, items: list[Item], now: datetime) -> list[Item]:
        fresh = []
        stamp = now.isoformat()
        # Сверяемся только с прошлыми запусками: дубли внутри одного запуска склеит dedupe с сохранением очков HN
        prior = set(self.seen)
        for item in items:
            keys = [canonical_key(item.url)]
            title = norm_title(item.title)
            if len(title) >= MIN_TITLE_KEY:
                # Тот же заголовок завтра из другого источника с другим URL — повтор, а не новость
                keys.append(f"title:{title}")
            already = any(k in prior for k in keys)
            # Каждое наблюдение продлевает срок: ссылка, висящая на странице месяцами, не «оживает» после retention
            for k in keys:
                self.seen[k] = stamp
            if already:
                continue
            # У HTML-источников нет дат: при первом запуске всё на странице — архив, а не новости
            if item.published is None and item.source not in self.sources:
                continue
            fresh.append(item)
        self.sources.update(i.source for i in items)
        return fresh

    def save(self, now: datetime | None = None) -> None:
        if now is not None:
            cutoff = now - RETENTION
            self.seen = {k: v for k, v in self.seen.items() if datetime.fromisoformat(v) >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"sources": sorted(self.sources), "seen": self.seen}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
