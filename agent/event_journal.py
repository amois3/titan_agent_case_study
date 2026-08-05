"""Append-only event journal for TITAN runtime state changes."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from agent.json_store import safe_load_json

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
_JOURNAL_PATH = os.getenv('TITAN_EVENT_JOURNAL_PATH') or os.path.join(_DATA_DIR, 'event_journal.jsonl')


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def append_event(event_type: str, details: dict[str, Any] | None = None, *, source: str = 'system', severity: str = 'info') -> dict[str, Any]:
    """Append a structured event to the runtime journal."""
    event = {
        'ts': _now_iso(),
        'type': str(event_type or 'event'),
        'source': source,
        'severity': severity,
        'details': details or {},
    }
    try:
        os.makedirs(os.path.dirname(_JOURNAL_PATH), exist_ok=True)
        with open(_JOURNAL_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        logger.debug(f"append_event failed: {e}")
    return event


def read_recent_events(limit: int = 20, event_type: str | None = None) -> list[dict[str, Any]]:
    """Read the most recent events from the journal."""
    if not os.path.exists(_JOURNAL_PATH):
        return []
    try:
        with open(_JOURNAL_PATH, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception:
        return []

    events: list[dict[str, Any]] = []
    for line in lines[-max(limit * 3, limit):]:
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event_type and event.get('type') != event_type:
            continue
        events.append(event)
    return events[-limit:]


def load_journal_stats() -> dict[str, Any]:
    """Small helper for health snapshots."""
    events = read_recent_events(limit=200)
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for event in events:
        by_type[event.get('type', 'event')] = by_type.get(event.get('type', 'event'), 0) + 1
        by_severity[event.get('severity', 'info')] = by_severity.get(event.get('severity', 'info'), 0) + 1
    return {
        'path': _JOURNAL_PATH,
        'count': len(events),
        'by_type': by_type,
        'by_severity': by_severity,
    }

