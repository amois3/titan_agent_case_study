# About these documents

These four ship with TITAN and are copied here **unchanged**. They describe the
whole system, not the six modules extracted into this repository, so they refer
to code that is not here — the ReAct loop in `llm.py`, the scheduler, the mail
and Garmin monitors, the tool layer.

That is deliberate. Editing them to match a smaller repository would produce
documents written *for* this repository, which is a different and less useful
thing to read: what is worth seeing is what the project documents for itself.

| | |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | how the pieces fit: the loop, the provider layer, memory, prompt caching |
| [DECISIONS.md](DECISIONS.md) | why each choice was made, the trade-off it accepted, and how to undo it |
| [TRUST_AND_SAFETY.md](TRUST_AND_SAFETY.md) | the constraints that make unattended autonomy acceptable |
| [AGENT_DESIGN.md](AGENT_DESIGN.md) | persona and capabilities as editable data, not a hardcoded prompt |

The modules in [`../agent`](../agent) are the provider layer and the memory
core, so the provider section of `ARCHITECTURE.md` and the model-choice entries
in `DECISIONS.md` are the parts that describe the code you can actually run
here.
