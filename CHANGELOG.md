# CHANGELOG

## v1.1.5 — 2026-09-13 — comm line closed by source audit; env-file provenance fixed; prefill-chunk window staged

**No live-lane change in this release.** Everything below is documentation, an env-file
correction that makes the published recipe match what production already runs, and a staged
window. Zero boots spent, zero service restarts.

**Formula file change — `env.tp4` (reproducibility fix, not a behaviour change)**
- `EXTRA_SGLANG_ARGS` now carries the three durable-fix flags production has been running since
  2026-09-12: `--enable-metrics --enable-metrics-for-all-schedulers --enable-mixed-chunk`.
- The four DSpark instrumentation/experiment variables production runs are now published:
  `SGLANG_DSPARK_FOLDED_PROPOSAL=1`, `SGLANG_DSPARK_BLOCK_ACCEPT_ONLINE_INTERVAL=60`,
  `SGLANG_DSPARK_ENABLE_SPS_RECORD=0`, `SGLANG_SIMULATE_ACC_LEN=-1`.
- **Why this matters:** the v1.1.4 release proof covered the *patch* surface (applying
  `patches/000NN` to a clean checkout and diffing `start.sh`/`Dockerfile` against production).
  It never compared the environment file — and `env.tp4` was under-describing production. Anyone
  building from v1.1.4 would have got a lane with no engine metrics and without mixed-chunked
  prefill, i.e. not the lane every number in `docs/EVIDENCE.md` was measured on.
- **No production edit was needed:** production already runs these values. Rollback is not
  applicable; for the frozen production file use `env.tp4.as-deployed-20260913` (value-identical
  to what the live lane runs), and for the pre-durablefix v1.1.4 file the historical
  `env.tp4.as-deployed-20260912`.

**Results**
- **Communication line CLOSED as configuration.** Six candidates, six exact stop sites:
  two-batch overlap (hard boot reject for this model, `deepseek_v4_hook.py:280`), fused MoE-sum
  + all-reduce (Triton-runner-only), MoE finalize + TP all-reduce fusion (requires the
  `flashinfer_trtllm` runner + NVFP4 pairing we do not run — the engine states it at boot),
  FlashInfer all-reduce fusion (SM100-gated, GB10 is SM121), quantized comms (NPU-only), fused
  qk-norm-rope (other architectures). Plus the one that would have cost a **failed boot**:
  setting `--flashinfer-allreduce-fusion-backend` on GB10 raises from
  `flashinfer_comm_fusion.py:56` via `distributed/bootstrap.py:203`. Method: source audit of the
  running image, no boot spent. `docs/COMM-AUDIT.md`, `docs/TUNING-LOG.md` §7.
- **Provenance gap CLOSED** with a repeatable value-for-value audit, now part of the release
  checklist. `docs/PROVENANCE-AUDIT.md`.
- **Next lever staged, not run:** prefill chunk sizing (`CHUNKED_PREFILL_SIZE` 4096 → 8192),
  one-variable A/B, engine-side metrics + cold-turn TTFT, explicit contaminant discard rules and
  one-copy rollback. `docs/WINDOW-PREFILL-CHUNK.md`.

**Docs**
- New: `docs/COMM-AUDIT.md`, `docs/PROVENANCE-AUDIT.md`, `docs/WINDOW-PREFILL-CHUNK.md`.
- `docs/TUNING-LOG.md`: §7 (the comm closures + the "env file is part of the formula" rule).
- `docs/RECOMMENDATIONS.md`: status update — acceptance line closed negative, comm closed,
  prefill chunk sizing named as the remaining configuration lever.
- `README.md`: deviation table extended (row 9: DSpark instrumentation values), version bump.

## v1.1.4 — 2026-09-13 — KV pool raised; acceptance, engram and NCCL lines closed; comm named as the lever

**Formula changes (both reversible, both measured on live A1–A4)**
- `MAX_TOTAL_TOKENS` **4,000,000 → 8,000,000** (FP8 KV). Covers ~8 concurrent 1M-token
  sessions. Speed-neutral (the step is latency-bound, not KV-bound). Rollback: `4000000`.
- `patches/0002-dspark-env-passthrough.diff`: `start.sh` now forwards the DSpark
  instrumentation/experiment variables (`SGLANG_DSPARK_BLOCK_ACCEPT_ONLINE_INTERVAL`,
  `SGLANG_DSPARK_FOLDED_PROPOSAL`, `SGLANG_DSPARK_ENABLE_SPS_RECORD`, `SGLANG_SIMULATE_ACC_LEN`,
  `SGLANG_RAGGED_VERIFY_MODE`, `SGLANG_DSPARK_STS_COLLECT_PATH`) to head **and** workers.
  **Why it matters:** `start.sh` passes an explicit allowlist; without these lines a variable set
  in `.env.tp4` never reaches the containers, and an experiment silently runs as a no-op.
- `patches/0004-status-weights-check.diff`: `cmd_status` tested a head-side path on the workers
  and reported a false `weights:MISSING` on a healthy fleet; it now tests the container's own
  mount.
- `patches/0003-dockerfile-encoding-guard.diff`: build-time patch for the placeholder-token
  guard that otherwise turns any transcript carrying the literal token into a self-sustaining
  HTTP 500 loop. Fails the build loudly if the base image drifts.

**Results — three lines closed, one named**
- **Acceptance line CLOSED (negative).** Uncapped accept-length cumulative ≈ **2.66** over 15,689
  blocks, i.e. *below* production's 3.25; the folded-off arm measured 2.84 mean and ~**9% slower**
  than folded-on. The recorder that produced the pinned "+23%" reading is structurally blind
  unless folded proposal is off (`dspark_observability.py` gate), and the cap thresholds were
  already 1.0. No patch to write. Patch `0002` exists precisely so this line can be re-run: see
  `docs/EVIDENCE.md` §10.
- **Engram line CLOSED for speed.** 230 ms of 35.7 s of GPU work = **0.6%**; NVMe ~12% util,
  8× headroom; 2 GiB/layer 16-way cache, 84–86% hit single-stream. Engram buys memory and
  cold-start, not tok/s.
- **NCCL LL128 line CLOSED (negative).** The `^LL128` ban exists to save pinned RAM (4.7 GiB →
  0.14 GiB). Re-allowing it with the small buffers kept (~128 MiB pinned) measured **+1.7%** —
  inside run-to-run noise. Protocol choice is not the lever.
- **Comm named as the remaining lever.** bf16 all-reduce is 23% of GPU work at ~800 calls/s ×
  279 µs. Candidates not yet tested: two-batch overlap, fused MoE-sum + all-reduce, quantized
  communications. FlashInfer all-reduce fusion is gated out on SM121.

**Ops / method notes**
- Engine logs die with the container: harvest `docker logs` **before** any restart, or the
  measurement window is lost.
- `tools/gpu-state-probe.py` can no longer run alongside the engine (8M pool + 0.90 mem
  fraction ⇒ `cudaErrorMemoryAllocation`); per-node slow-state checks need a maintenance window.
- Fleet traffic from other agent lanes makes matched A/B windows scarce; label and discard
  contaminated windows rather than averaging through them.

**Hardware**
- Firmware is not uniform across the four nodes: 2× driver `580.173.02` / BIOS `0105`, 2× driver
  `580.159.03` / BIOS `0104`. Documented, not changed; TP4 lockstep makes the slowest rank the
  step time, so this is a standing suspect (`docs/EVIDENCE.md` §12).

**Limits at release**
- Single-stream throughput is unchanged by this release (~36 tok/s client median in a quiet
  window, ~37 engine-side). v1.1.4 is a capability/durability release, not a speed release.
- The comm candidates above are *named*, not *proven*.


## v1.1.3 — 2026-09-12 — follow-up measurements: one gain confirmed, one falsified (tooling/docs only)

- **Falsified (our own recommendation).** Context hygiene was ranked the #1 speed lever. Measured
  with interleaved arms and 300-token generations: decode **12.4 / 11.2 / 12.3 tok/s at
  8k / 32k / 96k** of context — twelve times the context, no measurable change. The step is
  latency-bound, not KV-read-bound; summarising context for speed is wasted work. Withdrawn.
- **Confirmed and quantified.** Append-only vs rewritten prefix over six turns on identical
  ~33k-token content: **0.51 s flat vs 11.0–11.5 s every turn** (21× per turn, never recovers).
  The live fleet does not violate it: 94.6 % of prompt tokens cached, only 7.1 % of the lane's
  wall clock goes to cold prefill, and the two >8k-uncached turns in the clean window had 0
  cached tokens (new sessions, not re-prefills). Prefix stability is therefore a guardrail.
- **Retargeted the one real prefill lever**: cold-start turns with 16–43k uncached tokens are
  served as 4–11 sequential 4096-token chunk-steps ⇒ 4.4–11.6 s TTFT; chunk/max-prefill sizing
  for that shape is now the top engine-side candidate (window required, still untested).
- **Named the acceptance lever precisely**: the confidence-capped (compact) verify path is dead
  on this build — head built for `hidden+markov` (5376), fed the draft-stage hidden (4352),
  raising at `deepseek_v4_dspark.py:1021` — so the align-to-graph-tier flag is inert. Patch
  candidate; the only lever that raises tokens per step for every stream at once.
- **Tools**: `tools/context-and-turns-probe.py`, `tools/context-curve-interleaved.py`,
  `tools/lane-tax-window.py`, `tools/classify-prefills.py` (all read-only; the last two measured
  the same window two independent ways and agreed to 2 %).
- No formula change; A1–A4 untouched and serving throughout.

## v1.1.2 — 2026-09-12 — TTFT decomposition and prefix-stability finding (tooling and docs only)

- **`docs/TTFT-AND-CACHE.md`** — TTFT decomposed on the live lane: cached TTFT is flat (0.35 s @
  126 tok → 0.52 s @ 39,346 tok), cold prefill runs **3.5–3.9k tok/s**, and the felt tail is the
  uncached-token tail (p99 54,280 uncached ≙ ~15 s). Newly closed by measurement: no fp4 KV dtype
  exists in this image (`fp8_e4m3` already the smallest), KV pool 7 % used, γ 3.42 of 4 accepted.
  No formula change.
- **Prefix stability finding** — identical 40k-token content, a value that changes between turns
  placed at the top → **14.005 s** TTFT; at the end → **0.594 s**. Anything volatile placed early
  re-prefills the whole tail through the radix cache every turn. Free to fix; worth ~20× felt TTFT.
- **`tools/ttft-probe.py`, `tools/prefix-invalidation-probe.py`, `tools/metrics-quantiles.py`** —
  the three read-only probes that produced the above (production endpoint, no engine change).
- **Publication scrub fix.** The generator's substitution and post-scan had no rule for the
  owner's name or the account paths, so `/home/<user>` and `WORKER_USER` were published in the
  deployed-profile snapshot and six tooling/unit files from v1.1.0 onward. Both now substituted
  and both added to the fail-closed site scan (the scan exits non-zero on any hit).

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
