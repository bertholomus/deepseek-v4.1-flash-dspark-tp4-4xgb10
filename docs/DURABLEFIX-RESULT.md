# DURABLE-FIX WINDOW RESULT — 2026-09-12

All three durable fixes are LIVE in production. Lane discipline was followed throughout
(latch up during work, env+image backups, reset-failed, restart, gates, latch down).

## What changed (all three verified on the running engine)

### Fix A — image-placeholder 500-loop (upstream PR #15 class) — FIXED, image-level
- `runtime/patch_encoding_dsv41.py` (89 lines, idempotent, drift-fails) bakes into the image
  at build: Dockerfile `COPY` + `RUN python3 .../patch_encoding_dsv41.py` after the
  flash_mla copy. Verified **inside the running container** (`grep` marker = 2 lines) and
  idempotence tested twice in a throwaway container before ever touching production.
- Image: `dsv41-4x-spark:local` = `4c10c763c1ba` (was `6aa30088ec7a`).
- Rollback image tag: `dsv41-4x-spark:pre-durablefix-20260912-135455` (and `-140012`).

### Fix B — engine metrics — ENABLED, config-level
- `.env.tp4` `EXTRA_SGLANG_ARGS` += `--enable-metrics --enable-metrics-for-all-schedulers`.
- Verified from the engine's own resolved `server_args` line: `enable_metrics=True`,
  `enable_metrics_for_all_schedulers=True`. `/metrics` now serves **447 `sglang:*` series**
  (prefill_effective_tokens_total, realtime_tokens_total, cuda_graph_passes_total, ...).
- env-edit script is self-checking (flag-pairing assertions) — caught a spacing bug in the
  first attempt *before* it could reach a boot (`--enable-deepseek-v4-fp4-indexer--enable-metrics`
  would have been parsed as one garbage flag).

### A/B — `--enable-mixed-chunk` — ENABLED (winner by direct witness), config-level
- Verified resolved: `enable_mixed_chunk=True`.
- Direct witness of the mechanism working: prefill lines now show `#running-req: 1` while a
  decode is in flight (prefill sharing the step with running decode) — the exact interleave
  stall this flag removes; 16 prefills in a 12-min live window with queue depth 0.
- Post-change warm plateau: **engine_tp=113.34** (best recorded; previous best 108.99/108.86).
- Contention-normalized single-stream (4×300, fleet traffic present — all samples labelled
  CONTENDED, max running-req 2): client 27.1–31.2 tok/s, **engine@req1 medians 57.3/70.4/58.4**,
  lane-wide decode median 55.7 vs ~46–52 pre-change windows this morning. Not a clean A/B
  (fleet was live both times), so treat as supportive, not decisive; the decisive evidence is
  the plateau + the interleave witness.

## Gates (all on the new image, warm lane)
- health 200; served name `deepseek-v4.1-flash` unchanged; `context_len=1048576` unchanged.
- garbled gate: **5/5 PASS, 0 garbled** (one earlier FAIL was a bug in my gate harness's
  regex handling, fixed — the model's output was correct).
- needle gate: **PELICAN-1 exact @ ~136k depth, PASS**.
- no-op audit: only the known inert `--speculative-dspark-align-verify-tokens-to-graph-tier`
  (static mode) — no new no-ops introduced.
- DSpark: gamma=3, verify_num_draft_tokens=4 — unchanged.

## Durable-tooling additions
- `tools/batchtest.py` (regex-aware garbled gate) + `tools/prefill_distinct.py` (needle gate)
  now live in the lane dir on spark1 and in the recipe repo — `verify.sh` re-pointed at the
  lane head (`127.0.0.1`) so gates run where the harnesses live. Previous versions pointed at
  `$HOME` on spark2 and the harnesses no longer existed there.

## Rollback chain
- Env: `.env.tp4.pre-durablefix-20260912-135455` (gamma-3 pre-window config).
- Image: `dsv41-4x-spark:pre-durablefix-20260912-135455` / `-140012` (pre-patch image).
- Dockerfile backup: `Dockerfile.pre-durablefix-*` in the recipe dir.
- Rolling back = restore file/tag + `reset-failed` + `restart`.

## Status after window
- Unit active, health 200, watchdog timers active, **maintenance latch DOWN**.
- Endpoint/name/context unchanged — fleet profiles (our other agent lanes) need
  zero config change.
