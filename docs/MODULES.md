# Module Glossary

All 28 modules in plain English. Modules marked **core** are the safety
pipeline. Modules marked **experimental** are research components.

## Core: the safety pipeline

| Module | What it does |
|---|---|
| `integrate` | **Core.** The front door. One `DharmaOSKernel` object that runs every action through the full pipeline. This is what you import. |
| `ethics_engine` | **Core.** Scores each action against hard constraints. One red flag stops the action. No averaging. |
| `trust_domain_tagger` | **Core.** Tags every piece of context by origin. Only system instructions can authorize tool calls. Web content and emails are untrusted by default. The prompt-injection defense. |
| `lease` | **Core.** Short-lived permission leases. Revoking a parent revokes everything it handed out. The kill cascades. |
| `memory_validator` | **Core.** Scans every memory write for poisoning. Ungrounded writes are rejected. |
| `rta_ledger` | **Core.** Tamper-evident audit chain. Every decision is hash-chained to the one before it. |
| `rta` | **Core.** Named invariants checked at boot and periodically. Critical failure halts the system. |
| `adharma_detector` | **Core.** The 8-layer pre-execution pipeline: schema, freshness, vetoes, overlays, budget, abuse-screen, self-audit, reconciliation. |
| `ahamkara` | **Core.** Agent identity preservation. Detects persona drift and identity attacks via constitutional hash. |
| `constitution` | **Core.** Loads and validates the machine-readable constitution YAML. The declared self the agent is held to. |
| `world_state` | **Core.** Current-facts ledger with freshness TTLs. Blocks actions based on stale facts. |
| `samskara_ledger` | **Core.** Append-only action-consequence ledger. Tracks what happened and what followed. |
| `event_bus` | **Core.** Publish-subscribe bus connecting the modules. |
| `signal_filter` | **Core.** Classifies incoming signals: noise, threat, actionable, informational, ambiguous. |
| `canary_suite` | **Core.** Boot-time self-test. The agent refuses to start if safety checks fail. |
| `outcome_reconciler` | **Core.** Compares predicted vs observed outcomes. Measures calibration. |
| `metrics` | **Core.** Prometheus-style counters and histograms. Zero dependencies. |

## Experimental: research components

| Module | What it does |
|---|---|
| `consciousness` | **Experimental.** System vitals monitor (5-layer health metrics). |
| `iit` | **Experimental.** Approximate integration score for system coherence. Not production IIT. |
| `curiosity_popper` | **Experimental.** Two-agent belief-falsification circuit. Tests beliefs against counter-evidence. |
| `jnana_self_model` | **Experimental.** Capability self-model. Tracks what the agent believes it can and cannot do. |
| `guna_monitor` | **Experimental.** Operational-state classifier. Interlocks critical actions on system state. |
| `panca_prana` | **Experimental.** Decomposes system vitals into five sub-metrics. |
| `purusharthas` | **Experimental.** Four-goal coordinator for balancing competing objectives. |
| `sakshi` | **Experimental.** Immutable observation stack. Read-only witness layer. |
| `turiya_sidecar` | **Experimental.** Async observability sidecar. Watches but never writes back. |
| `dharma_yuddha` | **Experimental.** Proportional-defense gate for defensive actions against malicious targets. |

## Where to start

New to the codebase? Read in this order:

1. `integrate` — the API you will use
2. `ethics_engine` — how actions are judged
3. `trust_domain_tagger` — how injection is stopped
4. `lease` — how permissions work
5. `rta_ledger` — how everything is recorded
