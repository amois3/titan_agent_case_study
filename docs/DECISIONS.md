# Decisions

Every entry records what was decided, what it cost, what evidence supported it,
and how to undo it. Several of them reverse an earlier decision of mine; those
are the useful ones.

---

## ADR-1 — Roles, not model names

**Decision.** No module except `agent/provider.py` may name a model. Everything
else asks for a role: `chat`, `vision`, `memory`, `mail`, `engineer`, `scout`,
`trader`, and so on.

**Why.** Model names had been scattered across the codebase, so changing one
meant a hunt through call sites and a real chance of missing one. More
importantly, it made *evaluating* a model impossible: there was nowhere to
record why a choice had been made, so every reconsideration started from zero.

**Consequence.** The role map is now the decision log. Each entry carries the
measured trade-off and its rollback in a comment beside it. A model can be
swapped in one line, or from the environment with `TITAN_MODEL_<ROLE>` without
touching code at all.

---

## ADR-2 — Grok 4.1 Fast Reasoning for conversation and tools

**Decision.** The `chat` and `tools` roles run on `grok-4.1-fast-reasoning`
rather than Claude Opus.

**Evidence.** Measured against this system's actual request profile — roughly
twenty thousand tokens in against five hundred out — cost per request is about
22× lower than Opus at equivalent behaviour on the task. Tool calling and result
hand-back were verified with a live request before the switch rather than assumed
from documentation.

**Risk accepted.** Long tool chains under a reasoning model have not been
stress-tested. The failure mode to watch for is the agent abandoning a task
mid-chain or ignoring a tool result.

**Rollback.** Put `claude-opus-4.8` back in the role map, or set
`TITAN_MODEL_CHAT` in `.env`. One line, no restart of anything else.

---

## ADR-3 — A cheap model for everything unattended

**Decision.** The `memory`, `mail` and `analyst` roles run on
`gemini-2.5-flash-lite`.

**Why these three specifically.** They share a property: they run around the
clock and I never talk to the model directly. I read their output as finished
prose — a nightly digest, a mail classification, a daily summary. Voice and
personality are irrelevant; correctness and cost are not.

**Evidence.** Tested on the real jobs before adoption, not on benchmarks:
strict JSON for digests, an invoice with a deadline correctly classified as
URGENT, a marketing blast as SPAM. Twenty-six times cheaper than Sonnet on
input.

**The line I wrote next to it.** If mail triage starts getting things wrong,
restore `claude-sonnet-4.6` first — one missed invoice costs more than the
savings are worth. Cost optimisation has a ceiling, and it is set by the price
of the mistake, not by the price of the tokens.

---

## ADR-4 — The cached prefix must be byte-identical

**Decision.** The system prompt is split into an invariant prefix and a volatile
tail, with the cache marker on the boundary.

**What went wrong first.** The prompt opened with the current time. Prompt
caching requires an exact prefix match, so the hit rate was zero: every request
paid the write surcharge and none received the read discount. The feature was
live, was configured correctly by every visible measure, and inverted its own
economics — caching was strictly more expensive than not caching at all.

**Consequence.** Anything that varies now lives after the boundary. Capabilities
are pushed into the cached prefix even when a conversation does not need them,
because inside the cache they cost a tenth as much and their presence keeps the
prefix stable.

**Guard.** `tests/test_smoke.py` asserts the prefix is identical across six
unrelated messages, and that the time appears only in the volatile part.

---

## ADR-5 — A fallback provider, kept deliberately dumb

**Decision.** When the primary provider is unavailable, requests fail over to
`z-ai/glm-5.2` on OpenRouter.

**Why not something stronger.** The fallback exists so the agent does not go
silent, not so it stays clever. Making it a premium model would mean the
emergency path costs more than the normal one, which is exactly backwards.

**Deliberate limitation.** The fallback cannot see images. When a request
carrying a picture ends up there, the image is replaced with an explicit note
telling the model it cannot see the attachment and should ask for a description.
Silence would be worse: see ADR-7.

---

## ADR-6 — An empty answer does not announce itself

**Decision.** Detect exhausted-reasoning responses by inspecting content, not by
reading the finish reason.

**The problem.** On a reasoning model the thinking is billed against the same
output budget as the answer. When it runs out, the provider returns
`finish_reason: stop` — indistinguishable from a healthy completion — with empty
content and the thinking in a separate `reasoning_content` field. The original
check looked for `finish_reason == 'length'` and therefore never fired.

**Symptom in production.** Scheduled messages silently did not arrive. Nothing
logged an error, because as far as the code was concerned the model had answered
normally.

**Fix.** Treat a response as empty when there is no content and no tool call but
reasoning is present, then retry once with double the token budget.

---

## ADR-7 — A guard has to live on every path, not the main one

**Decision.** All OpenRouter calls go through a single wrapper,
`_openrouter_checked`, which applies the empty-answer guard from ADR-6.

**Why this is a separate entry.** The guard from ADR-6 was originally added
inside the Poe retry loop only. But OpenRouter is reached from two other places
— the fallback, and any role pinned directly to a non-Poe model — and both
bypassed it. The fallback model is itself a reasoning model, so the bug was
still fully present on precisely the path taken when the primary provider is
unavailable. The protection was absent exactly when it was needed.

**Consequence.** The direct call is no longer reachable from `call_model`, and a
test asserts that. Correctness that depends on remembering to repeat yourself is
not correctness.

---

## ADR-8 — Images must survive provider normalisation

**Decision.** `_strip_unsupported` removes the Anthropic `cache_control`
extension but preserves image blocks for every model that can see.

**What went wrong.** That function flattened structured content into plain text
for all non-Claude models, keeping text blocks and dropping everything else. An
attached image therefore disappeared silently for gpt-5, Gemini, Grok and the
fallback alike — including models that handle images perfectly well. The model
received a message referring to a picture, with no picture, and answered
confidently about something it had never seen.

**Fix.** Images are preserved for vision-capable families. For the ones that
cannot see, the image is replaced with an explicit marker so the model says it
cannot see the attachment instead of inventing its contents. A failure that is
announced is recoverable; a silent one is not.

---

## ADR-9 — A critical-path role does not depend on the scarcest model

**Decision.** The `vision` role runs on `gpt-5.4`.

**What the role actually does.** Vision requests in this system are frequent and
structurally simple: read a photograph or a screenshot and report what is in it.
That output then feeds a second step — a calculation, a lookup, a note written
to memory — and that second step is where the reasoning happens. Frontier
reasoning applied at the image-reading stage does not change what the next stage
receives.

**Availability, not price.** Under a shared provider quota, capacity is not
distributed evenly across models. The most expensive model in the pool is the
first to stop serving as the quota tightens, and it stops while cheaper models
are still answering normally — which makes its availability the least
predictable of anything on offer. Vision sits on the daily critical path: it is
the only route by which an image enters the system at all. Pairing the least
predictable model with the path that tolerates downtime worst is the wrong
combination irrespective of what either option costs.

**Why gpt-5.4 specifically.** It already backs the analytical roles, so a single
model both reads the image and computes over what it found, rather than handing
off between two. Verified on the real task before the switch: a 32×32 image
split into two colour bands, read back correctly, 34 tokens for the round trip.

**Prerequisite.** ADR-8 had to land first. Changing the model on its own would
have achieved nothing, because the image was being discarded during provider
normalisation before it reached any model.

---

## ADR-10 — Never trust a language model with arithmetic

**Decision.** When the trading subagent recommends changing a grid parameter,
the recommendation is recomputed in Python before it can be applied. Only
pre-calculated figures are trusted.

**Why.** The subagent is asked for judgement about market conditions, and it is
good at that. It is not reliably good at multiplying a spacing percentage by a
fee schedule, and a plausible-looking wrong number here moves real money.

**How it works.** Costs are computed in code up front — 0.2% fee, 0.1%
slippage, 0.05% spread — and the market analysis handed to the model already
contains the net-after-costs figure for each candidate. If the model comes back
recommending a value, that value is looked up against the pre-computed table
rather than taken on trust.

**Related.** Capital is hard-capped in code, and the client constructor raises
`SystemExit` rather than returning `None` if it cannot reach the exchange, so
systemd restarts the process. Silently running in a degraded fake mode is
forbidden — a trading bot that appears to work is more dangerous than one that
is visibly down.

---

## ADR-11 — Deterministic text where there is nothing to reason about

**Decision.** The morning briefing is assembled in code, not generated.

**Why.** The briefing reports facts that are already known: hours slept, body
battery, which clients are waiting, what today's focus is. Handing those to a
model adds a chance of distortion and buys nothing. A slot filled with known
work context has nothing to hallucinate with — so it is filled by code, and the
model is not invited.

---

## ADR-12 — Telegram retired in favour of a PWA

**Decision.** The PWA is the agent's permanent home; Telegram delivery is off by
default behind `TITAN_TELEGRAM_ENABLED`.

**Ordering that matters.** Notifications are delivered to the PWA *first*, then
to Telegram if enabled. Telegram is a network call that can time out, and when
it went first a Telegram failure took the notification with it.
