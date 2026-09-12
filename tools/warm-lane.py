#!/usr/bin/env python3
"""Warm the A1-A4 TP4 lane after a cold start.

Two things make a freshly booted TP4 slow, both host-side and both cured by traffic:
  1. the Engram host cache (DSV41_CACHE_GIB=4/rank) starts empty and only fills through
     real lookups — cold, ranks read from disk (measured hit-rate 44-82% once warm);
  2. the page cache for the 23.6 GB/rank packed shards is gone after a reboot.
Measured effect: conc1 16 -> 34 tok/s and conc4 55 -> 68 once warm, no config change.

This script drives realistic decode traffic (distinct prompts, concurrency ramp) until the
engine's own throughput estimate stops improving, then exits. It NEVER touches config or
containers; worst case it is 2-3 minutes of load on an engine that was about to get it
from real traffic anyway. Safe to run by hand any time; the lane unit runs it once after a
fresh start (never on adopt).

Usage: warm-lane.py [--url URL] [--budget-s SECONDS]
"""
import argparse
import concurrent.futures as cf
import json
import subprocess
import time
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000"
MODEL = "deepseek-v4.1-flash"
TOPICS = [
    "a lighthouse keeper who stops counting the ships", "the last tram out of a rain-soaked city",
    "a cartographer who maps only sounds", "a bell that rings a year before it happens",
    "an orchard grown seven stories above the street", "the smell of a bakery at four in the morning",
    "a river that runs backwards once a decade", "the keeper of a museum of unfinished letters",
    "a train that only stops at stations nobody built", "the colour of the sky just before a hailstorm",
    "a clockmaker who repairs only stopped watches", "the sound a library makes when nobody is reading",
    "the tide line where two seas disagree", "a garden that grows only in the dark",
    "the last voice on a dead radio frequency", "a bridge that remembers every crossing",
]


def head_throughput(url):
    """Latest engine-reported decode throughput (tok/s), or None."""
    p = subprocess.run(["docker", "logs", "--since", "3m", "dsv41-head"],
                       text=True, capture_output=True, timeout=30)
    best = None
    for line in p.stdout.splitlines():
        if "gen throughput (token/s):" in line:
            try:
                best = float(line.split("gen throughput (token/s):")[1].split(",")[0])
            except (ValueError, IndexError):
                pass
    return best


def one(args):
    idx, level, rep, url = args
    prompt = (f"Write a vivid short story (about 500 words) about {TOPICS[idx % len(TOPICS)]}. "
              f"Prose only, no headings. Warmup {level}-{rep}-{idx}.")
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.7, "max_tokens": 256,
               "chat_template_kwargs": {"thinking": False}}
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        json.load(r)
    return time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--budget-s", type=int, default=240)
    args = ap.parse_args()

    t_start = time.time()
    levels = [4, 8, 8, 8, 8]   # ramp then sustained decode pressure
    plateau_hits = 0
    prev = None
    idx = 0
    for rep, level in enumerate(levels):
        if time.time() - t_start > args.budget_s:
            print("WARM_BUDGET_EXHAUSTED", flush=True)
            break
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=level) as ex:
            list(ex.map(one, [(idx + i, level, rep, args.url) for i in range(level)]))
        idx += level
        wall = time.time() - t0
        tp = head_throughput(args.url)
        print(f"rep={rep} level={level} wall={wall:.1f}s engine_tp={tp}", flush=True)
        if tp is not None and prev is not None and tp >= 0.97 * prev:
            plateau_hits += 1
            if plateau_hits >= 2:
                print(f"WARM_PLATEAU after {time.time() - t_start:.0f}s engine_tp={tp}", flush=True)
                return
        else:
            plateau_hits = 0
        prev = tp
    print(f"WARM_DONE after {time.time() - t_start:.0f}s", flush=True)


if __name__ == "__main__":
    main()