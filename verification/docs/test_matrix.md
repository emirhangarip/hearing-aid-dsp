# Verification Matrix

This is the practical inventory of verification in this repository.

## Layers and Targets

| Layer | Module / Target | Scenarios | Drives RTL | Primary Evidence Type |
|---|---|---|---|---|
| L1 | `test_hifi_suite.py` / `make -C verification/sim` | `test_1`, `test_3`, `test_4`, `test_5` (paper-core) + `test_2/6/7` (legacy) | Yes (`ds_modulator`) | DSM distortion and dynamic-range evidence |
| L2 | `test_filterbank_suite.py` / `make -C verification/sim filterbank` | `test_L2_1`, `test_L2_2` | Yes (`hearing_tdm_pdm_wrap`) | Filterbank response and THD evidence |
| L3 | `test_hearing_aid_suite.py` / `make -C verification/sim hearing-aid` | `HA-1`, `HA-2`, `HA-4`, `HA-5`, `HA-6`, `HA-11`, `HA-12` (+ `HA-3` optional) | Yes (`hearing_tdm_pdm_wrap`) | Compression, distortion, saturation, and latency evidence |
| L4 | `test_wdrc_intrinsic_suite.py` / `make -C verification/sim wdrc-intrinsic` | `test_WDRC_intrinsic_tau` | Yes (`wdrc_intrinsic_wrap`) | Intrinsic WDRC time-constant evidence |
| L5 | `test_ha_literature_suite.py` / `make -C verification/sim ha-literature` | `HA-7`, `HA-8`, `HA-9`, `HA-10` | No (proxy-model path) | Objective speech metrics from proxy model |
| Meta | `validate_suite.py` / `make -C verification/sim validate-suite` | DSP analyzer checks, methodology checks, mutation checks | No (methodology validation) | Measurement-pipeline validity evidence |

## Proxy-vs-RTL Package

| Step | Command | Purpose | Drives RTL |
|---|---|---|---|
| Capture | `make -C verification/sim SIM=verilator paper-proxy-capture` | Generate RTL speech outputs (`mix/clean/noise`) + manifest | Yes |
| Validate | `make -C verification/sim paper-proxy-validate` | Correlate proxy metrics vs captured RTL outputs | No (offline analysis) |

Capture controls:
- `PROXY_CAPTURE_CASES` (default `1-18`)
- `PROXY_CAPTURE_MAX_SECONDS` (default `0.4`)
- `PROXY_CAPTURE_START_SECONDS` (default `0.0`)
- `PROXY_CAPTURE_AUTO_SPEECH` (default `1`)

Validation controls:
- `PROXY_VALIDATE_ALLOW_PARTIAL` (default `0`)
- `PAPER_PROXY_VALIDATE_ENFORCE` (default `0`)

## Claim Mapping

- RTL electroacoustic and signal-quality claims map to L1-L4.
- Objective literature-style outcome trends map to L5 (proxy-model).
- `paper-proxy-capture` is the RTL-on-speech artifact generator.
- `paper-proxy-validate` is a proxy-vs-RTL agreement check on captured artifacts.

## Meta-Validation Role

`validate-suite` validates the measurement methodology and mutation-detection behavior of the verification pipeline. It does not replace RTL functional verification in L1-L4.

## Out-of-Scope Claims

- Human-subject clinical efficacy is out of scope.
- HA-12 values are digital-domain proxy values (dBFS), not coupler SPL measurements.
