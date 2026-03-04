# Paper Signoff Workflow

## Scope

This flow produces objective, simulation-based evidence for the paper.
It does not include human-subject evaluation.

## Environment

```bash
cd verification
source bootstrap_env.sh
pip install -r requirements.txt
```

## Main Command

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

## Optional Proxy-vs-RTL Package

These targets are manual on purpose.

```bash
make -C sim SIM=verilator paper-proxy-capture
make -C sim paper-proxy-validate
```

- `paper-proxy-capture` drives RTL and writes case WAVs + manifest.
- `paper-proxy-validate` compares those RTL WAVs against proxy outputs.
- Default policy is non-blocking unless `PAPER_PROXY_VALIDATE_ENFORCE=1`.

## Pre-Push Lock Checklist

Run this exact sequence before a lock commit:

```bash
make -C verification/sim clean
make -C verification/sim SIM=verilator paper-signoff
make -C verification/sim SIM=verilator paper-proxy-capture
make -C verification/sim paper-proxy-validate
make -C verification/sim paper-manifest
```

Required coherence checks:
- `proxy_rtl_capture_manifest.json` has 18 entries (`case_spec=1-18`).
- `verification/reports/paper/rtl_cases/` contains 54 WAV files (18×`mix/clean/noise`).
- `proxy_rtl_correlation.{json,md}` is refreshed against the current capture manifest.

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
- 100 MHz simulation results are not silicon timing claims by themselves; timing claims come from implementation reports (for example `fpga_synthesis.json`).
