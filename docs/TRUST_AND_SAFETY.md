# Trust and safety

This agent can run shell commands, rewrite its own source, read and send files,
control a phone, operate a camera and a microphone, and supervise a bot that
trades real money. That is a large amount of authority to hand to a language
model. The answer to "why is that acceptable" is not that the model is good.
It is that the model's authority is bounded by code it cannot reach.

The design principle throughout: **the model proposes, the code disposes.** Every
irreversible action passes through a gate the model does not control, and every
reversible one leaves a trail that makes undoing it a single call.

---

## 1. Three mechanisms, applied by reach

Tools are not constrained identically, because they do not reach the same
distance. Three mechanisms are in use, and which one applies is a judgement
about blast radius rather than a blanket rule.

**Unconditional approval.** Some tools never execute on their own. They return
the sentinel string `NEEDS_APPROVAL` *instead of* doing anything: `restart_bot`,
`take_webcam_photo`, starting or stopping a remote agent session, and any write
outside the safe zone.

The loop intercepts that sentinel, mints an action id, persists the entire
conversation state including the pending tool call, and returns to the human a
preview of the exact call and its arguments. Nothing has happened yet. On
approval, execution resumes from precisely that saved state — the agent's
reasoning is neither restarted nor discarded.

This matters more than a confirmation dialog would. A dialog that aborts the
agent's train of thought creates pressure to approve things quickly to avoid
losing work. Suspend-and-resume removes that pressure, which means approval
stays a real decision rather than a reflex.

**Approval triggered by a denylist.** `execute_command` runs a shell command
directly unless it matches one of the entries in `DANGEROUS_COMMANDS` on a word
boundary, in which case it goes to approval instead. The list is in the next
section.

**A sandbox in place of approval.** `execute_python` is not gated. It runs in a
separate process with a thirty-second timeout, a working directory inside the
system temporary directory, a restricted environment, and a pre-flight check
that rejects code importing `os`, `sys`, `subprocess`, `socket`, `urllib` and
others. The trade-off is deliberate: computation is the single most frequent
thing the agent does, and routing every calculation through a human would empty
the approval prompt of meaning through sheer volume. An approval that is granted
fifty times a day is not a control.

**Neither.** A small number of tools execute directly. Their arguments are
handled defensively — metacharacters are rejected, which stops command
injection — but no approval is requested. This is the weakest part of the model
and is stated as such at the end of this document. Which tools they are is
deliberately not listed in the published copy; the internal document names
them.

## 2. Commands that always require a human

```python
DANGEROUS_COMMANDS = [
    'rm', 'sudo', 'mkfs', 'reboot', 'shutdown', 'mv', 'chown', 'chmod',
    'mkdir', 'rmdir', 'cp', 'wget', 'curl', 'apt', 'pip'
]
```

The list is deliberately over-broad. `mkdir` and `cp` are not dangerous in
themselves; they are on the list because the cost of an unnecessary approval
prompt is a few seconds, and the cost of a missing one is unbounded. Asymmetric
consequences deserve asymmetric caution.

## 3. The core cannot be overwritten

```python
_CORE_FILES = {
    'llm.py', 'handlers.py', 'db_helpers.py', 'main.py',
    'config.py', 'database.py', 'scheduler.py',
}
```

These files can never be written wholesale — only edited surgically through
`edit_file`, which replaces a specific fragment. The reasoning is direct: a
wholesale rewrite of `llm.py` by the agent could remove this very restriction,
and a safety property that the system can disable is not a safety property.

Tool modules under `agent/tools/` are explicitly *not* core. They can be
rewritten freely, because that is where the agent's capabilities live and
constraining them would defeat the point of self-modification.

## 4. Filesystem zoning

Writes are classified by location, and the classification is done before any
content is touched:

1. A core file inside the project → refused outright.
2. Outside the project and outside the safe zone → approval required.
3. Inside the project but outside the safe zone → approval required.
4. Inside the safe zone → proceeds.

## 5. Every self-modification is reversible

Before the agent edits its own source, two things happen automatically:

- **A timestamped backup** of the original file is written to `backups/auto/`.
- **A git commit** is created describing the change and its stated reason.

The result is that self-modification history is ordinary version-control
history. The `git_rollback` tool reverts a change by a single call, and
`git_log` lets the agent — or the human — see what it did to itself and when.

The agent is not trusted to get every edit right. It is required to make every
edit undoable, which is a much weaker and much more achievable property.

## 6. Money

The trading bot is the sharpest case, and it is constrained in three
independent ways.

**Capital is capped in code.** Not in configuration the model can edit, and not
in a prompt — as a constant in the trading module.

**Arithmetic is not delegated.** The subagent is consulted for judgement about
market conditions, which is what a language model is good at. Every
number it produces is re-derived in Python before it can affect anything. Costs
— 0.2% fee, 0.1% slippage, 0.05% spread — are computed up front, and a
recommended parameter is checked against that pre-computed table rather than
believed. See [ADR-10](DECISIONS.md#adr-10--never-trust-a-language-model-with-arithmetic).

**Degraded operation is forbidden.** If the exchange client cannot be
constructed after retries, the process raises `SystemExit` so systemd restarts
it. It never returns `None` and never quietly continues in a simulated mode. A
trading bot that looks alive while doing nothing real is more dangerous than one
that is visibly down, because only the second one gets noticed.

## 7. Secrets

No credential is present in this repository. Every secret is read from the
environment at runtime; `config.py` contains no literal values and
`.env.example` ships with empty strings. Runtime tokens — Garmin OAuth, the
Upwork session — live outside the project tree entirely, so that no accident
inside the working directory can sweep them into version control.

The agent's memory, personal facts and client material live in `data/`, which is
excluded from this repository entirely.

---

## What this does not protect against

Stating the gaps is part of the argument; a safety section that lists only
strengths is not describing a real system.

- **An approved action is unbounded.** The gate ensures a human sees the exact
  command before it runs. It does not evaluate whether that command is wise. The
  quality of the last line of defence is the quality of the person reading it.
- **Read access is broad.** The constraints above govern writes and execution.
  The agent can read widely across the host, and a prompt-injected instruction
  arriving through mail or a web page could in principle direct it to read
  something and repeat it back. Mail is classified by a model, and that model
  reads attacker-controlled text.
- **Single operator, single host.** There is no privilege separation between the
  agent and the user account it runs as. Anything that account can do, the agent
  can be made to do given an approval.
- **The `DANGEROUS_COMMANDS` list is a denylist.** Denylists are structurally
  incomplete. It catches the obvious cases; it would not catch a novel one.
- **Some tools bypass the denylist.** A few paths do not route through
  `execute_command` and therefore never reach the approval step. Argument
  filtering stops injection, but it does nothing about what the call itself is
  allowed to do. One of them amounts to arbitrary code execution by a slower
  route; another is a privacy exposure rather than an integrity one, which is
  why it is accepted rather than gated.
- **Which ones they are is not published here.** This copy is public and names a
  real machine. The internal version of this document lists them by name, and I
  will walk through it in a conversation. The gaps are stated because the count
  and the shape of them are the honest part; the map is not.
