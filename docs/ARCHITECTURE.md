# Architecture

TITAN is a single Python process with a scheduler attached, talking to language
models through one narrow interface and to the world through 61 tools. There is
no framework underneath it. Everything below is a deliberate choice rather than
a default inherited from a library.

## The loop

`agent/llm.py` runs a ReAct loop: the model receives the conversation plus a
tool schema, either answers or requests a tool call, and the result of that call
is appended to the conversation and handed back. The loop continues until the
model produces an answer, hits a step limit, or a tool demands human approval.

Three things in that loop are worth pointing out.

**Approval suspends the loop rather than aborting it.** When a tool returns the
sentinel `NEEDS_APPROVAL`, the loop does not fail and does not silently skip the
call. It mints an action id, persists the *entire* message list including the
pending tool call, and returns a `needs_approval` status with a preview of the
call and its arguments. Once the human approves, execution resumes from exactly
that state. The agent's reasoning is not restarted and not lost.

**Tool results are classified, not just concatenated.** Every result is passed
through a classifier before it re-enters the conversation, so an error, an empty
result and a successful one are distinguishable to the loop itself rather than
only to the model.

**Empty answers are detected by substance, not status code.** See
[DECISIONS.md](DECISIONS.md#adr-6--an-empty-answer-does-not-announce-itself).

## The provider layer

`agent/provider.py` is the only place in the codebase that knows a model name
exists. Everything else asks for a *role*:

```python
data = await provider.chat('engineer', messages, tools=tools)
```

Twelve roles are defined — `chat`, `tools`, `vision`, `memory`, `mail`,
`analyst`, and six subagent roles. Each maps to a model, and each mapping can be
overridden from the environment with `TITAN_MODEL_<ROLE>` without touching code.

The benefit is not abstraction for its own sake. It means a model can be
replaced in one line after being tested on its actual job, and that the reason
for the choice can live next to the mapping as a comment rather than being
scattered across call sites. The role map in `provider.py` reads as a decision
log, with the measured trade-off and the rollback written beside each entry.

Poe is the primary provider. OpenRouter is the fallback and the sole source of
embeddings, because Poe has no embedding endpoint. A model name containing a
slash is routed to OpenRouter, one without to Poe — which is what lets older
call sites passing `openai/text-embedding-3-small` keep working untouched.

## Prompt caching

The system prompt is deliberately split in two:

```
build_cached_prefix()     → invariant, byte-identical for every message
build_volatile_context()  → time, current topic, related threads
```

Anthropic-style caching only pays off when the cached prefix matches exactly.
The prefix therefore contains the persona, the operating rules and the full
capability list — and nothing that changes. Capabilities are included even when
a conversation does not need them: inside the cache they cost a tenth of the
price, and their presence keeps the prefix stable, which is worth more.

For Anthropic models the system message is emitted as two blocks with a
`cache_control` marker on the prefix boundary. For every other provider the
structure collapses to a plain string, because that marker is an Anthropic
extension and arrives elsewhere as garbage that takes the whole request down.

A test asserts the prefix is identical across six unrelated messages. It exists
because the timestamp once sat first in the prompt, which held the cache hit rate
at zero: every request paid the write surcharge and none received the read
discount.

## Tool descriptions are prompt, not documentation

This deserves its own section because it is easy to get wrong.

The tool schema is assembled at runtime from the Python functions themselves:

```python
"description": (func.__doc__ or "").strip()[:512]
```

A registered tool's docstring is therefore not documentation — it is the text
the model reads when deciding whether to call that tool. `take_webcam_photo`
does not say "captures an image from the webcam"; it says, in the operator's
language, that this tool must always be called for any request to take a photo
and that answering in text instead is forbidden. That phrasing exists because
the model used to describe a photo rather than take one.

Two consequences follow. Tool docstrings are versioned as part of the prompt and
reviewed as prompt changes. And they stay in the agent's operating language,
while the rest of the codebase is English — a translation would be a behavioural
change, not a cosmetic one.

## Memory

Memory is layered by access frequency rather than by importance, and nothing is
ever deleted by heuristic.

**Hot layer** — `user_facts.json`: personal facts, preferences, active goals,
recent decisions and session summaries. Loaded into context on demand through
the `get_user_facts` tool.

**Current state** — a small, separate document describing what is being worked
on right now. Kept apart from long-term facts because "what I am doing today" and
"what is true about me" decay at completely different rates.

**Cold archive** — older sessions and closed goals, moved out of the hot layer
once it grows past a size threshold and indexed into sqlite-vec with 768-dimension
embeddings. Retrieved semantically, with a keyword search over the raw JSON as a
fallback when the vector path is unavailable.

The compression job never discards: it moves. A misclassification should cost a
retrieval, never a fact.

## Scheduling

APScheduler with a SQLAlchemy job store, so the fifteen jobs survive process
restarts. They fall into three groups: monitors (mail, Garmin, grid bot,
client lifecycle), scheduled communication (morning briefing, two heartbeats,
sleep follow-up), and hygiene (memory compression, session flush, manifest
refresh, runtime maintenance, proposal hygiene).

Jobs that must not be skipped are registered with `misfire_grace_time=None`, so
a job is delivered however long the process was down, with `coalesce` preventing
a burst of duplicates on restart.

## Subagents

`delegate_to_subagent` hands a task to a specialised role with its own model and
its own system instruction. Roles that need tools — `engineer`, `scout` — run
their own tool loop; text-only roles such as `scribe` and `trader` answer in one
pass. The model is selected per role rather than shared: an engineer needs
strength at code, a scout needs web search and a long context. Both were
originally pinned to a single model, which quietly left the scout role useless.

## Layout

```
agent/
  llm.py            ReAct loop, prompt assembly, subagent dispatch
  provider.py       model names live here (one literal remains in llm.py: the fallback)
  scheduler.py      15 recurring jobs
  mail_monitor.py   IMAP triage
  heartbeat.py      twice-daily unattended check-ins
  vector_store.py   sqlite-vec semantic archive
  tools/            61 tools across 14 modules
services/
  gridbot/          Binance grid trading bot
  system/           systemd units
tests/              40 tests
```
