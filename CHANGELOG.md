# CHANGELOG

## v1.1.1 — 2026-09-12 — 4-node state guard (tooling and docs only)

- **`tools/gpu-state-probe.py` + `docs/4-NODE-STATE.md`** — a per-node memory-bandwidth probe
  that detects the hidden GB10 slow state (a reported-but-invisible failure mode where decode
  bandwidth drops ~3.3× while `nvidia-smi` clock, throttle reasons and device copies all look
  normal). This is the one degradation unique to a 4-node recipe: TP4 runs in lockstep, so a
  single slow Spark slows the whole fleet, and no config change can fix it. Measured 2026-09-12
  on all four nodes under live load: all fast (p50 ≈ 247 GB/s). Method credited to
  `tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark` issue #1 (MIT); the implementation is ours.
  No formula change.

## v1.1.0 — 2026-09-12 — codename *gamma-3* + durable fixes (first public release)

Formula unchanged from v1.0.0 (γ-3). This release adds three durability fixes and the first
public publication of the recipe.

**Durable fixes (all verified live on the running engine)**
- **In-image guard patch** — `patches/runtime/patch_encoding_dsv41.py` is applied at image
  build time. It replaces the upstream guard that *raised* on an image-placeholder token
  appearing in message text (upstream PR #15 class) with one that *sanitises* it: previously a
  transcript containing the placeholder re-entered the request, hit the guard, and produced a
  self-sustaining HTTP 500 loop. Idempotent, and it fails the build loudly if the base image
  drifts. Rollback tag `dsv41-4x-spark:pre-durablefix-20260912`.
- **Engine metrics on** — `--enable-metrics --enable-metrics-for-all-schedulers` in `.env.tp4`;
  `/metrics` now serves `sglang:*` series instead of nothing.
- **Mixed-chunk prefill on** — `--enable-mixed-chunk`, kept after A/B: prefills now share a step
  with in-flight decode (`#running-req: 1` on prefill lines), removing the interleave stall;
  post-change warm plateau 113.34 tok/s (previous best 108.99).

**Gates** — health · served name · 1M context · 0 garbled (5/5) · needle `PELICAN-1` exact
@~136k · γ-3 / verify window 4 unchanged.

**Tooling** — `tools/batchtest.py` (garbled gate) and `tools/prefill_distinct.py` (needle gate)
are now part of the repo and of the lane dir, and `verify.sh` runs all gates from the lane head
in one command.

**Publication** — first public release. Licensed AGPL-3.0-or-later (derived from an
AGPL-3.0-or-later launcher recipe); upstream attribution and the retained MIT notice for the
original skeleton are in `NOTICE.md`. Site identifiers replaced with placeholders; the
derivation is scripted in the private working repo.

## v1.0.0 — 2026-09-12 — codename *gamma-3* (first release; declared the BertholomusAI in-house recipe)

First frozen release of the in-house formula, declared production-viable on live hardware and
adopted as this stack's own recipe.

**Formula**
- `DSPARK_BLOCK_SIZE=3` (γ sweep 5→4→3; verify window 4). Acceptance: conc2 +7–10%,
  conc4 +6–10% vs γ-4, conc1 flat.
- `MAX_TOTAL_TOKENS=4000000` (from 750k) · `EP_SIZE=2` · `DSV41_CACHE_GIB=4` ·
  `DSV41_CACHE_WAYS=16`.
- `EXTRA_SGLANG_ARGS` += `--speculative-dspark-align-verify-tokens-to-graph-tier`
  `--min-free-slots-delay 1` `--enable-deepseek-v4-fp4-indexer`.
- `NCCL_HOST_DIR` emptied (image ships NCCL 2.28.3); `WORKER_ENGRAM_DIR` set for A4.
- Patch: per-node active IB-HCA discovery in `start.sh` (required for TP4 boot; PR #10).

**Results**
- Warm conc1/2/4 ≈ 35–38 / 59.5–61.4 / 78.8–79.4 vs stock 16.9 / 28.5 / 42.6 (~2.1×/2.1×/1.85×).
- Versus upstream `e59e6eb`'s own published table (same `Decode batch` counter): 1/2/3/4 streams
  **+22% / +22% / +32% / +31%** aggregate, measured while the lane was carrying fleet traffic.
- Capability delta vs the same commit: context 1,048,576 (4.1–5.2× their 200k–256k configured),
  KV pool 4,000,000 tok (5.33× their 750k), verify window 4 tok vs their 6, 29–32 GB/rank free
  vs their ~6 GB on the head.
- Gates green: 0 garbled; needle `PELICAN-1` exact @141,385 tok; context 1M/4M KV.

**Operations added**
- `warm-lane.py` post-boot warmer (plateau ~128 s) wired into `adopt-or-start-tp4.sh`.
- `keep-warm` timer/service: 5-min tick, runs the warmer only if idle ≥ 10 min.
- Watchdog unchanged (health timer) but documented: respects `maintenance.latch`.
- Documented both service traps (`start` is a no-op; `reset-failed` before restart).

**Known limits at release**
- Single-stream is not a clean win over the 69 tok/s TP3 demo: our flat engine median ≈ 46
  (under fleet load) with bursts 70.8–112.6. 69 is not reproducible as a flat median on a lane
  that is also serving four agents — see `docs/FLEET-BASELINE.md`.
- Idle decay: **RETRACTED**. The "~45–49 → ~37 within ~18 min idle" reading was contention,
  not decay (three labelled probes: 16.6 / 10.3 / 16.6 tok/s, 0/3 clean). Corrected in
  `docs/EVIDENCE.md` §5; re-measure only with `tools/bench-normalized.py`.
- SPS-profiler and cap-accept (budgeted verify) lines closed by measurement; upstream
  issue drafted (two DSpark bugs). Verify-window tuning is spent — γ-3 ≥ γ-4 under batch too.

**Backups / rollback chain present on the host**
`.pre-gamma3-*` (γ-4) · `.pre-gamma4-*` (γ-5 stock) · `.pre-pr7-*` (pre-tuning) ·
`.pre-capaccept2-*` · `.pre-spsrecord-*` · `.pre-cache12-*` · `.pre-attnmode-*` ·
`.pre-graphtier-*`; `adopt-or-start-tp4.sh.bak-pre-{casefix,warmpathfix}-*`.

<!-- Next entries: add at top. Keep the same shape: formula → results → ops → limits. -->