# WINDOW-FAIRGO — the last unarmed levers, before we publish (DSV4.1-Flash TP4)

Window id: `window-fairgo-<stamp>` · host `spark1` · service `deepseek-v41-flash-a1a4-sglang-tp4.service`
Runner: `/tmp/window/window-fairgo.sh` · Probe: `$RT/fairgo-probe.py` (= `gamma-ctx-probe.py` + `--salt`)
Written **before** launch; the rule below is fixed and the window may not be re-interpreted after the fact.

## Why this window exists

the operator: "before we publish our updates and findings let's give it a fair go here." The recipe is at the
optimum of what we have *measured*, but three things had never been fairly trialed on this lane.
Two more were closed at source level tonight and cost no boot:
- `speculative_accept_threshold_single/_acc` (live-updatable) are only consumed by the **dflash** and
  **eagle** speculative paths; `SPEC_ALGO=DSPARK` never reads them → no adaptive-depth knob there.
- `SGLANG_DSPARK_FP32_LM_HEAD` (off) is a *precision* increase at speed cost — not our direction.

## Arms (one change each; everything else at the live production config: γ=3, fused-off, fcfs)

| arm | change | why it is a fair candidate |
|---|---|---|
| **C1** | none — live lane, no reboot | opening control |
| **G2** | `DSPARK_BLOCK_SIZE=2` | live traffic at 123 k ctx: `accept len 2.20–2.55`, `accept rate 0.40–0.52` ⇒ only 1.2–1.5 of 3 drafted positions are accepted at depth, so the 3rd position is mostly wasted verify work. Depth-adaptive γ was never fairly trialed: the 2026-09-12 closure assumed short-prompt acceptance (3.0–3.5 of 4), which does not hold at depth. |
| **F1** | `SGLANG_DSPARK_OPT_FUSED_GREEDY_MARKOV=1` | engine default is **False** (unarmed, and absent from our env file): fuses the greedy Markov sampling row inside the captured decode graph. Quality-neutral for greedy rows — the gauntlet must show byte-identical outputs. |
| **L** | `--schedule-policy lpm` | this build's default is `fcfs` (verified in `server_args.py`), not `lpm`; `lpm` is the cache-aware policy and our workload is multi-turn over a long cached trunk. Small ceiling (cache hit already ~95.9%) but never trialed. |
| **C2** | control again: full env restored + reboot | closing control **and** the restore boot (so a null window costs no extra boot) |

## Pre-registered decision rule

- `P8/P32/P96` = the probe's PRIMARY (prose+code median decode tok/s).
- Fleet-weighted score `W = 0.20·P8 + 0.30·P32 + 0.50·P96` — weights from the fleet's own context
  distribution (p50 ≈ 52 k, p90 ≈ 148 k → long-context dominant).
- Control reference per size = **mean(C1, C2)** (bracketing controls, one at each end).
- **Drift flag:** `|C2 − C1| > 15%` at any size ⇒ the window is drift-limited and an arm must clear the
  bar against **both** controls at that size.
- **ADOPT** an arm iff **all** of: (a) `W_arm ≥ 1.05 × W_ctrl`; (b) every size `≥ 0.92 × control`;
  (c) probe failures = 0 and the garbled-output gate is clean.
- Multiple qualifiers → highest `W`. **No qualifier → live config unchanged.**

## Measurement hygiene (what tonight taught us)

Tonight's window ended with a SETTLE arm reading 18.5 tok/s against a 10.6 control *in the same
window*: the packed/engram prefix cache survives reboots, so later arms silently inherited warm
prefixes and arm order leaked into the numbers. Both windows' conclusions survived it (an arm that
loses despite being warmest loses a fortiori), but it must not leak into a decision window that can
*adopt*. Hence: every arm primes its **own** salted prefix (`--salt <arm>`), giving cold-equal footing
while keeping the in-arm prime→measure shape that matches real agentic traffic.

## Cost / safety

- 4 boots (G2, F1, L, C2) + 5 probes; worst case the lane is dark ≈ 2 h, latch raised throughout
  (`$RT/maintenance.latch`), agents fall back to buddy doors.
- Any boot/probe failure marks that arm unusable and the window continues.
- A probe that reports failures aborts the arm (per-call timeout 600 s; nothing above 96 k — the
  banked rule from the 2026-09-14 hang, `sgl-project/sglang#33549`).
- Rollback: `$W/.env.tp4.orig` is a byte copy; the runner restores it and reboots (arm C2), and the
  settle path only ever arms a label the rule selected. `cp` + one boot = rollback, as always.
- The window always ends on a verified healthy lane, latch dropped, env md5 before/after recorded.

## Outcomes we accept

- Any arm qualifies → arms for production, then a settle boot that re-verifies health, γ and the gate.
- Null → the recipe keeps the live config, and we publish with a full fair-go record: every
  remaining in-envelope lever now has a measured answer, and the residual gap is the two upstream
  engine defects (see `docs/upstream-issues/`).

## RESULT — window `window-fairgo-20260914T214105Z` (4 boots, 6 probes, ~92 min)

**Adopted: G2 (γ=2).** Rejected: F1 (`FUSED_GREEDY_MARKOV=1`, worse) and L (`--schedule-policy
lpm`, null against the warm control). Numbers and the drift analysis: `EVIDENCE.md` §13; arm logs,
metrics dumps and warm/gate logs in `~/dsv41-state/window-fairgo-20260914T214105Z/`.

| arm | config | 8k | 32k | 96k | W | verdict |
|---|---|---|---|---|---|---|
| C1 | γ=3 live (opening control) | 12.96 | 12.79 | 13.05 | 12.95 | — |
| **G2** | **γ=2** | **19.99** | **19.71** | **19.77** | **19.80** | **ADOPT** |
| F1 | `FUSED_GREEDY_MARKOV=1` | 14.27 | 12.62 | 13.12 | 13.20 | reject |
| L | `--schedule-policy lpm` | 18.22 | 18.16 | 18.23 | 18.21 | reject (null) |
| C2 | γ=3 restored (closing control) | 17.83 | 18.52 | 18.09 | 18.17 | — |
| SETTLE | γ=2 adopted, independent boot | 19.84 | 19.26 | 19.45 | 19.47 | confirms |

Drift flag fired (C2 − C1 = +37.6 / +44.8 / +38.6%) → the both-controls clause engaged. Against the
closing control: G2 **+12.1 / +6.4 / +9.3%**, L +2.4 / −1.9 / +0.8% (**null**), F1 −20.0 / −31.9 /
−27.5%. Gate: 6/6 arms `garbled=0`.

Adopted state verified by the settle boot: `gamma=2, verify_num_draft_tokens=3`, health 200,
watchdog healthy / 0 failures / not latched, env md5 `6add2ff5529e0d39b4e0bd7311e3a840` (rollback
`94a97d96738e8b1e0a4d4093c59010fa`, i.e. `env.tp4.as-deployed-20260913`).

Closed without a boot: `speculative_accept_threshold_single/_acc` are dflash/eagle-only — DSPARK
reads neither, so this build has no engine-provided adaptive-depth knob.