# PROVENANCE AUDIT — does the published recipe actually reproduce the live lane?

**Date:** 2026-09-13 · **Trigger:** the v1.1.4 release proof (applying the published patches to a
clean checkout of `e59e6eb` and diffing `start.sh` / `Dockerfile` against production) covered the
*patch* surface. It did not cover the **environment file**, which is where most of the recipe's
deviations actually live. This audit closes that gap.

## Method (repeatable, value-for-value — not a text diff)

The live `.env.tp4` and the recipe's `env.tp4` are deliberately *not* byte-identical: the
published file carries curated comments explaining each deviation, the live file carries
operator notes and restore breadcrumbs. A text diff therefore produces noise and hides real
divergence. Compare **parsed key=value pairs with trailing comments stripped**:

```bash
# 1. live values, from the head node
ssh spark1 'cat ~/ai/recipes/DeepSeek-v4.1-Flash-DGX-Sparks/.env.tp4' > /tmp/live-env.tp4

# 2. value-for-value comparison against the recipe's canonical env.tp4
python3 - <<'EOF'
import re
def kv(p):
    d = {}
    for l in open(p, encoding="utf-8", errors="ignore"):
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", l)
        if m:
            d[m.group(1)] = re.sub(r"\s+#.*$", "", m.group(2)).strip()
    return d
repo, live, snap = kv("env.tp4"), kv("/tmp/live-env.tp4"), kv("env.tp4.as-deployed-20260913")
for k in sorted(set(repo) | set(live)):
    r, l = repo.get(k, "<absent>"), live.get(k, "<absent>")
    print(("DIFF " if r != l else "ok   ") + f"{k}: recipe={r!r} live={l!r}")
print("frozen snapshot == live:", snap == live)
EOF
```

## Result: two real gaps, both now fixed

1. **`EXTRA_SGLANG_ARGS` was missing the three durable-fix flags.** Production runs
   `--enable-metrics --enable-metrics-for-all-schedulers --enable-mixed-chunk`; the published
   file stopped after `--enable-deepseek-v4-fp4-indexer`. The durable-fix window result
   (`docs/DURABLEFIX-RESULT.md`) documents all three as live and two of them as *measured wins*,
   so anyone building from the published recipe would have got a lane with **no engine metrics**
   and **without mixed-chunked prefill** — i.e. a quieter, slower lane than the one every number
   in `EVIDENCE.md` was measured on.
2. **The four DSpark instrumentation/experiment variables were absent.** Production sets
   `SGLANG_DSPARK_FOLDED_PROPOSAL=1`, `SGLANG_DSPARK_BLOCK_ACCEPT_ONLINE_INTERVAL=60`,
   `SGLANG_DSPARK_ENABLE_SPS_RECORD=0`, `SGLANG_SIMULATE_ACC_LEN=-1`. Patch `0002` exists
   precisely to forward these, and `EVIDENCE.md` §10 rests on the folded-proposal value — but the
   recipe never published the values themselves, so the patch and its variables were documented
   separately and never joined up in the shipped file.

Both are fixed in `env.tp4` for v1.1.5. **No production change was needed: production already
runs these values** — the defect was in the published description of it, which is exactly the
kind of defect this repository exists not to have.

## Standing rule (add to the release checklist)

Every release that touches the formula must run this comparison and state the result in the
CHANGELOG. The published patch set reproducing `start.sh` and `Dockerfile` is *necessary but not
sufficient* — the environment file is part of the formula.

## Verified equal at this audit

- `env.tp4.as-deployed-20260913` is value-identical to the live file (`snap == live: True`), so
  the frozen snapshot is a trustworthy rollback/provenance anchor (comments differ, values do
  not).
- With the two gaps fixed, `env.tp4` (canonical) and the live file agree on every key; the only
  remaining differences are comments.
- Nothing in this audit touched the running lane: it is read-only on the head's file and
  compares text.