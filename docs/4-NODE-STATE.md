# 4-NODE STATE — is every Spark actually fast? (hidden GB10 slow state)

**Measured 2026-09-12 22:50 UTC, all four nodes, engine live and healthy (health 200 before
and after every probe).** Tool: `tools/gpu-state-probe.py` (added with this doc).

## Why this exists

A 4-node recipe has a failure mode no 3-node or single-node recipe has: **one slow node drags
all four**. TP4 runs in lockstep, so if one Spark's memory path degrades, every token gets
slower — and nothing in our dashboards would say why. The obvious check (`nvidia-smi` SM clock,
throttle reasons) does **not** detect it.

Reported by Tech2Wild, `tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark` issue #1 "GPU slow
state" (MIT). Their finding, on four Sparks:

- Achievable memory bandwidth collapses in the slow state: decode-shaped GEMV **66–80 GB/s**
  slow vs **224–233 GB/s** fast (≈3.3×).
- `nvidia-smi` shows the **same** SM clock in both states (2164–2177 MHz) and throttle reasons
  `0x0`; a device copy is unaffected (239–242 GB/s both states) — so the loss is below the
  reported SM clock, in the memory path.
- Power draw under GEMV is the only external signal: 14–16 W slow vs 18–23 W fast.
- Each state lasts 7–32 s and then flips abruptly; the first request after a long idle can run
  slow from its first step to its last.
- Distinct from the known GB10 EC clock latch (630–950 MHz, cleared by an AC power cycle) —
  this one reports ~2.2 GHz and AC cycling is not the remedy.
- Their untested lead: hidden firmware power limits (pl1 / syspl1, maybe USB-PD negotiation)
  below the level where NVML reports a throttle reason; the `antheas/spark_hwmon` driver can
  read them.

`tools/gpu-state-probe.py` is our independent implementation of that measurement: a warmup,
then ~6 s of back-to-back decode-shaped GEMV (`6×5120 @ 5120×16384` bf16), reporting min/p10/
p50/max GB/s and a fast/slow/mixed verdict (p50 ≥ 150 GB/s = fast, ≤ 110 = slow).

## Our measurement

| Node | smi before (clock / power / util / °C) | probe p50 | min | max | verdict |
|---|---|---|---|---|---|
| A1 (head) | 2444 MHz / 38.0 W / 91 % / 71 °C | **247.5 GB/s** | 196.5 | 424.0 | fast |
| A2 | 2470 MHz / 39.4 W / 96 % / 72 °C | **246.9 GB/s** | 197.1 | 442.8 | fast |
| A3 | 2411 MHz / 15.7 W / 5 % / 68 °C | **249.1 GB/s** | 98.5 | 443.9 | fast |
| A4 | 2405 MHz / 32.7 W / 84 % / 73 °C | **246.6 GB/s** | 117.1 | 259.0 | fast |

- All four nodes clear the fast band by a wide margin (our p50 ≈ 247 GB/s; the slow state is
  66–80 GB/s). **No node was in the hidden slow state**, in this sample, under live fleet load.
- The low `min` values (98–197 GB/s) are *contention with the running engine*, not the slow
  state: the reported defect is a sustained p50 collapse, not a dip. Sample count 174–193 per
  node over ~6 s.
- A3 was at 5 % util / 15.7 W while still probing 249 GB/s — i.e. an idle worker is fast too,
  which is the state a slow node would be caught in.
- Engine health was 200 before and after; the probe is a ~6 s load per node and left the lane
  serving. It is safe to run on the live lane, node by node.

## How to run it

```bash
# on any node, from the operator side:
scp tools/gpu-state-probe.py <node>:/tmp/
ssh <node> 'img=$(docker inspect dsv41-head --format "{{.Config.Image}}"); \
  docker run --rm --gpus all --entrypoint python3 -v /tmp/gpu-state-probe.py:/probe.py:ro \
  $img /probe.py --seconds 6'
```

Two pitfalls that cost time here: the recipe image's **entrypoint is a launcher**, so
`--entrypoint python3` is required or it prints its own usage and exits 1; and the workers hold
their own image `dsv41-4x-spark:local` under container `dsv41-worker`, so take the image from
the container you are testing.

## Use it before believing a slowdown

If the lane's tok/s drops, run this on all four nodes **before** touching any config. A node
stuck at 66–80 GB/s is not a tuning problem, and no amount of γ/KV/scheduler work will fix it.
Sequence: probe → if any node is slow, restart/quiesce the engine on that node (their data
shows all four ran fast with the GPU clocks held and no resident process) → re-probe → only then
look at config. If a node stays slow with AC power cycling ineffective, the firmware power-limit
path (pl1/syspl1 via spark_hwmon) is the open lead.