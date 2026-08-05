"""Single entry point for every language-model call TITAN makes.

Poe is the primary provider: that is where the subscription lives, along with
the strongest models and a million-token context window. Everything used to run
on OpenRouter, and the logs filled up with 429s and 504s as the paid balance ran
low — on mail triage that turned into silently missed messages.

OpenRouter stays on as the fallback and as the source of embeddings: Poe has no
embedding endpoint at all, so vector_store still calls out to it. That costs
pennies.

Every role gets its own model. Writing code, auditing the trading bot and
drafting a letter are different jobs and deserve different models. The codebase
refers to role names, never to model names: swapping a model happens here and in
.env, without touching the core.

This module is self-contained and does not reach into the core —
TITAN_CONSTITUTION.md permits exactly that ("new modules are safe as long as
they stay isolated").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

POE_URL = "https://api.poe.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Roles. Any default below can be overridden by an environment variable
# named TITAN_MODEL_<ROLE> — for example TITAN_MODEL_ENGINEER=gpt-5.3-codex.
_DEFAULT_MODELS = {
    # Main conversation and tool use. Grok 4.1 Fast Reasoning: a two-million
    # token window, reasoning-capable, and on our request profile (twenty
    # thousand tokens in, five hundred out) it comes out twenty-two times
    # cheaper than Opus. Tool calls and result hand-back were verified with a
    # live request before it went in.
    # Rollback if it starts derailing on long chains: put claude-opus-4.8 back
    # here, or set TITAN_MODEL_CHAT in .env.
    'chat': 'grok-4.1-fast-reasoning',
    'tools': 'grok-4.1-fast-reasoning',
    # Vision runs on gpt-5.4 rather than Opus: at some point the subscription
    # stopped covering Opus (402, while every other model still answered 200).
    # gpt-5.4 reads images correctly — verified with a live request before the
    # switch.
    'vision': 'gpt-5.4',
    # BACKGROUND. Runs unattended around the clock; the owner never talks to
    # these models directly: nightly digest, mail triage, daily summary.
    # Gemini 2.5 Flash Lite was tested on exactly these jobs before adoption —
    # strict JSON for digests, an invoice with a deadline classified as URGENT,
    # a marketing blast as SPAM. Twenty-six times cheaper than Sonnet on input.
    # If mail triage starts getting things wrong, put claude-sonnet-4.6 back
    # first: one missed invoice costs more than the savings are worth.
    'memory': 'gemini-2.5-flash-lite',
    'mail': 'gemini-2.5-flash-lite',
    # Analytics and heartbeat: background as well, read by a human only as
    # finished prose. The 1024k window covers long documents, and no tools are
    # needed — both jobs are a single short request with no tool calls.
    'analyst': 'gemini-2.5-flash-lite',

    # Subagents.
    'engineer': 'claude-opus-4.8',   # code: edits, debugging, migrations
    'scout': 'gpt-5.4',              # job search and triage: web search + tools
    'scribe': 'claude-opus-4.8',     # letters, cover letters, prose
    'trader': 'gpt-5.4',             # grid bot: hard numbers, no improvisation
    'nutriai_analyst': 'gpt-5.4',    # metrics and traffic — precision again
    'proposal': 'claude-opus-4.8',   # proposals: this is a letter, not a report
}

# Fallback model: when Poe is unreachable we answer through OpenRouter.
_FALLBACK_MODEL = os.getenv('TITAN_FALLBACK_MODEL', 'z-ai/glm-5.2')

_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_TIMEOUT = 180


def poe_api_key() -> str:
    return os.getenv('POE_API_KEY', '').strip()


def openrouter_api_key() -> str:
    return os.getenv('OPENROUTER_API_KEY', '').strip()


def model_for(role: str) -> str:
    """Model name for a role. Overridden by TITAN_MODEL_<ROLE>."""
    configured = os.getenv('TITAN_MODEL_' + role.upper(), '').strip()
    if configured:
        return configured
    return _DEFAULT_MODELS.get(role, _DEFAULT_MODELS['chat'])


def is_poe_model(model: str) -> bool:
    """Poe model or someone else's. Poe names carry no slash: claude-opus-4.8."""
    return bool(model) and '/' not in model


class ProviderError(Exception):
    """Provider answered with an error code. The code decides whether to retry."""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def _empty_because_of_reasoning(data: dict) -> bool:
    """True when the answer is empty because reasoning ate the token budget.

    On reasoning models the thinking is billed against the same output budget.
    Poe puts it in a separate reasoning_content field and still returns
    finish_reason='stop' rather than 'length' — so by finish code alone such a
    response is indistinguishable from a healthy one. We therefore look at
    substance: no content, no tool calls, but reasoning is present.

    TITAN's main model is a reasoning model, so this is not a rare case.
    """
    try:
        choice = data['choices'][0]
    except (KeyError, IndexError, TypeError):
        return False
    message = choice.get('message') or {}

    is_empty = not (message.get('content') or '').strip() and not message.get('tool_calls')
    if not is_empty:
        return False

    reasoned = bool((message.get('reasoning_content') or '').strip())
    return reasoned or choice.get('finish_reason') == 'length'


async def _post(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise ProviderError(resp.status, body[:400])
            return await resp.json()


async def _call_poe(model: str, payload: dict, timeout: int) -> dict:
    key = poe_api_key()
    if not key:
        raise ProviderError(401, "POE_API_KEY is not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    return await _post(POE_URL, headers, dict(payload, model=model), timeout)


async def _call_openrouter(model: str, payload: dict, timeout: int) -> dict:
    key = openrouter_api_key()
    if not key:
        raise ProviderError(401, "OPENROUTER_API_KEY is not set")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Application attribution for OpenRouter; does not affect the answer.
        # The address comes from the environment so no host is baked into the code.
        "HTTP-Referer": os.getenv('TITAN_PUBLIC_URL', 'https://titan-agent.local'),
        "X-Title": "TITAN Agent",
    }
    return await _post(OPENROUTER_URL, headers, dict(payload, model=model), timeout)


# What we substitute for an image when the model cannot see. The note has to
# be explicit: without it the model receives a message with the picture missing
# and confidently reasons about something it never saw.
_IMAGE_LOST = '[изображение приложено, но эта модель его не видит — попроси описать словами]'


def _model_sees_images(model: str) -> bool:
    """Whether the model can look at images.

    Matched by family rather than exact name: versions change far more often
    than the ability to see does.
    """
    name = (model or '').lower()
    return any(tag in name for tag in ('claude', 'gemini', 'gpt-5', 'grok'))


def _strip_unsupported(messages: list, model: str) -> list:
    """Reshapes a request into what a particular model actually understands.

    cache_control is an Anthropic extension. To every other provider a
    structured block carrying that field arrives as garbage and takes the whole
    request down with it.

    Images, on the other hand, must survive the trip. This function used to
    collect text blocks only, so a picture vanished silently for every
    non-Claude model — including the ones that see perfectly well.
    """
    if 'claude' in (model or '').lower():
        return messages

    sees_images = _model_sees_images(model)
    cleaned = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get('content')
        if isinstance(content, list):
            blocks, texts, kept_image = [], [], False
            for block in content:
                if not isinstance(block, dict):
                    continue
                block = {k: v for k, v in block.items() if k != 'cache_control'}
                if block.get('type') == 'text':
                    texts.append(block.get('text') or '')
                    blocks.append(block)
                elif 'image' in str(block.get('type', '')):
                    if sees_images:
                        blocks.append(block)
                        kept_image = True
                    else:
                        texts.append(_IMAGE_LOST)
                        blocks.append({'type': 'text', 'text': _IMAGE_LOST})

            if kept_image:
                # The image survived, so the structured form has to stay:
                # a picture will not fit into a flat string.
                msg = dict(msg, content=blocks)
            else:
                flat = '\n'.join(t for t in texts if t)
                if not flat and msg.get('role') == 'system':
                    continue
                msg = dict(msg, content=flat)
        cleaned.append(msg)
    return cleaned


async def _openrouter_checked(model: str, payload: dict, timeout: int) -> dict:
    """OpenRouter with the same empty-answer guard the Poe path already has.

    The fallback model reasons too: the thinking eats the output budget and an
    empty string comes back. On Poe the retry loop catches this — here there is
    no loop, so we make exactly one more attempt with double the limit.
    """
    data = await _call_openrouter(model, payload, timeout)
    if _empty_because_of_reasoning(data) and payload["max_tokens"] < 16384:
        payload["max_tokens"] *= 2
        logger.warning("Empty answer from %s (fallback): retrying with max_tokens=%s",
                       model, payload["max_tokens"])
        data = await _call_openrouter(model, payload, timeout)
    return data


async def call_model(
    model: str,
    messages: list,
    *,
    tools: list | None = None,
    temperature: float = 0.7,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: int = _DEFAULT_TIMEOUT,
    allow_fallback: bool = True,
) -> dict:
    """Call a specific model. Returns an OpenAI-shaped response.

    A name without a slash is a Poe model, with a slash it is OpenRouter. That
    way older code passing names like "openai/text-embedding-3-small" keeps
    working untouched.
    """
    payload: dict = {
        "messages": _strip_unsupported(messages, model),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    if not is_poe_model(model):
        return await _openrouter_checked(model, payload, timeout)

    last_error: Exception | None = None

    for attempt in range(3):
        try:
            data = await _call_poe(model, payload, timeout)
            if _empty_because_of_reasoning(data) and payload["max_tokens"] < 16384:
                payload["max_tokens"] *= 2
                logger.warning("Empty answer from %s: reasoning ate the budget, retrying with max_tokens=%s",
                               model, payload["max_tokens"])
                continue
            return data
        except ProviderError as exc:
            last_error = exc
            # Retrying a 4xx (other than 429) is pointless — the request will not get any more valid.
            if exc.status < 500 and exc.status != 429:
                logger.error("Poe refused (%s), model %s: %s", exc.status, model, exc.body[:200])
                break
            logger.warning("Poe %s, attempt %s/3", exc.status, attempt + 1)
            await asyncio.sleep(3 + attempt * 5)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = exc
            logger.warning("Network to Poe (%s), attempt %s/3", type(exc).__name__, attempt + 1)
            await asyncio.sleep(3 + attempt * 5)

    if not allow_fallback:
        raise last_error or ProviderError(500, "Poe is unreachable")

    logger.warning("Falling back to OpenRouter/%s", _FALLBACK_MODEL)
    try:
        fallback = dict(payload, messages=_strip_unsupported(messages, _FALLBACK_MODEL))
        return await _openrouter_checked(_FALLBACK_MODEL, fallback, timeout)
    except Exception as exc:
        logger.error("The fallback failed as well: %s", exc)
        raise last_error or exc


async def chat(role: str, messages: list, **kwargs) -> dict:
    """Call the model for a ROLE — the preferred way in."""
    return await call_model(model_for(role), messages, **kwargs)


async def complete(role: str, prompt: str, *, system: str = "", temperature: float = 0.7,
                   max_tokens: int = 1024, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """One question, one text answer. For background jobs with no tools."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = await chat(role, messages, temperature=temperature,
                      max_tokens=max_tokens, timeout=timeout)
    try:
        return (data['choices'][0]['message'].get('content') or '').strip()
    except (KeyError, IndexError, TypeError):
        logger.warning("Answer carried no text: %s", json.dumps(data, ensure_ascii=False)[:300])
        return ""


def human_error(exc: Exception) -> str:
    """A human explanation instead of the provider's technical answer."""
    text = str(exc).lower()
    if any(k in text for k in ('401', 'invalid api key', 'no auth', 'unauthorized', 'is not set', 'не задан')):
        return "Ключ доступа к модели не принят — проверь POE_API_KEY в .env."
    if any(k in text for k in ('insufficient', 'credit', 'balance', 'quota', 'payment', '402')):
        return "Закончились средства на подписке — пополни, и я снова смогу отвечать."
    if any(k in text for k in ('rate limit', '429', 'too many requests')):
        return "Слишком много запросов подряд. Подожди минуту и повтори."
    if any(k in text for k in ('timeout', 'timed out', 'connection', 'unreachable',
                               'dns', '502', '503', 'network')):
        return "Не дозвонился до модели — похоже, пропала связь. Повтори через минуту."
    return f"Ошибка на моей стороне: {exc}"
