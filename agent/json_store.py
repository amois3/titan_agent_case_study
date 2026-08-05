"""Shared JSON persistence helpers with backup and self-healing."""
from __future__ import annotations

import copy
import glob
import json
import logging
import os
import shutil
import tempfile
import time
from typing import Any

logger = logging.getLogger(__name__)

_BACKUP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backups', 'json'))


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)


def _backup_name(path: str) -> str:
    base = os.path.basename(path)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    return os.path.join(_BACKUP_ROOT, f"{base}.{stamp}.bak")


def backup_json(path: str) -> str | None:
    """Copy the current JSON file into backups/json/ before overwriting."""
    if not os.path.exists(path):
        return None

    try:
        os.makedirs(_BACKUP_ROOT, exist_ok=True)
        backup_path = _backup_name(path)
        shutil.copy2(path, backup_path)
        return backup_path
    except Exception as e:
        logger.warning(f"backup_json failed for {path}: {e}")
        return None


def _iter_backup_candidates(path: str):
    base = os.path.basename(path)
    pattern = os.path.join(_BACKUP_ROOT, f"{base}.*.bak")
    candidates = []

    direct_bak = f"{path}.bak"
    if os.path.exists(direct_bak):
        candidates.append(direct_bak)

    candidates.extend(sorted(glob.glob(pattern), reverse=True))
    return candidates


def safe_load_json(path: str, default: Any = None, repair: bool = True) -> Any:
    """Load JSON and fall back to the latest backup if the file is broken."""
    if default is None:
        default = {}

    if not os.path.exists(path):
        return copy.deepcopy(default)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as primary_error:
        logger.warning(f"JSON load failed for {path}: {primary_error}")

    for candidate in _iter_backup_candidates(path):
        try:
            with open(candidate, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.warning(f"Recovered JSON for {path} from backup {candidate}")
            if repair:
                safe_write_json(path, data, backup_current=False)
            return data
        except Exception:
            continue

    return copy.deepcopy(default)


def safe_write_json(path: str, data: Any, *, indent: int = 2, ensure_ascii: bool = False, backup_current: bool = True) -> None:
    """Atomically write JSON to disk, optionally backing up the previous file first."""
    _ensure_dir(path)

    if backup_current and os.path.exists(path):
        backup_json(path)

    directory = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmp_path = tempfile.mkstemp(prefix='.tmp_json_', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise
