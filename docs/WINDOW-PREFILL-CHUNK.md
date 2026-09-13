# WINDOW — prefill chunk sizing (the remaining configuration lever)

**Status: STAGED, NOT RUN.** This is the one candidate left that is (a) reachable by
configuration, (b) aimed at an already-measured cost, and (c) reversible in one file copy.
It needs a ~20–30 minute maintenance window and the owner's go-ahead, because it restarts the
lane the whole fleet is served from.

## Why this and nothing else

Measured cost, from `docs/TTFT-AND-CACHE.md` and the lane tax window:

- **7.1% of the lane's wall clock** is cold prefill (21 s per 5-minute window; 79,288 uncached
  tokens at ~3.7k tok/s).
- The expensive turns are cold-start requests carrying **16–43k uncached tokens with 0 cached**
  (`chunked_prefill_size=4096` ⇒ 4–11 sequential chunk-steps ⇒ **4.4–11.6 s TTFT**).
- p50 turn is 1,536 uncached tokens; the fleet's steady state is append-only and cheap. So the
  lever is *specifically* the first turn of a session and any large paste — exactly the turns an
  agent notices.

Every other candidate is closed: γ (spent), acceptance (closed negative, §10), engram (0.6%),
NCCL protocol (+1.7%, §11), all six communication routes (`docs/COMM-AUDIT.md`), KV capacity
(7% used), context hygiene (falsified), prefix caching volume (98.9% hit already).

## Hypothesis

`chunked_prefill_size` is a **scheduling quantum**, not a compute bound. Raising it from 4096 to
8192 halves the number of chunk-steps a cold 16–43k request is cut into, so per-step fixed
overheads (launch, scheduling, radix bookkeeping, the interleave with live decode traffic) are
paid half as often. Predicted effect: **TTFT on cold 16–43k turns improves, all else equal**;
steady-state decode is untouched (the step is latency-bound at ~12× above the bandwidth bound —
`EVIDENCE.md` §4).

Alternative outcome, also informative: no change, i.e. prefill is genuinely compute/bandwidth
bound at this size and the chunk-step count is not what costs. Then the cold-prefill tax is a
hard floor for this engine and the honest answer to the fleet is "make the first turn smaller"
— a harness-side fix, not a recipe fix.

## Design (one variable, both arms on the same image)

| | Arm A (control) | Arm B |
|---|---|---|
| `chunked_prefill_size` | 4096 (current, in `env.tp4`) | **8192** |
| `max_prefill_tokens` | 16384 | 16384 (unchanged — keep the ceiling) |
| `MEM_FRACTION_STATIC` | 0.90 | 0.90 (unchanged) |
| everything else | unchanged | unchanged |

One variable. 8192 before 16384: 16384 is the ceiling and may raise activation memory inside a
0.90 static fraction; if 8192 is clean and gains, a second window can try 16384.

Primary metric — **engine-side, not client-side** (the lane is shared; client tok/s is
confounded, see `EVIDENCE.md` §5):

1. `sglang:prefill_effective_tokens_total` rate over each arm (metrics are live since the
   v1.1.4 durable-fix window) — prefill throughput in tok/s.
2. `Prefill batch` lines harvested from `docker logs dsv41-head` **before** the next restart
   (logs die with the container — `TUNING-LOG.md` §6), fed to `tools/classify-prefills.py`
   (per-request cold clusters) and `tools/lane-tax-window.py` (share of wall clock).
3. Client TTFT on a **deliberately cold** 16–43k-token prompt (unique text each rep, so the
   radix cache cannot mask the effect) via `tools/ttft-probe.py` and a purpose-built cold probe.

Acceptance: Arm B's cold-turn TTFT (p50) and prefill tok/s both improve beyond the spread seen
between two control windows; **or** the result is "no change", which is written down as such.

## Procedure (exact, in order)

```bash
R=~/ai/recipes/DeepSeek-v4.1-Flash-DGX-Sparks          # on spark1
RT=~/ai/runtime/deepseek-v41-flash-a1a4-sglang
U=deepseek-v41-flash-a1a4-sglang-tp4.service

# 0. proof of the starting state (do not skip: this is what makes the A/B interpretable)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
python3 $RT/lane-status.py
docker logs dsv41-head 2>&1 | tail -2000 > /tmp/armA-taillog.txt        # harvest BEFORE restart

# 1. latch up (the watchdog must not fight us)
touch $RT/maintenance.latch

# 2. backup, then the single edit
cd $R && cp .env.tp4 .env.tp4.pre-chunk8k-$(date +%Y%m%d-%H%M%S)
# edit: DSPARK/CHUNKED_PREFILL... the key is CHUNKED_PREFILL_SIZE in .env.tp4; set it to 8192
grep -n 'CHUNKED_PREFILL_SIZE\|MAX_PREFILL_TOKENS' .env.tp4          # assert the pair before boot

# 3. restart (never `start`; always reset-failed first — OPERATIONS.md §Trap 2)
systemctl --user reset-failed $U && systemctl --user restart $U

# 4. VERIFY THE RESOLVED VALUE IN THE BOOT LOG, never the env line
docker logs dsv41-head 2>&1 | grep -m1 'server_args=' | tr ',' '\n' | grep -E 'chunked_prefill_size|max_prefill_tokens'
docker logs dsv41-head 2>&1 | grep -E 'gamma=|available_gpu_mem' | head

# 5. gates + warm-up, then measure arm B with the same probes as arm A
#    verify.sh is the packaged gate (health, served identity, garbled gate, needle gate).
#    It needs curl + the $H harnesses only; the deployed recipe dir on the head has start.sh
#    but not verify.sh, so run it from a checkout of this repo (or copy it to the head first).
bash verify.sh
python3 $RT/warm-lane.py
```

Then: `docker logs dsv41-head 2>&1 > /tmp/armB-taillog.txt` after the measurement window, and
compare A vs B with the two analysis tools.

**Always, whatever happens:** `rm -f $RT/maintenance.latch` — a latched lane has no health
protection (`OPERATIONS.md` §1).

## Rollback (one copy + one restart)

```bash
cd ~/ai/recipes/DeepSeek-v4.1-Flash-DGX-Sparks
cp .env.tp4.pre-chunk8k-* .env.tp4
systemctl --user reset-failed deepseek-v41-flash-a1a4-sglang-tp4.service
systemctl --user restart  deepseek-v41-flash-a1a4-sglang-tp4.service
rm -f ~/ai/runtime/deepseek-v41-flash-a1a4-sglang/maintenance.latch
```

The image is untouched, so rollback is a file copy and one boot — the same shape as the γ and
KV-8M windows already proven on this lane.

## Traps that will cost the window if ignored

1. **Logs die with the container.** Harvest `docker logs` before every restart or the arm's
   evidence is unrecoverable.
2. **The lane is shared with other agent lanes.** Label each arm's window; if `#running-req`
   swings to 8 mid-arm or a 60+ prefill/min cadence appears, discard that arm and repeat.
   Never average through a contaminated window.
3. **`gpu-state-probe.py` cannot run alongside the engine** (8M pool + 0.90 fraction ⇒ CUDA OOM).
   If the per-node slow-state check is wanted, do it with the lane down *before* the arms, not
   during them.
4. **The latch must be dropped**, including on failure paths.
5. **Do not add `--flashinfer-allreduce-fusion-backend`** to chase the communication share in
   the same window: on GB10 it raises at boot and would burn the window (`docs/COMM-AUDIT.md`).

## Outcome bookkeeping

Whatever the result, it goes into `docs/EVIDENCE.md` (new section) with the window label, then
into `CHANGELOG.md` as v1.1.6 with either the new `CHUNKED_PREFILL_SIZE` (if it wins) or
"closed, no change" (if it does not) — the same discipline that closed γ, engram, NCCL and the
acceptance line.