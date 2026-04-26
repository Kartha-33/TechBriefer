"""SQLite-backed seen-URL history. 7-day rolling dedup, 30-day janitor."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "history.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_stories (
            url     TEXT PRIMARY KEY,
            title   TEXT,
            source  TEXT,
            seen_on TEXT
        )
        """
    )
    c.commit()
    return c


def get_seen_urls(days: int = 7) -> set[str]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    c = _conn()
    rows = c.execute(
        "SELECT url FROM seen_stories WHERE seen_on >= ?", (cutoff,)
    ).fetchall()
    c.close()
    return {r[0] for r in rows}


def mark_seen(stories: list) -> None:
    if not stories:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    c = _conn()
    c.executemany(
        "INSERT OR REPLACE INTO seen_stories (url, title, source, seen_on) VALUES (?, ?, ?, ?)",
        [(s.url, s.title, s.source, today) for s in stories],
    )
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    c.execute("DELETE FROM seen_stories WHERE seen_on < ?", (cutoff,))
    c.commit()
    c.close()
