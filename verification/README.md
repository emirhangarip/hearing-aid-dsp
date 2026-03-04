# Hearing Aid DSP Verification

This directory contains the simulation and paper evidence workflow.

Related docs:
- `verification/docs/paper_signoff.md`
- `verification/docs/test_matrix.md`

## Prerequisites

Required:
- Python 3 + `pip`
- Verilator in PATH
- Linux-like shell environment

Setup from repo root:

```bash
cd verification
source bootstrap_env.sh
python3 -m pip install -r requirements.txt
make -C sim help-verification
```

## Canonical Flows

### Quick Confidence (RTL smoke + core behavior)

```bash
make -C sim SIM=verilator
make -C sim SIM=verilator hearing-aid
```

### Paper Signoff (full flow)

```bash
make -C sim SIM=verilator paper-signoff
```

### Proxy Package Only (manual diagnostics)

```bash
make -C sim SIM=verilator paper-proxy-capture
make -C sim paper-proxy-validate
```

## Runtime Expectations

Typical ranges on a desktop CPU:
- `paper-signoff`: multi-hour run (commonly 4-7 hours)
- `paper-proxy-capture` (18 cases, defaults): about 45-70 minutes
- `paper-proxy-validate`: short run (usually under 5 minutes)
- `validate-suite`: short run (usually under 1 minute)

## Result Interpretation

- `paper-signoff` is successful when the log ends with:
  - `[paper] === SIGNOFF DONE ===`
- `paper-proxy-validate` is non-blocking by default:
  - it may print `[proxy-rtl] FAIL` and still continue as INFO policy
- strict proxy validation:
  - set `PAPER_PROXY_VALIDATE_ENFORCE=1`
  - then any proxy validation failure exits non-zero

Evidence boundary:
- L1-L4 are RTL-driven verification.
- HA-7..HA-10 are proxy-model evaluations (DUT instantiated but not driven).

## Core Targets

- `make -C sim help-verification`
- `make -C sim` (L1 paper-core: `test_1/3/4/5`)
- `make -C sim filterbank`
- `make -C sim hearing-aid`
- `make -C sim wdrc-intrinsic`
- `make -C sim ha-literature`
- `make -C sim paper-signoff`
- `make -C sim paper-proxy-capture`
- `make -C sim paper-proxy-validate`
- `make -C sim validate-suite`
- `make -C sim paper-ingest-fpga FPGA_SYN_RPT=... FPGA_PNR_RPT=... [FPGA_PWR_RPT=...] [FPGA_NOTES=...]`

## Proxy Capture Controls

- `PROXY_CAPTURE_CASES` (default `1-18`, supports `1-6`, `1,4,7`, `*`)
- `PROXY_CAPTURE_MAX_SECONDS` (default `0.4`)
- `PROXY_CAPTURE_START_SECONDS` (default `0.0`)
- `PROXY_CAPTURE_AUTO_SPEECH` (default `1`)
- `PROXY_VALIDATE_ALLOW_PARTIAL` (default `0`)

Examples:

```bash
# Faster subset
make -C sim SIM=verilator PROXY_CAPTURE_CASES=1-3 paper-proxy-capture

# Longer audible segment
make -C sim SIM=verilator PROXY_CAPTURE_MAX_SECONDS=2.0 PROXY_CAPTURE_START_SECONDS=2.0 paper-proxy-capture

# Force literal start (disable auto speech window)
make -C sim SIM=verilator PROXY_CAPTURE_AUTO_SPEECH=0 PROXY_CAPTURE_START_SECONDS=0.0 paper-proxy-capture

# Allow validating a partial manifest
make -C sim PROXY_VALIDATE_ALLOW_PARTIAL=1 paper-proxy-validate
```

## Common Failures and Fixes

### Missing proxy manifest

Symptom:
- `paper-proxy-validate` reports missing `proxy_rtl_capture_manifest.json`

Fix:

```bash
make -C sim SIM=verilator paper-proxy-capture
```

### Partial or stale proxy capture set

Symptom:
- mismatch between expected and available cases

Fix:

```bash
make -C sim SIM=verilator PROXY_CAPTURE_CASES=1-18 paper-proxy-capture
```

Then verify:
- manifest has 18 entries
- `verification/reports/paper/rtl_cases/` has 54 WAV files

### Environment not active

Symptom:
- module/tool import errors (`cocotb`, `clarity`, etc.)

Fix:

```bash
cd verification
source bootstrap_env.sh
python3 -m pip install -r requirements.txt
```

### WAV playback sounds wrong or silent

Cause:
- proxy capture WAV outputs are 32-bit PCM

Use:
- `ffplay`, VLC, or Audacity (instead of limited default players)

## Artifact Policy

- Tracked: `verification/reports/**` (JSON/MD/PNG/PDF/WAV artifacts)
- Ignored: `verification/.venv/`, `verification/data/`, sim build/cache outputs
- Proxy capture evidence: 54 RTL WAV files in `verification/reports/paper/rtl_cases/` (`mix/clean/noise` × 18)

Regenerate tracked evidence:

```bash
make -C sim SIM=verilator paper-signoff
make -C sim SIM=verilator paper-proxy-capture
make -C sim paper-proxy-validate
make -C sim paper-manifest
```
