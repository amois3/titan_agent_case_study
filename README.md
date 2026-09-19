# TITAN — the provider and memory core, extracted

[![CI](https://github.com/amois3/titan_agent_case_study/actions/workflows/ci.yml/badge.svg)](https://github.com/amois3/titan_agent_case_study/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776ab?logo=python&logoColor=white)](requirements-dev.txt)
[![Runtime deps](https://img.shields.io/badge/runtime%20dependencies-1-brightgreen)](requirements-dev.txt)
[![Licence](https://img.shields.io/badge/licence-review%20only-lightgrey)](LICENSE)

TITAN is an autonomous personal agent that has run unattended on a single host
since December 2025. It reads my mail and decides what deserves my attention,
briefs me each morning, watches a live trading bot, keeps a long-term memory of
my projects and clients, and edits its own source under constraints I set. It
holds write access to my filesystem, control of my devices, and supervision of a
live trading account. The implementation is private. I walk through the code in
interviews.

This repository is not a description of it. It is the layer that has to be right
before any of that is defensible — how a model is chosen and what happens when
the provider fails — lifted out with its tests, so it can be read, run and
disagreed with.

```bash
pip install -r requirements-dev.txt && python -m pytest -q   # 17 tests, no API key
```

---

## What is here

Five modules, 983 lines, one runtime dependency. 225 lines of tests, and the four
design documents the project ships with.

| Module | Lines | What it does |
|---|---:|---|
| [`provider.py`](agent/provider.py) | 351 | Role → model routing, Poe primary with an OpenRouter fallback, retry policy, and the guards below |
| [`memory_graph.py`](agent/memory_graph.py) | 271 | Builds the graph of what relates to what, and renders the slice worth showing the model |
| [`memory_manifest.py`](agent/memory_manifest.py) | 176 | The manifest that keeps memory addressable as it grows |
| [`json_store.py`](agent/json_store.py) | 107 | Atomic reads and writes: a crash mid-write must not leave half a file |
| [`event_journal.py`](agent/event_journal.py) | 78 | Append-only record of what the agent did |
| [`tests/`](tests) | 225 | Two production defects, pinned down so they cannot come back |

## The problem this layer solves

An agent that runs unattended fails differently from one a person is watching.
Nobody notices the moment it goes wrong. It keeps running, keeps deciding, and
the first sign is a consequence rather than an error.

That makes the provider layer load-bearing in a way it is not for a chatbot.
Two failures in particular are silent by nature, and both happened here.

### An empty answer that looks like a successful one

A reasoning model can spend its entire output budget thinking and return an
empty string with `finish_reason: stop` — a perfectly successful HTTP 200 with
nothing in it. Upstream, that becomes an agent that answers nothing and reports
no error.

The guard — retry once with double the limit — originally lived inside the Poe
retry loop. But OpenRouter is reached from two other paths: the fallback, and
any role pinned directly to a non-Poe model. Both bypassed it. The failure mode
is the ugly part: when the Poe subscription runs out of credit, *every* request
takes exactly those bypassing paths. The guard was absent precisely when it was
needed.

[`test_fallback_empty.py`](tests/test_fallback_empty.py) pins the retry on both
paths.

### A picture that never arrives

`_strip_unsupported` exists to remove `cache_control`, an Anthropic extension
that breaks the request at other providers. It used to gather only text blocks
into the flattened result — so for every non-Claude model the image silently
vanished, and the model then reasoned confidently about something it had never
been shown.

The fix is not "always send the image": some models genuinely cannot see one.
It is to know which families can, and to tell the model in words when it is
being denied a picture that exists.

[`test_vision_strip.py`](tests/test_vision_strip.py) covers both halves.

## Routing

`provider.py` is where model names live. Everything else asks for a **role**:

```python
data = await chat('analyst', messages)
```

Twelve roles — `chat`, `tools`, `vision`, `memory`, `mail`, `analyst`, and six
subagents — each mapped to a model and each overridable from the environment
with `TITAN_MODEL_<ROLE>`. Swapping the model behind a role is a one-line change
in one file, and the reasoning for each current choice is written next to it,
including what to put back if it starts going wrong.

That last part matters more than it looks. A cheaper model on mail triage saves
real money right up until it misclassifies an invoice, and the comment above
that line says so.

## A note on language

Some strings in this repository are Russian. That is not an oversight and not a
missing translation.

TITAN has exactly one user, and that user is Russian-speaking. The sentences it
says when a provider fails, and the marker it hands a model that cannot see an
attached image, are the agent's voice — they are read by a person and by a
model, in the language that person and that conversation use. Logs, comments,
identifiers and documentation are English, because those are read by whoever
maintains it.

Translating the voice would not be a translation. It would be a different
product.

## Running it

```bash
python -m venv .venv && . .venv/bin/activate     # .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt
python -m pytest -q
```

Python 3.11 or newer. No API key, no network, no account: every test drives the
provider through injected responses.

## The system this comes from

Measured against the tree at the time of writing, not from memory:

| | |
|---|---|
| Python | 18,611 lines across 64 modules |
| Tools available to the model | 61, across 14 modules |
| Model roles | 12, each independently swappable |
| Scheduled jobs | 15, surviving restarts in a SQLite job store |
| Tests | 40 |
| Providers | Poe (primary), OpenRouter (fallback and embeddings) |
| Running since | December 2025, single host, unattended |

A ReAct loop with dynamic tool routing, persistent vector and graph memory,
proactive scheduled work, and approval gates on anything irreversible — behind
a FastAPI and Next.js PWA with WebSockets, voice and file handling.

## Documents

These ship with the project and are copied here unchanged.

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | the loop, the provider layer, memory, prompt caching |
| [DECISIONS.md](docs/DECISIONS.md) | why each choice was made, what it cost, and how to undo it |
| [TRUST_AND_SAFETY.md](docs/TRUST_AND_SAFETY.md) | the constraints that make unattended autonomy acceptable |
| [AGENT_DESIGN.md](docs/AGENT_DESIGN.md) | persona and capabilities as editable data, not a hardcoded prompt |

`TRUST_AND_SAFETY.md` is the one to read if you only read one. It is about an
agent with access to money, and it says which mechanisms are enforced and which
are only asked for.

## Licence

Published for evaluation and review. See [LICENSE](LICENSE).

---

The rest of this work, and how it is built: [moisejevs.com](https://moisejevs.com)
