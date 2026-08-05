# Agent design

How the agent's identity, capabilities and memory are defined — and why almost
none of it lives in the source code.

## Personality is data, not code

The agent's character and its description of what it can do are held in two
editable documents that the running system reads at startup:

```
data/SOUL.md          who the agent is: tone, standards, how it addresses its user
data/CAPABILITIES.md  what it can do, in the words the model itself will read
```

Neither is in this repository — they contain personal material — but the design
matters more than the contents.

**Why files rather than a string in `llm.py`.** Changing how the agent behaves
should not be a code change. A code change means an edit, a review, a restart
and a diff in version control against a module full of unrelated logic. Behaviour
gets tuned constantly — a tone that grates, a rule that turned out too strict, a
capability the model keeps forgetting it has. Making that a data edit means it
can be done in seconds and reverted just as fast.

**Why the agent can edit them.** These files sit in the safe zone, so the agent
can revise its own description of itself without approval. That sounds alarming
and is not: `SOUL.md` and `CAPABILITIES.md` are prompt inputs, not constraints.
Nothing in them can grant an ability, because abilities are the registered tools,
and the gates around those tools are in code the agent cannot rewrite wholesale.
An agent that talks itself into believing it may do something still meets the
same approval gate.

That separation is the point. **Identity is soft and editable; authority is hard
and is not.**

## How a prompt is assembled

```
build_cached_prefix()        ← SOUL + CAPABILITIES + operating rules
--- cache boundary ---
build_volatile_context()     ← current time, active topic, related threads
--- conversation history ---
--- 61 tool schemas ---
```

The prefix is byte-identical for every request, which is what makes caching pay
— see [ADR-4](DECISIONS.md#adr-4--the-cached-prefix-must-be-byte-identical).
Everything that varies comes after it.

Tool schemas are assembled from the Python functions themselves, which makes a
tool's docstring part of the prompt rather than part of the documentation. That
consequence is covered in
[ARCHITECTURE.md](ARCHITECTURE.md#tool-descriptions-are-prompt-not-documentation).

## Memory tiers, and what a tier actually is

Memory is organised in layers, but a layer is **a priority when assembling the
prompt, not a bucket in storage**. That distinction is the most consequential
decision in the memory system, and it rests on the asymmetry between the two
ways of being wrong.

The tempting design is to classify each fact by importance and archive or delete
the unimportant ones. It fails for a simple reason: the classifier is a language
model, it will misjudge, and under that design a misjudgement is irreversible.
A fact wrongly rated trivial is gone.

Under the design actually used, nothing is deleted and nothing is archived by
heuristic. Cold material moves to a semantically searchable archive when the hot
layer outgrows a size threshold — moves, not discards. The cost of a
misclassification is therefore that a fact takes a retrieval to find instead of
being present immediately. That is a bad afternoon, not a permanent loss.

The rule generalises past this system: **when a classifier will be wrong, make
being wrong cheap rather than trying to make it rare.**

## What gets remembered, and what deliberately does not

The memory tool's own instruction to the model draws the line by consequence:

> Save automatically, without waiting to be asked: facts with consequences for
> future decisions — biography, family, business, standing working rules, goals,
> important decisions, agreements.
>
> Do not save: one-off practical questions and passing curiosity; your own
> conclusions about the user's character drawn from a single remark; technical
> system state; things discussed without a decision and without a request to
> remember.
>
> The test: will this matter in a month? If not, do not save it.

The middle prohibition is the one that took experience to write. An agent that
records inferences about its user's personality from single remarks accumulates
a distorted picture quickly, and then reasons from it confidently. Facts are
recoverable from context; conclusions calcify.

## Current state, kept separate

What the user is working on right now lives in its own small document, apart
from long-term facts, and is described to the model as the source of truth about
active work — explicitly instructing it not to invent tasks from memory.

The separation exists because the two decay at completely different rates. "Runs
a nutrition project" is true for years. "Is debugging the mail monitor today" is
false by Thursday. Merging them produces an agent that confidently brings up
last month's task as though it were live, which is worse than an agent that
knows nothing about today.
