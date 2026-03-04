# Hearing Aid DSP Verification

Documentation:
- `verification/docs/paper_signoff.md`
- `verification/docs/test_matrix.md`

## Quick Start

```bash
cd verification
source bootstrap_env.sh
make -C sim SIM=verilator paper-signoff
```

Clock claim boundary:
- 100 MHz PDM results are simulation-reference unless explicitly tagged silicon-correlated.
- FPGA implementation timing claims come from `reports/fpga_synthesis.json` (`fmax_mhz`).

## Core Targets

- `make -C sim help-verification` : project-specific target list
- `make -C sim` : L1 paper-core (`test_1/3/4/5`)
- `make -C sim filterbank` : L2 filterbank checks
- `make -C sim hearing-aid` : L3 paper-core HA checks
- `make -C sim wdrc-intrinsic` : intrinsic WDRC tau check
- `make -C sim ha-literature` : HA-7..HA-10 proxy-model objective scenarios
- `make -C sim paper-baselines` : unprocessed/NAL-R/WDRC same-dataset comparison
- `make -C sim paper-proxy-capture` : generate 18-case RTL outputs + proxy manifest
- `make -C sim paper-proxy-validate` : 18-case proxy-vs-RTL correlation package
- `make -C sim paper-clock` : write + validate clock claim artifacts
- `make -C sim paper-clock-check` : enforce clock metadata/claim consistency
- `make -C sim validate-suite` : methodology self-validation (`verification/tb/validate_suite.py`)
- `make -C sim paper-ingest-fpga FPGA_SYN_RPT=... FPGA_PNR_RPT=... [FPGA_PWR_RPT=...] [FPGA_NOTES=...]` : ingest FPGA implementation evidence

Evidence split:
- L1-L4 are RTL-driven verification.
- HA-7..HA-10 are proxy-model evaluations (no RTL signal stimulation in those tests).

Proxy capture window controls:
- `PROXY_CAPTURE_MAX_SECONDS` (default `0.4`)
- `PROXY_CAPTURE_START_SECONDS` (default `0.0`)
- `PROXY_CAPTURE_AUTO_SPEECH` (default `1`; when start is `0.0`, auto-picks speech-active window)
- `PROXY_CAPTURE_CASES` (default `1-18`, supports `1-6`, `1,4,7`, `*`)
- Example (more audible segment): `make -C sim SIM=verilator PROXY_CAPTURE_MAX_SECONDS=2.0 PROXY_CAPTURE_START_SECONDS=2.0 paper-proxy-capture`
- Example (faster subset): `make -C sim SIM=verilator PROXY_CAPTURE_CASES=1-3 paper-proxy-capture`
- Example (force literal start; disable auto speech window): `make -C sim SIM=verilator PROXY_CAPTURE_AUTO_SPEECH=0 PROXY_CAPTURE_START_SECONDS=0.0 paper-proxy-capture`
- Partial-manifest validation (optional): `make -C sim PROXY_VALIDATE_ALLOW_PARTIAL=1 paper-proxy-validate`

## Full / Optional Targets

- `make -C sim hifi-full`
- `make -C sim hearing-aid-full`
- `make -C sim selftest`

## Artifact Notes

- Primary evidence files are under `verification/reports/` and are tracked for paper snapshots.
- Proxy capture evidence includes 54 RTL WAV files in `verification/reports/paper/rtl_cases/`
  (18 cases × `mix/clean/noise`).

## Git Artifact Policy

- Tracked: `verification/reports/**` (JSON/MD/PNG/PDF/WAV paper artifacts).
- Ignored: `verification/.venv/`, `verification/data/`, simulator build/cache outputs.
- Regenerate tracked evidence with:
  `make -C sim SIM=verilator paper-signoff && make -C sim SIM=verilator paper-proxy-capture && make -C sim paper-proxy-validate && make -C sim paper-manifest`
