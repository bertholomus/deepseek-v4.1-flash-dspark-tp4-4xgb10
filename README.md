# DSV41-Flash TP4 Spark Recipe — v1.1.6 (gamma-2 + durable fixes)

**BertholomusAI in-house deployment recipe for DeepSeek-V4.1-Flash on 4× NVIDIA DGX Spark (GB10), tensor-parallel 4, served by SGLang.**

This is *our* formula: the upstream launcher repo plus our measured, evidence-gated
deviations. Everything here was verified on live hardware (spark1..spark4) on 2026-09-11/14.

**Ownership.** The **BertholomusAI in-house recipe**, maintained as a first-class versioned
artifact rather than a fork: the upstream-facing *code* changes are the four `patches/000NN-*.diff`
files (per-node active-HCA discovery, DSpark env passthrough, the Dockerfile encoding guard, and
the `cmd_status` weights check), with `0001` submitted back as PR #10. Upstream attribution and third-party licences are preserved in
`NOTICE.md`. Licensed **AGPL-3.0-or-later** — required, not chosen: this recipe derives from an
AGPL-3.0-or-later launcher repo (§ Licensing in `NOTICE.md`).

> All hosts (`spark1`…`spark4`) and addresses (`10.0.0.x`, `100.64.0.1`) in this repository are
> placeholders. Substitute your own.

| | |
|---|---|
| **Recipe version** | **v1.1.6** (codename *gamma-2* + durable fixes) |
| **Target** | 4× DGX Spark (GB10, SM121), 2-rail CX7 fabric, TP4 |
| **Serving stack** | SGLang `dev-dsv41` (image tag `dsv41-4x-spark:local`) |
| **Model** | DeepSeek-V4.1-Flash (checkpoint `dba1be0a`, MIT), 48 shards, /models/DeepSeek-V4.1-Flash |
| **Upstream base** | `MiaAI-Lab/DeepSeek-v4.1-Flash-DGX-Sparks` @ `e59e6eb` |
| **Endpoint** | `http://100.64.0.1:8000/v1` (tailnet), served name `deepseek-v4.1-flash` |
| **Context** | 1,048,576 tok/request · 8,000,000 tok shared KV pool (FP8 KV) |
| **Status** | LIVE production on A1–A4 |

---

## 1. What makes it ours

Seven measured deviations from the upstream example plus one required patch, plus the
instrumentation values production runs (nine rows in total). Each was
A/B'd on the live lane against the same harness (`tp4_ab.py` / `agentic_single_stream.py`,
temp 0, warm lane) — nothing here is a guess. Full numbers: `docs/EVIDENCE.md`.

| # | Change | Upstream | Ours | Effect |
|---|---|---|---|---|
| 1 | `DSPARK_BLOCK_SIZE` (γ, speculative verify window = γ+1) | `5` | **`2`** | +6–12% vs γ-3 at the fleet's real depth (8k/32k/96k, 2026-09-14 fair-go window). Supersedes the v1.1.0 short-prompt sweep (5→4→3, whose "γ=2 would clip accepts" stop rule did not hold at 104k+ context). |
| 2 | `MAX_TOTAL_TOKENS` (KV pool) | `750000` | **`8000000`** | 1M-context × ~8 concurrent sessions; raised 4M→8M 2026-09-13, measured speed-neutral |
| 3 | `EP_SIZE` | `4` | **`2`** | fitted to per-node memory headroom |
| 4 | `DSV41_CACHE_GIB` (engram NVMe KV cache) | `0` | **`4`** | 12 GiB **rejected** — head host-RAM exhaustion ~90 s into weight load |
| 5 | `DSV41_CACHE_WAYS` | `4` | **`16`** | engram hit rate 74% aggregate (82% @A1 → 44% @A4) |
| 6 | `EXTRA_SGLANG_ARGS` | `--fp8-gemm-backend flashinfer_cutlass --watchdog-timeout 1800` | **+ `--speculative-dspark-align-verify-tokens-to-graph-tier --min-free-slots-delay 1 --enable-deepseek-v4-fp4-indexer --enable-metrics --enable-metrics-for-all-schedulers --enable-mixed-chunk`** | graph-tier alignment; FP4 indexer path; engine metrics; mixed-chunked prefill (measured winner 2026-09-12) |
| 7 | `NCCL_HOST_DIR` | `$HOME/nccl-2.30.7` | **empty** | not vendored in repo; image ships NCCL 2.28.3 |
| 8 | `WORKER_ENGRAM_DIR` | — | **`$HOME/dsv41-engram`** | A4 home is an XFS-loop symlink; docker resolves symlinks |
| 9 | DSpark instrumentation/experiment variables | unset | **`SGLANG_DSPARK_FOLDED_PROPOSAL=1`, `SGLANG_DSPARK_BLOCK_ACCEPT_ONLINE_INTERVAL=60`, `SGLANG_DSPARK_ENABLE_SPS_RECORD=0`, `SGLANG_SIMULATE_ACC_LEN=-1`** | the values every measurement in `docs/EVIDENCE.md` was taken at; they only reach the containers because patch `0002` forwards them |
| P | **Patch: per-node active IB HCA discovery** (`patches/0001-*.diff`, +37/−1 in `start.sh`) | hardcoded `IB_HCA` | auto-detect PORT_ACTIVE per node | **required** — TP4 boot fails on mixed HCA naming (`mlx5_0/mlx5_2` vs `rocep1s0f0/roceP2p1s0f0`). Submitted upstream as PR #10. |

Kept at upstream defaults (verified correct for us): `MEM_FRACTION_STATIC=0.90`,
`HEAD_MEM_FRACTION_STATIC=0.90`, `MAX_RUNNING_REQUESTS=8`, `CHUNKED_PREFILL_SIZE=4096`,
`CONTEXT_LENGTH=1048576`, `--watchdog-timeout 1800`.

## 2. Measured results (warm lane, temp 0, medians)

> **Contention caveat (measured 2026-09-12).** This lane has live production consumers: the
> fleet profiles our other agent lanes all target
> `http://100.64.0.1:8000/v1`. Engine logs during a "quiet" test window showed
> `#running-req: 3→8`, `#queue-req: 2`. Treat every number below as a **contended**
> measurement unless it says otherwise, and quote single-stream numbers only with the
> contention marker from `tools/bench-normalized.py`.

| Concurrency | Upstream stock | **This recipe** | Δ |
|---|---|---|---|
| 1 | 16.9 | **~35–38** | ~2.1× |
| 2 | 28.5 | **59.5–61.4** | ~2.1× |
| 4 | 42.6 | **78.8–79.4** | ~1.85× |

### Versus the upstream author's own published numbers

`MiaAI-Lab` publishes a benchmark table in the README of the very commit this recipe derives
from (`e59e6eb`), measured with the same counter we use — the `gen throughput` field of the
`Decode batch` log lines, which that README explicitly names as the correct decode metric.
Head-to-head, aggregate tok/s:

| Streams | Upstream `e59e6eb` (published, 3-node triangle) | **This recipe** (live lane, carrying fleet traffic) | Δ |
|---|---|---|---|
| 1 | 37.9 (TTFT 248 ms) | **46.1** median | **+22%** |
| 2 | 58.9 (30.5/stream) | **72.0** (36.0/stream) | **+22%** |
| 3 | 71.2 (24.5/stream) | **94.2** (31.4/stream) | **+32%** |
| 4 | 78.6 (20.9/stream) | **103.2** (25.8/stream) | **+31%** |

Capability, from the same two documents:

| | Upstream `e59e6eb` | **This recipe** | Δ |
|---|---|---|---|
| Context **configured** | 200k–256k (model max 1M) | **1,048,576** | **4.1–5.2×** |
| KV pool | 750,000 tok | **8,000,000 tok** (FP8) | **10.67×** |
| Speculative verify window | 6 tokens (γ=5) | **3 tokens (γ=2)** | narrower, measured faster at fleet depth |
| Free memory while serving | ~6 GB on the head | **29–32 GB/rank** | — |

Read this honestly: it is *their published table* versus *our live lane mid-production*, not a
controlled A/B — different prompt mix, and our TP4 has more all-reduce legs than their TP3
triangle (an advantage to them on single-stream). The like-for-like controlled comparison is
the stock-vs-ours table above, same hardware and same harness. See `docs/EVIDENCE.md`.

- Engine single-stream metric (the counter 3rd-party TP3 demos quote): median **~46–52**, peaks **57.9–70.8**, best `running-req:1` samples **112.6** during high-accept windows.
- Warm plateau (`engine_tp`, bs=8): **97.1** (γ-3) vs 89.0 (γ-5).
- Quality gates: **0 garbled** outputs; needle-in-haystack **PELICAN-1 exact @ 141,385 tok** (prefill 1773 tok/s).
- Idle behaviour: an earlier "idle decay to ~20–28 / 37.3 at 18 min" reading is **retracted** —
  those probes were contention-mixed, not decay. Re-measure with `tools/bench-normalized.py`
  before quoting anything about warm/cold on this lane. Details: `docs/EVIDENCE.md` §5.

## 3. Lane anatomy

```
env.tp4                   the formula (production copy, deltas annotated)
env.tp4.raw               exact live file as deployed 2026-09-12
patches/                  required launcher patch (PR #10)
units/                    systemd: engine, watchdog(+timer), keep-warm(+timer)
tools/
  adopt-or-start-tp4.sh   the ONLY sanctioned boot/restart entrypoint
  warm-lane.py            post-boot warmer (proven: plateau in ~128 s)
  keep-warm.py            idle heartbeat (reuses warm-lane.py; fires only if idle ≥ 10 min)
  watchdog-dsv41-flash-tp4.py  health watchdog, respects maintenance latch
  bench-normalized.py     single-stream bench that also records contention (never quote
                          a bare client tok/s on this lane without it)
  lane-status.py          one command: "slow, or just busy?" (health, running-req, queue,
                          accept len, per-stream estimate, verdict)
  fleet-load-analysis.py  read-only: per-stream speed vs concurrency, cache hit, queue depth
  prefill-deepdive.py     read-only: prefill behaviour under load (see the artifact warning)
docs/
  OPERATIONS.md           boot/restart discipline, latch, rollback
  EVIDENCE.md             every number with method + date
  TUNING-LOG.md           the sweep story and the audited dead-end bank
  FLEET-BASELINE.md       what the fleet actually experiences (measured) + measurement rules
  RECOMMENDATIONS.md      ranked next improvements (and what is provably not worth doing)
verify.sh                 post-deploy gates (health, model list, garbled, needle)
```

## 4. Boot / restart — read this first

```bash
# NEVER: systemctl --user start   (oneshot + RemainAfterExit = silent no-op)
systemctl --user reset-failed deepseek-v41-flash-a1a4-sglang-tp4.service
systemctl --user restart  deepseek-v41-flash-a1a4-sglang-tp4.service
```

Two traps, both cost us boots on 2026-09-12: `start` is a **no-op** after out-of-band
container stop (`RemainAfterExit=yes`), and omitting `reset-failed` after several restarts
hits the start-rate limiter (`start request repeated too quickly`).
Full discipline, including the maintenance latch: `docs/OPERATIONS.md`.

## 5. Known limits (honest)

- **Single-stream steady ≈ 52 tok/s** engine-counter on TP4. A 3-node TP3 demo quotes 69 —
  fewer all-reduce legs per verify step is a genuine topology edge, not a config gap.
  We cross 69 in bursts and win decisively on aggregate/multi-stream, but that flat
  median is still the open target (see `docs/TUNING-LOG.md` §open).
- **Idle decay**: a cold lane loses roughly a third of single-stream speed within ~20 min
  of no traffic, worse at the cold floor right after boot (conc1 16.2). Mitigation is
  operational, not config: `warm-lane.py` after boot + the `keep-warm` heartbeat.
- **SPS / cap-accept (budgeted verify) are closed on this build** — three measured walls,
  see `docs/TUNING-LOG.md`. Upstream issue drafted from the two DSpark bugs found.
- SM121 is **not** SM100: anything gated `cutedsl` is unreachable here.

## 6. Provenance & licensing

- Upstream launcher: MiaAI-Lab recipe repo, Apache-2.0, pinned `e59e6eb`.
- SGLang: Apache-2.0.
- Model: DeepSeek-V4.1-Flash, **MIT** (`LICENSE` in the checkpoint). Weights are *not*
  redistributed here — this repo is configuration, patches and operations tooling only.
- Patch P was offered upstream as PR #10 from `bertholomus` (`8d595d6`).

---

## Where the remaining speed lives (2026-09-13 profile)

A torch-profiler capture of live decode (4.6M events over 37 s) says where the time goes, and
it is not the two places people expect:

| Subsystem | Share of GPU work |
|---|---|
| MoE GEMM (already MXFP4, 4.25 bpw, QAT'd upstream) | ~37% |
| **bf16 inter-node all-reduce** (29,838 calls ⇒ ~800/s, 279 µs each) | **23%** |
| Dense GEMM | ~11% |
| mHC | ~3.6% |
| Attention | 2.6% |
| **Engram gather + hash** | **0.6%** |

Consequences, each measured rather than assumed:

- **Quantization's classic ~2× prize does not exist here.** The routed experts already ship
  4-bit (MXFP4) as QAT'd by DeepSeek; ~57% of the checkpoint is already 4-bit. The only large
  FP8 block left is the engram tables, and engram is 0.6% of the step. See `docs/EVIDENCE.md` §9.
- **Engram is not a speed lever.** NVMe sits at ~12% utilisation with ~8× headroom; hit rate is
  84–86% single-stream (67.7% at 4 streams) on a 2 GiB/layer, 16-way cache. It buys memory and
  cold-start, not tok/s. §9.
- **The acceptance lever is closed.** The uncapped accept-length ceiling (~2.66) sits *below*
  what production already achieves (3.25); the recorder that produced the +23% reading only runs
  when folded proposal is off, and folded proposal is ~9% faster. §10.
- **NCCL protocol is not the lever either.** Re-allowing LL128 (the ban existed to save pinned
  RAM: 4.7 GiB → 0.14 GiB) measured **+1.7%, inside noise**. §11.
- **The communication candidates are closed as configuration** (v1.1.5): two-batch overlap
  (hard boot reject for this model), fused MoE-sum + all-reduce (Triton runner only), MoE
  finalize + TP all-reduce fusion (needs the TRT-LLM runner pairing we do not run — the engine
  says so at boot), FlashInfer all-reduce fusion (SM100-gated; GB10 is SM121, and forcing the
  flag raises at boot), quantized comms (NPU-only), fused qk-norm-rope (other architectures).
  Exact stop sites: `docs/COMM-AUDIT.md`. The 23% is therefore a code-level property of this
  path, not an unturned knob; reaching it needs an upstream change, an SM100-class part, or
  fewer TP legs (owner-declined).
- **The lever still open is prefill chunk sizing**, staged as a one-variable A/B with rollback:
  `docs/WINDOW-PREFILL-CHUNK.md`. Cold prefill is 7.1% of lane wall clock, and the expensive
  turns are the 16–43k uncached ones cut into 4–11 chunk-steps at `chunked_prefill_size=4096`.

**Hardware caveat worth stating plainly:** the four nodes are *not* on identical firmware —
two run driver `580.173.02`/BIOS `0105`, two run `580.159.03`/BIOS `0104`. TP4 lockstep means the
slowest rank sets the step, so non-uniform firmware is a standing suspect; it has not yet been
shown to cost throughput and was deliberately **not** changed mid-window. §12.
