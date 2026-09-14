# Upstream issue drafts — rights-safe, ready to file

Two engine defects found while tuning this recipe, each reduced to a minimal reproduction from a
clean checkout with the exact stop sites in the pinned build. Written to be filed as-is: no site
identifiers, no private paths, no fleet details.

| file | defect | why it matters here |
|---|---|---|
| `ISSUE-1-sps-profiler-simulate-capture-conflict.md` | `dspark_sps_profiler.py` requires `SGLANG_SIMULATE_ACC_LEN=1.0` on every DP rank and refuses `--disable-cuda-graph`, but at exactly that configuration the verify epilogue + capture hook install with **no simulate guard** → the rank-desync capture hang on multi-node TP4 | without an admissible profiling configuration the verify-budget / compact-SPS pricing lever cannot be measured at all |
| `ISSUE-2-cap-accept-confidence-head-capture-width.md` | the `cap-accept` path feeds a 4096-wide hidden to a confidence head built at 5376 (`hidden_size 5120` + `dspark_markov_rank 256`) → shape crash during graph capture | `cap-accept` is the mechanism that would let the engine commit a variable number of drafted tokens instead of a fixed γ — it is the structural fix for the verify-window waste we measured at long context |

Status: **drafted, not filed.** The drafts are versioned here so the finding is public either way;
filing them into the upstream tracker is the maintainer's/owner's call.

Context for both: our lane is DeepSeek-V4.1-Flash, 4× GB10 (SM121), TP4, SGLang `dev-dsv41`,
DSpark speculative decoding. Both drafts state what we ran, what happened, the exact file/line
where it stops, and what we could not test as a result — no speculation about intent.