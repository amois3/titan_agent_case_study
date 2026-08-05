"""Deterministic memory graph for TITAN.

Builds explicit links between current focus, goals, decisions, clients, and projects.
The graph is intentionally conservative: it only surfaces links backed by
token overlap, explicit status, or direct metadata matches.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from agent.json_store import safe_load_json, safe_write_json

_DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
_GRAPH_PATH = os.path.join(_DATA_DIR, 'memory_graph.json')
_USER_FACTS_PATH = os.path.join(_DATA_DIR, 'user_facts.json')
_CURRENT_STATE_PATH = os.path.join(_DATA_DIR, 'current_state.json')
_PROJECTS_INDEX_PATH = os.path.join(_DATA_DIR, 'projects_index.json')
_CLIENTS_DIR = os.path.join(_DATA_DIR, 'clients')

_WORK_HINTS = {
    'project', 'client', 'contract', 'proposal', 'cover', 'letter', 'vacancy', 'job',
    'upwork', 'freelance', 'portfolio', 'launch', 'billing', 'saas', 'agent', 'automation',
    'ai', 'llm', 'prompt', 'web', 'frontend', 'backend', 'react', 'next', 'python',
    'grid', 'trading', 'bot', 'analytics', 'marketing', 'seo', 'pwa',
}


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r'[a-z0-9а-яё]+', (text or '').lower()) if len(token) >= 3}


def _entry_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ('title', 'decision', 'summary', 'text', 'topic', 'conclusion', 'change', 'notes'):
            value = item.get(key)
            if value:
                return str(value)
        return str(item)
    return str(item or '')


def _parse_date(value: Any):
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.strptime(value[:10], '%Y-%m-%d').date()
    except Exception:
        return None


def _age_days(item: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    if not isinstance(item, dict):
        return None
    for key in keys:
        parsed = _parse_date(item.get(key))
        if parsed is not None:
            return (datetime.now().date() - parsed).days
    return None


def _goal_is_hot(goal: dict[str, Any], focus_tokens: set[str]) -> bool:
    if not isinstance(goal, dict):
        return False
    status = str(goal.get('status', 'active')).lower()
    if status not in {'active', 'waiting', 'paused'}:
        return False
    age = _age_days(goal, ('updated_at', 'started', 'date'))
    if age is not None and age > 45:
        title_tokens = _tokenize(_entry_text(goal))
        if not (focus_tokens & title_tokens):
            return False
    return True


def _load_client_names() -> set[str]:
    names: set[str] = set()
    if not os.path.isdir(_CLIENTS_DIR):
        return names
    for name in os.listdir(_CLIENTS_DIR):
        if len(name) >= 3:
            names.add(name.lower())
    return names


def _load_sources() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    current_state = safe_load_json(_CURRENT_STATE_PATH, default={})
    if not isinstance(current_state, dict):
        current_state = {}
    facts = safe_load_json(_USER_FACTS_PATH, default={})
    if not isinstance(facts, dict):
        facts = {}
    projects_index = safe_load_json(_PROJECTS_INDEX_PATH, default={})
    if not isinstance(projects_index, dict):
        projects_index = {}
    return current_state, facts, projects_index


def _score_overlap(tokens: set[str], haystack: str) -> tuple[int, set[str]]:
    hay_tokens = _tokenize(haystack)
    overlap = tokens & hay_tokens
    score = len(overlap)
    return score, overlap


def _make_bridge(source: str, target: str, relation: str, reason: str, score: int, kind: str) -> dict[str, Any]:
    return {
        'source': source,
        'target': target,
        'relation': relation,
        'reason': reason,
        'score': score,
        'kind': kind,
    }


def build_memory_graph(user_message: str = '', limit: int = 8) -> dict[str, Any]:
    """Build a conservative graph of memory bridges for the current context."""
    current_state, facts, projects_index = _load_sources()
    msg_tokens = _tokenize(user_message)
    focus_tokens = _tokenize(' '.join(str(current_state.get(key, '')) for key in ('current_topic', 'active_project', 'active_client', 'notes')))
    combined_tokens = msg_tokens | focus_tokens
    client_names = _load_client_names()

    focus = {
        'current_topic': current_state.get('current_topic', ''),
        'active_project': current_state.get('active_project', ''),
        'active_client': current_state.get('active_client', ''),
        'open_tasks': (current_state.get('open_tasks', []) or [])[:5],
    }

    bridges: list[dict[str, Any]] = []

    # Goals and decisions from hot memory
    for goal in facts.get('goals', []) or []:
        if not _goal_is_hot(goal, combined_tokens):
            continue
        title = str(goal.get('title', '')).strip()
        if not title:
            continue
        title_tokens = _tokenize(title + ' ' + str(goal.get('notes', '')))
        overlap = combined_tokens & title_tokens
        if not overlap and not focus_tokens:
            continue
        score = len(overlap) + 1
        reason = f"совпадение: {', '.join(sorted(overlap))}" if overlap else "связано с текущим фокусом"
        bridges.append(_make_bridge('focus_or_query', f"goal:{title}", 'supports', reason, score, 'goal'))

    for decision in facts.get('key_decisions', []) or []:
        if not isinstance(decision, dict):
            continue
        status = str(decision.get('status', 'active')).lower()
        if status not in {'active', 'review'}:
            continue
        age = _age_days(decision, ('reviewed_at', 'updated_at', 'date'))
        text = _entry_text(decision).strip()
        if not text:
            continue
        text_tokens = _tokenize(text)
        overlap = combined_tokens & text_tokens
        if not overlap and age is not None and age > 30:
            continue
        score = len(overlap) + (1 if status == 'review' else 2)
        reason = f"совпадение: {', '.join(sorted(overlap))}" if overlap else f"статус: {status}"
        bridges.append(_make_bridge('focus_or_query', f"decision:{text}", 'recalls', reason, score, 'decision'))

    # Projects index
    for name, meta in projects_index.items():
        if not isinstance(meta, dict):
            continue
        status = str(meta.get('status', 'unknown')).lower()
        if status not in {'active', 'paused', 'closed_success'}:
            continue
        label = str(name).strip()
        if not label:
            continue
        keywords = ' '.join([
            label,
            str(meta.get('type', '')),
            ' '.join(meta.get('keywords', []) or []),
            ' '.join(meta.get('stack', []) or []),
        ])
        score, overlap = _score_overlap(combined_tokens, keywords)
        if score == 0 and not (msg_tokens and (msg_tokens & {'cover', 'letter', 'proposal', 'upwork', 'portfolio', 'vacancy', 'job'})):
            continue
        if score == 0 and status != 'closed_success':
            continue
        if score == 0:
            score = 1
        if status == 'closed_success':
            score += 1
        reason = f"совпадение: {', '.join(sorted(overlap))}" if overlap else f"статус: {status}"
        bridges.append(_make_bridge('focus_or_query', f"project:{label}", 'matches', reason, score, 'project'))

    # Clients
    if os.path.isdir(_CLIENTS_DIR):
        for name in sorted(os.listdir(_CLIENTS_DIR)):
            profile_path = os.path.join(_CLIENTS_DIR, name, 'profile.md')
            if not os.path.exists(profile_path):
                continue
            try:
                with open(profile_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue
            meta_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
            stage = 'lead'
            if meta_match:
                for line in meta_match.group(1).split('\n'):
                    if line.startswith('stage:'):
                        stage = line.split(':', 1)[1].strip().lower()
                        break
            if stage in {'cold', 'archived'}:
                continue
            label = name.replace('_', ' ').strip()
            haystack = f"{label} {content[:700]}"
            score, overlap = _score_overlap(combined_tokens, haystack)
            if score == 0 and name.lower() not in client_names:
                continue
            if score == 0 and not overlap:
                continue
            reason = f"совпадение: {', '.join(sorted(overlap))}" if overlap else f"stage: {stage}"
            bridges.append(_make_bridge('focus_or_query', f"client:{label}", 'relates_to', reason, score + 1, 'client'))

    bridges.sort(key=lambda item: (-int(item.get('score', 0)), item.get('kind', ''), item.get('target', '')))

    if limit > 0:
        bridges = bridges[:limit]

    graph = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'focus': focus,
        'query': user_message[:240],
        'bridges': bridges,
        'stats': {
            'bridges': len(bridges),
            'goals_scanned': len(facts.get('goals', []) or []),
            'decisions_scanned': len(facts.get('key_decisions', []) or []),
            'projects_scanned': len(projects_index),
            'clients_scanned': len(client_names),
        },
    }
    return graph


def format_memory_bridges(user_message: str = '', limit: int = 4) -> str:
    graph = build_memory_graph(user_message=user_message, limit=limit)
    bridges = graph.get('bridges', []) if isinstance(graph, dict) else []
    if not bridges:
        return ''
    lines = ["[🕸 СВЯЗИ ПАМЯТИ]"]
    for bridge in bridges[:limit]:
        source = bridge.get('source', 'focus')
        target = bridge.get('target', '')
        reason = bridge.get('reason', '')
        lines.append(f"- {source} → {target} ({reason})")
    return "\n".join(lines)


def save_memory_graph(graph: dict[str, Any] | None = None) -> dict[str, Any]:
    if graph is None:
        graph = build_memory_graph()
    safe_write_json(_GRAPH_PATH, graph)
    return graph


def load_memory_graph() -> dict[str, Any]:
    graph = safe_load_json(_GRAPH_PATH, default={})
    return graph if isinstance(graph, dict) else {}
