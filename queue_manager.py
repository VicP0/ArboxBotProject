"""
Persistent registration queue.

Stores pending registration requests in a JSON file so they survive
bot restarts.  The queue is drained at Saturday 21:00 when Arbox
opens next-week registration.

File format (registration_queue.json):
    [
        {"date": "2025-03-02", "time": "07:00"},
        ...
    ]
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

QUEUE_FILE = Path("registration_queue.json")


def _load() -> list[dict]:
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(queue: list[dict]) -> None:
    QUEUE_FILE.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_to_queue(target_date: date, time_str: str) -> bool:
    """
    Add a registration request to the queue.
    Returns True if added, False if already present.
    """
    queue = _load()
    entry = {"date": target_date.isoformat(), "time": time_str}
    if entry in queue:
        return False
    queue.append(entry)
    _save(queue)
    return True


def get_queue() -> list[dict]:
    """
    Return all queued registrations as a list of dicts:
        {"date": date, "time": str}
    Dates are parsed from ISO strings back to date objects.
    """
    raw = _load()
    return [{"date": date.fromisoformat(r["date"]), "time": r["time"]} for r in raw]


def remove_from_queue(target_date: date, time_str: str) -> bool:
    """Remove a specific entry.  Returns True if it was present."""
    queue = _load()
    entry = {"date": target_date.isoformat(), "time": time_str}
    if entry not in queue:
        return False
    queue.remove(entry)
    _save(queue)
    return True


def clear_queue() -> None:
    """Remove all entries from the queue."""
    _save([])
