"""Build and persist a compact memory manifest for TITAN."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from agent.json_store import safe_load_json, safe_write_json
from agent.memory_graph import build_memory_graph

_DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
_MANIFEST_PATH = os.path.join(_DATA_DIR, 'memory_manifest.json')
_USER_FACTS_PATH = os.path.join(_DATA_DIR, 'user_facts.json')
_CURRENT_STATE_PATH = os.path.join(_DATA_DIR, 'current_state.json')
_PROJECTS_INDEX_PATH = os.path.join(_DATA_DIR, 'projects_index.json')
_CLIENTS_DIR = os.path.join(_DATA_DIR, 'clients')
_ARCHIVE_DIR = os.path.join(_DATA_DIR, 'archive')


def _read_client_meta(profile_path: str) -> dict[str, Any]:
    import re

    meta = {'stage': 'lead', 'last_activity': '', 'next_followup': ''}
    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            content = f.read()
        m = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if m:
            for line in m.group(1).split('\n'):
                if ':' in line:
                    key, val = line.split(':', 1)
                    meta[key.strip()] = val.strip()
    except Exception:
        pass
    return meta


def _count_json_files(path: str) -> int:
    total = 0
    if not os.path.isdir(path):
        return 0
    for root, _, files in os.walk(path):
        for fname in files:
            if fname.endswith('.json') or fname.endswith('.jsonl'):
                total += 1
    return total


def build_memory_manifest() -> dict[str, Any]:
    """Build a compact, deterministic snapshot of current memory layers."""
    current_state = safe_load_json(_CURRENT_STATE_PATH, default={})
    if not isinstance(current_state, dict):
        current_state = {}
    facts = safe_load_json(_USER_FACTS_PATH, default={})
    if not isinstance(facts, dict):
        facts = {}
    projects_index = safe_load_json(_PROJECTS_INDEX_PATH, default={})
    if not isinstance(projects_index, dict):
        projects_index = {}
    graph = build_memory_graph('', limit=8)

    def _goal_is_hot(goal: dict[str, Any]) -> bool:
        if not isinstance(goal, dict):
            return False
        status = str(goal.get('status', 'active')).lower()
        if status not in {'active', 'waiting', 'paused'}:
            return False
        # Explicit active goals stay hot until their status changes. Age-only
        # demotion belongs to hygiene/archive flows, not the manifest view.
        return True

    def _decision_is_hot(decision: dict[str, Any]) -> bool:
        if not isinstance(decision, dict):
            return False
        status = str(decision.get('status', 'active')).lower()
        if status not in {'active', 'review'}:
            return False
        # Explicit active/review decisions stay hot until their status changes.
        return True

    active_clients: list[dict[str, Any]] = []
    cold_clients = 0
    if os.path.isdir(_CLIENTS_DIR):
        for name in sorted(os.listdir(_CLIENTS_DIR)):
            profile_path = os.path.join(_CLIENTS_DIR, name, 'profile.md')
            if not os.path.exists(profile_path):
                continue
            meta = _read_client_meta(profile_path)
            entry = {
                'name': name,
                'stage': meta.get('stage', 'lead'),
                'last_activity': meta.get('last_activity', ''),
                'next_followup': meta.get('next_followup', ''),
            }
            if entry['stage'] in {'lead', 'waiting', 'active', 'delivered'}:
                active_clients.append(entry)
            else:
                cold_clients += 1

    active_projects: list[dict[str, Any]] = []
    cold_projects = 0
    for name, meta in projects_index.items():
        if not isinstance(meta, dict):
            continue
        entry = {
            'name': name,
            'status': meta.get('status', 'unknown'),
            'type': meta.get('type', ''),
            'added': meta.get('added', ''),
            'file': meta.get('file', ''),
        }
        if entry['status'] in {'active', 'paused', 'closed_success'}:
            active_projects.append(entry)
        else:
            cold_projects += 1

    manifest = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'focus': {
            'current_topic': current_state.get('current_topic', ''),
            'active_project': current_state.get('active_project', ''),
            'active_client': current_state.get('active_client', ''),
            'open_tasks': (current_state.get('open_tasks', []) or [])[:5],
            'notes': current_state.get('notes', ''),
        },
        'hot': {
            'facts_count': len(facts.get('facts', []) or []),
            'about_user_count': len(facts.get('about_user', []) or []),
            'goals_count': len(facts.get('goals', []) or []),
            'active_goals': [
                g.get('title', '')
                for g in (facts.get('goals', []) or [])
                if _goal_is_hot(g)
            ][:5],
            'recent_decisions': [
                d.get('decision', '')
                for d in (facts.get('key_decisions', []) or [])
                if _decision_is_hot(d)
            ][-5:],
        },
        'warm': {
            'sessions_count': len(facts.get('sessions', []) or []),
            'research_notes_count': len(facts.get('research_notes', []) or []),
            'completed_goals_count': len(facts.get('completed_goals', []) or []),
        },
        'cold': {
            'archive_files': _count_json_files(_ARCHIVE_DIR),
            'client_archive_files': _count_json_files(os.path.join(_ARCHIVE_DIR, 'clients')),
            'project_archive_files': _count_json_files(os.path.join(_ARCHIVE_DIR, 'projects')),
            'memory_archive_files': _count_json_files(os.path.join(_ARCHIVE_DIR, 'memory_summaries')),
            'inactive_clients': cold_clients,
            'inactive_projects': cold_projects,
        },
        'lists': {
            'active_clients': active_clients[:8],
            'active_projects': active_projects[:8],
        },
        'links': {
            'bridge_count': len(graph.get('bridges', [])),
            'top_bridges': graph.get('bridges', [])[:3],
        },
    }
    return manifest


def save_memory_manifest(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist the manifest to disk and return it."""
    if manifest is None:
        manifest = build_memory_manifest()
    safe_write_json(_MANIFEST_PATH, manifest)
    return manifest


def load_memory_manifest() -> dict[str, Any]:
    data = safe_load_json(_MANIFEST_PATH, default={})
    return data if isinstance(data, dict) else {}
