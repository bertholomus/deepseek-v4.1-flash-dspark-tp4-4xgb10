# NOTICE — provenance, attribution, licensing

## What this repository contains

Configuration, patches, systemd units, operational scripts and measured evidence for running
**DeepSeek-V4.1-Flash on 4× NVIDIA DGX Spark (GB10), TP4, under SGLang**.

**No model weights are included or redistributed.** Nothing here is a download of the model,
the container image or any NVIDIA component.

## Licensing of this repository

**GNU AGPL-3.0-or-later** — see `LICENSE`.

This repository is a derivative work of `MiaAI-Lab/DeepSeek-v4.1-Flash-DGX-Sparks`
(AGPL-3.0-or-later, Copyright (C) 2026 zurih), whose `.env.tp4.example`, `start.sh`, `boot.py`
and helper tooling this recipe is built on. Accordingly it is offered under the same licence,
and the upstream MIT notice for the underlying skeleton is retained in `LICENSE.upstream-MIT`
as that licence requires. Note AGPL-3.0 §13: if you run a modified version of this as a
network service, you must offer the corresponding source to your users.

## Upstream credits

- **Launcher recipe** — `MiaAI-Lab/DeepSeek-v4.1-Flash-DGX-Sparks` @ `e59e6eb`,
  AGPL-3.0-or-later, Copyright (C) 2026 zurih. Base for `.env.tp4`, `start.sh`, `boot.py`
  and helper tooling.
- **Recipe skeleton** — `0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000`, MIT, Copyright (c) 2026
  0xSero (via the upstream repository); notice retained in `LICENSE.upstream-MIT`.
- **SGLang** — `lmsysorg/sglang`, image tag `dev-dsv41`; Apache-2.0, see `LICENSE.sglang`.
- **Model** — DeepSeek-V4.1-Flash, checkpoint `dba1be0a`; MIT (the licence ships inside the
  checkpoint). Not redistributed here.
- **Hardware / fabric** — NVIDIA DGX Spark (GB10) and ConnectX-7; NCCL is used from the
  container image (2.28.3 as shipped — the `nccl-2.30.7` overlay some recipes document is
  **not** vendored here and is not used).
- **GB10 slow-state measurement** — the fast/slow memory-bandwidth diagnostic in
  `docs/4-NODE-STATE.md` and `tools/gpu-state-probe.py` follows the finding published by
  `tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark` (MIT, issue #1); the implementation here is
  independent, and no code from that project is copied.

## What is ours (BertholomusAI)

- The measured deviations from the upstream example (README §1) and the numbers behind them.
- The per-node active-IB-HCA discovery patch, `patches/0001-per-node-active-ib-hca.diff`
  (offered upstream as PR #10).
- The in-image guard patch, `patches/runtime/patch_encoding_dsv41.py`.
- The lane's systemd units, watchdog, warmer and status tooling (`units/`, `tools/`).
- The measurement corpus and operational runbooks (`docs/`).

## Address placeholders

Every host, address and client name in this repository is a placeholder: hosts `spark1`…`spark4`,
fabric/LAN addresses `10.0.0.x` and `10.0.20.x`, service address `100.64.0.1`. Substitute your
own. The fabric addresses in particular are only meaningful for your own cabling — see
`docs/OPERATIONS.md` for how each value was chosen.

## Secrets

Nothing here requires credentials to read. The served endpoint is configured **without auth**
by design (`.env.tp4` carries no API key) — this is intentional for a private fabric, but if you
expose the service beyond your own network, put authentication in front of it.