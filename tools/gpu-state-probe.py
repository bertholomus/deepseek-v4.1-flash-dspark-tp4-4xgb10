#!/usr/bin/env python3
"""Per-node memory-bandwidth discriminator for the GB10 "hidden slow state".

Why this exists
---------------
On GB10 (DGX Spark) the SM clock and the NVML throttle reasons do NOT distinguish the
healthy state from the slow state: both report ~2.17-2.5 GHz with throttle reasons 0x0.
What changes is achievable memory bandwidth — a decode-shaped GEMV runs at ~225 GB/s
healthy and ~66-80 GB/s in the slow state, while a pure device copy is unaffected.
Reported by Tech2Wild (MIT, tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark issue #1);
this is an independent implementation of that measurement, not a copy of their script.

Usage (inside a CUDA-capable container on the node under test):
    python3 gpu-state-probe.py [--seconds 6] [--json]

Exit: 0 fast, 10 slow, 11 mixed. Prints one summary line either way.
"""
import argparse
import json
import time

import torch

GB = 1024 ** 3
K = 5120
N = 16384
BYTES_PER_CALL = 2 * K * N * 2  # bf16 weight read + bf16 activation read


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    torch.cuda.set_device(0)
    w = torch.randn(K, N, dtype=torch.bfloat16, device="cuda")
    a = torch.randn(6, K, dtype=torch.bfloat16, device="cuda")
    name = torch.cuda.get_device_name(0)

    def timed(n):
        s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        s.record()
        for _ in range(n):
            a @ w
        e.record()
        e.synchronize()
        return s.elapsed_time(e) / n / 1000.0  # seconds per call

    for _ in range(20):  # warmup: clocks ramp, allocator settles
        a @ w
    torch.cuda.synchronize()

    vals, t0 = [], time.time()
    while time.time() - t0 < args.seconds:
        vals.append(BYTES_PER_CALL / timed(8) / 1e9)  # GB/s
        time.sleep(0.02)

    s = sorted(vals)
    res = {
        "gpu": name,
        "n": len(s),
        "gbps_min": round(s[0], 1),
        "gbps_p10": round(s[len(s) // 10], 1),
        "gbps_p50": round(s[len(s) // 2], 1),
        "gbps_max": round(s[-1], 1),
        "spread_p50_over_max": round(s[len(s) // 2] / s[-1], 2),
    }
    p50 = res["gbps_p50"]
    if p50 >= 150:
        res["state"] = "fast"
        code = 0
    elif p50 <= 110:
        res["state"] = "slow"
        code = 10
    else:
        res["state"] = "mixed"
        code = 11

    if args.json:
        print(json.dumps(res))
    else:
        print(f"GPU_STATE_PROBE gpu={name!r} state={res['state']} "
              f"p50={p50} GB/s (min {res['gbps_min']} max {res['gbps_max']}) n={res['n']}")
    raise SystemExit(code)


if __name__ == "__main__":
    main()