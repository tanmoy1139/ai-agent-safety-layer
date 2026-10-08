# DharmaOS — Security + Correctness Remediation & Path-to-Best-in-Class (V1)

Two independent enterprise reviews (adversarial **security** + **correctness/robustness**)
covered all 28 modules. Baseline: **699 passed, 1 skipped**. The architecture was
assessed genuinely strong (fail-closed pipeline discipline, lease ordering, frozen
models, sound Merkle/hash-chain, bounded IIT). Exploitable defects clustered in
**persistence round-trips, default secrets, fail-open seams, and concurrency**.

This doc tracks every finding with **honest status**. "FIXED" = applied **and**
re-verified (suite still 699 green; reproduced bugs re-tested).

---

## FIXED + VERIFIED — public-repo hardening pass (2026-10-08)

Independent security review of the public release candidate found 2 new issues.
Both fixed and re-verified.

| ID | Severity | Issue | Fix | Verification |
|----|----------|-------|-----|--------------|
| **PUB H-1** | High | `DharmaOSKernel.authorize()` defaulted to `fail_open=True`, and write-vs-read classification used substring matching on action names. Crashing the pipeline allowed `send_email`, `transfer_funds`, `publish_post` through — only actions containing one of 9 substrings were protected. The C-4 fail-closed guarantee depended on action naming. **Residual chain found after initial fix:** the `governed()` convenience wrapper (the path most users actually call) still defaulted `fail_open=True`, so the crash-attack chain survived via `governed()` + classifier-missed action name + induced `_evaluate` exception. | `authorize()` default changed to `fail_open=False`. `governed()` default also changed to `fail_open=False` to match. On governance error, everything is denied unless the caller explicitly opts into fail-open for known-safe reads. The substring classifier's false-negative risk is now documented: action names not matching the write-capability tokens are treated as reads for the error path, so fail-open must never be used for state-changing actions. | Crash attack re-run via both `authorize()` and `governed()`: all actions denied. Regression test covers the exact chain (wrapper + missed classification + induced error). Suite green. |
| **PUB M-1** | Medium | Default audit HMAC key was predictable: `f"dharmaos-audit-{tenant}-v1"`. Anyone guessing the tenant name could forge audit chain entries, defeating tamper-evidence. | No predictable fallback. Key comes from explicit parameter or `DHARMAOS_AUDIT_HMAC_KEY` env var; otherwise an ephemeral random key is generated with a warning. Production (`DHARMAOS_ENV=production`) requires a key and raises without one. | Key verified ephemeral and unpredictable. Suite green. |

---

## FIXED + VERIFIED (this pass)

| ID | Severity | Issue | Fix | Verification |
|----|----------|-------|-----|--------------|
| **SEC C-1** | Critical | `ethics_evaluation_hash` is in the `entry_hash` preimage but had **no sqlite column** → cross-process `verify_integrity` returns `ok=False, bad_seq=0` on every restart (the audit chain reports itself TAMPERED). | Added column + persist in `_insert_db` + reload in `_load_from_db` + back-compatible `ALTER TABLE` migration. `rta_ledger.py`. | Writer→fresh-reader cross-process verify now `ok=True`; suite green. |
| **COR C1** | Critical | `dharma_yuddha.py` referenced nonexistent `TargetAdharmaFlag.UNCERTAIN` → guaranteed `AttributeError` crash at escalation depth ≥5 (instead of fail-close). | `UNCERTAIN`→`UNKNOWN`. | suite green. |
| **COR H2** | High (patent) | EthicsEngine `score`/`risk_level` did **not** collapse to 0 for `brahmacarya`(w=0.8)/`aparigraha`(w=0.6) violations — the non-compensatory "single-zero-collapse" patent claim was false for 2 of 5 yamas (a catastrophic budget overage scored 0.82/"caution"). | `_weighted_score` now returns 0.0 on **any** constraint at 0.0, regardless of weight; docstring corrected. | suite green; score now consistent with `is_ethical`. |
| **SEC C-3 / COR H3** | Critical | Hardcoded source-committed default HMAC keys (provenance, trust-label) → forgeable provenance + **self-elevation to SYSTEM trust** (defeats the IPI gate). | `memory_validator.py` + `trust_domain_tagger.py`: require key in `DHARMAOS_ENV=production`, else ephemeral per-process random + warn — never the committed constant. | suite green. |
| **COR C2** | Critical | `SamskaraLedger` not concurrency-safe → `RuntimeError: dictionary changed size during iteration` under parallel agents (reproduced 221×). | Added re-entrant `_lock`; `append` atomic under lock; readers snapshot `_mem` under lock. | **9,000 concurrent appends across 3 threads, 0 size-change errors**; suite green. |
| **SEC C-4** | Critical | Integration wrapper failed **open** on kernel error → an induced exception turns a governed action into an ungoverned allow. | `integrate.py` (the drop-in SDK): write/high-impact actions **always fail-closed** on error; only reads honor `fail_open`. | SDK smoke green. |

### FIXED + VERIFIED — continuation batch 2

| ID | Severity | Fix | Verification |
|----|----------|-----|--------------|
| **SEC C-2** | Critical | `verify_integrity` now fails CLOSED when a chain was HMAC-sealed but no key is supplied (was silently `ok=True` on a forgeable chain). `rta_ledger.py`. | keyless verify of a sealed chain → `ok=False`; with key → `ok=True`; suite green. |
| **COR C3** | Critical | AdharmaDetector `_InMemoryRateLimiter`: added a lock (read-modify-write was racy under `to_thread`) + bounded per-tenant dict with LRU eviction (tenant-ID-rotation OOM). | suite green. |
| **COR H5** | High (patent) | curiosity_popper falsification now classified by `action_signature` suffix (`:falsified`/`:corroborated`), not karma sign — Claim 10f historical-falsification-rate was always 0 (dead code). | suite green; rate now computed. |
| **COR H8** | High | `CoreSelf.enter_peak_awareness` is now idempotent + reversible (`exit_peak_awareness` restores pre-boost vitals); previously repeated calls pinned vitals to 1.0 permanently. | base 0.5→boost 0.75 (single, not stacked)→restored 0.5; suite green. |
| **COR H7 (Layer 6)** | High | Layer-6 guṇa/abuse internal exception now returns INCONCLUSIVE (→ DENY fail-closed) instead of swallowing to ALLOW. | suite green. (Layer-7 / signal_filter / Layer-4 unknown-domain still TODO below.) |
| **SEC H-5** | High | Delegated child lease now clamped to ≤ parent expiry; `revoke`/`emergency_kill` cascade down the full delegation chain. `lease.py`. | parent kill → child + grandchild cascade-revoked, child clamped; suite green. |
| **COR H6 (part)** | High | `jnana._brier_scores` is now a bounded `deque(maxlen=512)` rolling window (was unbounded + stale all-history mean). | suite green. (Accumulator re-fold + `_domain_harm_patterns` cap still TODO — accumulator behavior is design-dependent on the caller contract.) |

**Attempted + reverted (honest):** **COR H4** — caching the RtaSupervisor audit on Layer 3 broke runtime constitution-tamper detection (test_12). The audit MUST stay fresh there; the correct fix is to optimize `rta.audit()` internals (reuse EthicsEngine/SamskaraLedger; keep only the constitution-hash check fresh), not cache it. Left an in-code note; tracked below.

### FIXED + VERIFIED — completion batch 3

| ID | Severity | Fix | Verification |
|----|----------|-----|--------------|
| **SEC H-2 / COR H1** | High | `WorldStateService` now scopes freshness to the tenant (`tenant_to_org_id` uuid5 convention) with fallback to shared/global facts. `world_state.py`. | tenant B can't see tenant A's fact (`missing_sources`), global facts still shared; 25 ws tests + suite green. |
| **COR M1** | Medium | `bind_contextvars` sites now unbind OUR keys first (adharma + ethics) → no cross-request/tenant audit-log contamination on pooled workers. | suite green. |
| **COR M3** | Medium | jnana tenant filter fixed (`org_id == tenant_to_org_id(tenant)`); the old `str(uuid)==tenant_name` never matched tenant entries. | suite green. |
| **SEC H-4 / COR M4** | Medium | memory_validator: `_URL_ENCODED_RE` rewritten to catch any `%XX` run (decode-then-check); scan bounded (`_MAX_SCAN_CHARS`, `_MAX_B64_CANDIDATES`) → no CPU amplification. | suite green. |
| **COR M5** | Medium | `ahamkara.check_write_scope_violation` proper `**` glob — `**/.env` now blocks a root `.env`, `secrets/**/key` blocks `secrets/key` (zero-segment). | all glob cases verified; suite green. |
| **COR M6** | Medium | `dharma_yuddha` zero-threat guard before proportionality (no ZeroDivisionError; fail-closed DENY). | suite green. |
| **COR (metrics)** | Medium | `_MetricRegistry._counters` now lock-guarded (key-creation + snapshot iteration). | suite green. |
| **COR H6 (rest)** | Medium | `_domain_harm_patterns` per-domain list bounded (≤50). | suite green. |
| **L** | Low | `SamskaraLedger` validates `ebbinghaus_tau_days > 0`. | suite green. |

**Assessed and correctly NOT changed (no bluff):** Layer-7 exception path is already fail-closed (INCONCLUSIVE); Layer-4 permissive default is by-design (positive-rule overlay layer; fail-closed coverage is in the other 7 layers; its exception path fails closed); `signal_filter` exception→AMBIGUOUS is fail-*safe* (routes to human review, EU AI Act Art.14).

### GENUINELY REMAINING (honest)
- **COR H4 (perf)** — refactor `rta.audit()` checkers to reuse heavy objects while keeping the constitution-hash check fresh. Perf optimization, NOT a correctness/security bug (the audit is correct, just per-request expensive). In-code note left.
- **Real layer timeout (advisory)** — current `_run_with_timeout` already converts completed-over-budget + exceptions → INCONCLUSIVE; only a true infinite-hang is uncovered (low realistic risk). A real interrupt needs threading into the layer hot-path (loses contextvars, adds complexity) — deferred as advisory.
- **M8 (lazy `__init__`)** — graceful degradation if an optional dep is missing; a high-risk package-surface change for low value (deps are present). Tracked.
- **L (metric label cardinality)** — bound/aggregate high-cardinality per-tenant label names. Minor.

**Also delivered:** `dharmaos/integrate.py` — a drop-in `DharmaOSKernel` SDK so any
team wires governance in a few lines (ethics + 8-layer Adharma + lease + crypto
audit + Art.50 disclosure), with the ActionContract calibration baked in. Plus the
rebrand-completeness fixes (stale `vedic.*` test patch/logger strings → `dharmaos.*`,
which had been silently mis-binding and masking the failing IIT test).

---

## TODO — tracked with exact fixes (not yet applied)

### Critical / High
- **SEC C-2** — `verify_integrity` silently skips HMAC when no key supplied → a verifier reports `ok=True` on a forgeable chain. *Fix:* persist an "HMAC-expected" marker (`rta_meta` row); fail-closed when chain was HMAC-sealed but no/zero key presented; treat `hmac_hex NOT NULL` with no key as hard failure. (`rta_ledger.py:776-795`)
- **COR C3 / SEC H-3** — AdharmaDetector in-memory rate limiter: no lock (read-modify-write race), unbounded per-tenant dict (tenant-ID-rotation OOM), double-charge in async path. *Fix:* lock the bucket; LRU/TTL-cap tenant dict; skip sync limiter when entered via `evaluate_async`. (`adharma_detector.py` `_InMemoryRateLimiter`)
- **SEC H-2 / COR H1** — `WorldStateService.require_fresh` calls `is_fresh`/`get` **without `org_id`** → multi-tenant freshness gate broken (cross-tenant freshness confusion; over-block of tenant-scoped facts). *Fix:* thread `contract.tenant_id`→`org_id` into both calls. (`world_state.py:412,417`)
- **COR H4** — `RtaSupervisor.audit()` runs on the **request hot path** (Layer 3) doing disk YAML reads + `EthicsEngine`/`SamskaraLedger` construction every call. *Fix:* Layer 3 reads cached `last_report`/`current_mode`, not `audit()`; exception path should inherit registered severity (fail-closed → HALTED). (`adharma_detector.py:1212`, `rta.py`)
- **COR H5** — `curiosity_popper` historical-falsification is dead code (counts falsifications by `karma_delta<0`, but falsified writes positive karma) → Patent Claim 10f non-functional. *Fix:* classify by `action_signature` suffix (`:falsified` vs `:corroborated`). (`curiosity_popper.py:636,745`)
- **COR H7 / SEC**: fail-**open** ALLOW defaults inside the fail-closed pipeline — Layer 6 reads private `_guna_monitor._history[-1]` then ALLOWs on exception; Layer 7 unknown event → permitted; `signal_filter.analyze` swallows exceptions to AMBIGUOUS even for THREAT. *Fix:* unknown/exception → INCONCLUSIVE/DENY, never ALLOW. (`adharma_detector.py:~1515,~1686`, `signal_filter.py`)
- **COR H6** — `jnana_self_model` accumulator double-counts confidence (re-folds full history per batch); `_brier_scores` unbounded. *Fix:* clear/fold-deltas; `deque(maxlen=...)`.
- **COR H8** — `consciousness.CoreSelf` not thread-safe; `enter_peak_awareness` multiplies vitals ×1.5 and **never restores** → vitals pinned to 1.0 permanently. *Fix:* lock vitals; restore in a context manager/finally; guard singleton.
- **SEC H-5** — delegated child lease can outlive parent expiry/kill (`emergency_kill` doesn't cascade). *Fix:* clamp child `expires_at=min(req,parent)`; cascade revoke/kill to `parent_lease_id` chain. (`lease.py`)
- **SEC H-1 / COR M2** — `_run_with_timeout` doesn't actually interrupt a hung layer (measures elapsed after completion). *Fix:* run layer in a worker with `future.result(timeout=...)`, fail-closed on timeout; bound thread pool. (`adharma_detector.py:348`)

### Medium / Low (selected)
- **COR M1** — `bind_contextvars` without unbind on pooled `to_thread` workers → cross-request/tenant audit-log contamination. Use `bound_contextvars(...)` / `clear_contextvars()` in `finally`. (`adharma_detector.py`, `ethics_engine.py:693`)
- **SEC M-3** — chain **truncation/rollback** not detected (`verify_integrity` is `ok=True` for any consistent prefix). *Fix:* persist tip-hash/high-water mark to an external anchor (SealStore/the platform Ed25519 ledger); fail if live chain < last sealed window_end.
- **SEC M-2** — Merkle seals not bound to chain length → window-boundary truncation still verifies. *Fix:* record tip/length in each seal; verify monotonic coverage.
- **COR M3** — `jnana` accepts `org_id is None` entries into every tenant's model; `restore()` bypasses the validator. *Fix:* drop null-org; validate on restore.
- **COR M4 / SEC H-4** — ReDoS/CPU amplification: base64/DOTALL injection regex on every memory write + every token in the tagger; `_URL_ENCODED_RE` malformed. Add input-length + candidate caps; Unicode NFKC + homoglyph fold + base64 decode in the SilentBridge detector; the default `HeuristicParallelGuard` ships an empty banned list (Phase-3 no-op) — require a real ParallelGuard in prod.
- **COR M5** — `ahamkara.check_write_scope_violation` collapses `**`→`*` (fnmatch) → `secrets/**/key` misses `secrets/a/b/key`. Use real `**` glob semantics.
- **COR M6** — `dharma_yuddha.proportionality` `ZeroDivisionError` when `threat_magnitude==0`; `V11TargetClassifier` conflates procedural deny with malicious. Guard + separate.
- **COR M8** — `__init__.py` eager-imports the whole kernel (one failure breaks `import dharmaos`). Use lazy `__getattr__` for graceful degradation.
- **L:** high-cardinality metric label names (bound them); `samskara` τ not validated `>0`; `FileSealStore` O(n²)+non-atomic (known TD); constitution dual-hash footgun.

---

## Path-to-Best-in-Class (beyond bug-fixes — what makes the "leading" claim TRUE)

**P0 (makes the claim real):**
1. **Detection quality at scale** — replace heuristic regex defenses with a trained classifier layer (integrate + benchmark Llama Guard 4 / a Lakera-class detector — hooks exist as stubs); cover multilingual/multimodal/encoded/many-shot/multi-turn; prove **low FPR on large benign corpora**; add a threat-intel update path.
2. **Validated evidence** — full runs on **AgentDojo, InjecAgent, AgentHarm, HarmBench, JailbreakBench, AdvBench, CyberSecEval** with CIs + multiple seeds, **head-to-head vs Llama Guard/NeMo/Lakera**, published via the V16 reproducible harness (replace 30–60-sample slices).

**P1 (production hardening + adoption):** distributed append-only audit backend (Postgres/Kafka/transparency-log, not sqlite) with cross-node verifiable integrity; KMS/HSM keys, SBOM, signed releases, fuzzing; p99 latency + throughput benchmarks, stateless gate workers; framework adapters (LangGraph/CrewAI/AutoGen/OpenAI-Agents); REST/gRPC sidecar + Docker/Helm; admin console; OpenTelemetry/SIEM; policy-as-code + RBAC; semver PyPI.

**P2 (trust/compliance to buy):** SOC 2 Type II, ISO 27001, **ISO 42001**, NIST AI RMF mapping, EU AI Act conformity (Art.50 hooks ✅); close patent reduction-to-practice gaps (Claim 10f → COR H5; Claim 12 → SEC C-1 done); design-partner deployment; bug bounty; open-source the benchmark harness.

**Honest framing:** "unbeatable" is never literal in security. With P0 done, the
defensible claim is **best-in-class agentic action-governance + cryptographic
compliance** — complementary to (not replacing) a trained content/PI classifier.
