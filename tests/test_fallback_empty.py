"""An empty answer must never slip through, on any path to a model.

The guard against "reasoning ate the output budget" originally lived only
inside the Poe retry loop. But OpenRouter is called from two places here —
from the fallback, and directly whenever a role is pinned to a non-Poe model.
Both bypassed the check, and when Poe runs out of credit every request takes
exactly those bypassing paths.

These tests pin down that the retry with a raised limit happens on both.
"""
import asyncio
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from agent import provider  # noqa: E402


def _empty_from_reasoning():
    """A reasoning model's reply where the thinking consumed the limit."""
    return {"choices": [{"finish_reason": "stop",
                         "message": {"content": "", "reasoning_content": "thinking..."}}]}


def _healthy(text="Hello"):
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


@pytest.fixture
def openrouter(monkeypatch):
    """Replaces OpenRouter and records the limit used on every call.

    The recorded limits are what reveal whether a retry happened and with
    what budget.
    """
    async def _no_wait(*_a, **_kw):
        return None

    monkeypatch.setattr(provider.asyncio, "sleep", _no_wait)
    limits: list[int] = []

    def serve(*replies):
        queue = list(replies)

        async def _call(model, payload, timeout):
            limits.append(payload["max_tokens"])
            return queue.pop(0) if len(queue) > 1 else queue[0]

        monkeypatch.setattr(provider, "_call_openrouter", _call)
        return limits

    return serve


@pytest.fixture
def poe_down(monkeypatch):
    """Poe is unreachable, so the call has to reach the fallback."""
    async def _down(*_a, **_kw):
        raise provider.ProviderError(500, "out of credit")

    monkeypatch.setattr(provider, "_call_poe", _down)


def _ask(model, **kw):
    return asyncio.run(provider.call_model(
        model, [{"role": "user", "content": "hello"}], **kw))


def test_fallback_retries_an_empty_answer(openrouter, poe_down):
    limits = openrouter(_empty_from_reasoning(), _healthy("Done"))

    answer = _ask("claude-opus-4.8", max_tokens=2000)

    assert len(limits) == 2, "an empty fallback answer must trigger a retry"
    assert limits[1] > limits[0], "the retry must raise the limit"
    assert answer["choices"][0]["message"]["content"] == "Done"


def test_direct_openrouter_path_retries_too(openrouter):
    """A non-Poe model skips the retry loop, so it needs the check as well."""
    limits = openrouter(_empty_from_reasoning(), _healthy("Done"))

    answer = _ask("z-ai/glm-5.2", max_tokens=2000)

    assert len(limits) == 2, "the direct OpenRouter path was bypassing the check"
    assert answer["choices"][0]["message"]["content"] == "Done"


def test_a_healthy_answer_is_not_retried(openrouter, poe_down):
    limits = openrouter(_healthy())

    _ask("claude-opus-4.8", max_tokens=2000)

    assert len(limits) == 1, "a needless retry is needless money"


def test_retrying_does_not_run_forever(openrouter, poe_down):
    """If the model keeps going quiet we give up, rather than hammering it."""
    limits = openrouter(_empty_from_reasoning())

    _ask("claude-opus-4.8", max_tokens=2000)

    assert len(limits) == 2, "the fallback is already the emergency path — one retry is enough"


def test_a_raised_limit_is_not_retried(openrouter, poe_down):
    """At a large limit the emptiness has another cause, and a retry won't cure it."""
    limits = openrouter(_empty_from_reasoning())

    _ask("claude-opus-4.8", max_tokens=16384)

    assert len(limits) == 1


def test_no_path_calls_openrouter_directly():
    """It must not be possible to forget the check in some new place."""
    source = inspect.getsource(provider.call_model)
    assert "_call_openrouter(" not in source, \
        "call_model reaches OpenRouter past the guard — an empty answer would slip through"
    assert source.count("_openrouter_checked(") == 2, \
        "both paths, fallback and direct, must go through the guard"
