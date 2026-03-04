# Paper Signoff Workflow

## Scope

This flow produces objective, simulation-based evidence for the paper.
It does not cover human-subject evaluation.

## Preflight

Before starting a lock run:
1. Ensure working tree is clean or intentionally staged.
2. Ensure verification environment is active.
3. Ensure enough free disk space for regenerated plots/WAV artifacts.

Environment setup:

```bash
cd verification
source bootstrap_env.sh
python3 -m pip install -r requirements.txt
```

## Main Signoff Command

```bash
make -C sim SIM=verilator paper-signoff
```

`paper-signoff` runs:
1. data + RIR preparation
2. literature collect run + baseline build
3. L1 (DSM), L2 (filterbank), L3 (hearing-aid core), L4 (intrinsic tau), L5 (literature proxy)
4. figure generation
5. clock note/check
6. artifact manifest

## Lock Snapshot Sequence (Exact)

Run this sequence before push/lock:

```bash
make -C verification/sim clean
make -C verification/sim SIM=verilator paper-signoff
make -C verification/sim SIM=verilator paper-proxy-capture
make -C verification/sim paper-proxy-validate
make -C verification/sim paper-manifest
```

## Success Markers

Check these markers in terminal output:
- `[paper] === SIGNOFF DONE ===`
- `[manifest] Wrote ... -> .../verification/reports/paper/manifest.json`

Proxy step behavior:
- `paper-proxy-validate` may print `[proxy-rtl] FAIL` under default non-blocking policy.
- strict mode requires:
  - `PAPER_PROXY_VALIDATE_ENFORCE=1`

## Proxy Coherence Checks

Required package coherence after capture/validate:
1. `verification/reports/paper/proxy_rtl_capture_manifest.json`
   - `entries = 18`
   - `case_spec = "1-18"`
2. `verification/reports/paper/rtl_cases/`
   - exactly 54 WAV files (`mix/clean/noise` × 18)
3. `verification/reports/paper/proxy_rtl_correlation.{json,md}`
   - refreshed after the latest capture

## Optional Proxy-vs-RTL Commands

Manual package commands:

```bash
make -C sim SIM=verilator paper-proxy-capture
make -C sim paper-proxy-validate
```

## Key Artifacts

- `verification/reports/HA_*.json`
- `verification/reports/paper/baseline_comparison.json`
- `verification/reports/paper/clock_correlation.{json,md}`
- `verification/reports/paper/proxy_rtl_capture_manifest.json`
- `verification/reports/paper/proxy_rtl_correlation.{json,md}`
- `verification/reports/paper/manifest.json`

## Claim Boundaries

- L1-L4 are RTL electroacoustic evidence.
- HA-7..HA-10 are proxy-model objective evaluations.
- HA-12 values are digital-domain proxies (dBFS), not SPL coupler measurements.
- 100 MHz simulation results are simulation-reference unless supported by implementation timing evidence (for example `fpga_synthesis.json`).
