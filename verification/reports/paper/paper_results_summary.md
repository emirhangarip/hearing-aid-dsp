# Paper Results Summary

Date: 2026-02-28  
Run: `make -C verification/sim SIM=verilator paper-signoff`

## Result Overview

- L1-L4: PASS
- L5 proxy suite: PASS (proxy-model gates)
- Signoff flow: PASS

## Measured Highlights

| Layer | Scenario | Measured | Gate / reference | Status |
|---|---|---|---|---|
| L1 | THD+N sweep (analog path) | best `-95.10 dBc`, worst `-63.05 dBc` | `test_1`: per-frequency analog THD+N limits + core guard (`THDN_LIMITS_ANALOG`, `THDN_LIMIT_CORE`) | PASS |
| L1 | CCIF IMD | `-165.40 dBr` | `test_3`: 1 kHz diff-product `< -80 dBr` | PASS |
| L1 | SMPTE IMD | worst sideband `-39.82 dBr` | `test_4`: worst sideband `< -35 dBr` (architectural limit-cycle regime) | PASS |
| L1 | Dynamic range | `100.6 dB` | `test_5`: dynamic range `>= 80 dB`, DC within limit | PASS |
| L2 | Filterbank THD+N @ 1 kHz | `-28.97 dBc` | `test_L2_2`: THD+N `< -25 dBc` | PASS |
| L3 | HA-2 attack/release | `tau_atk=13.39 ms`, `tau_rel=96.99 ms` | `HA-2`: settle + fit quality + tau error bounds (`<=20%` in SPEC gate) | PASS (with NOTE in log) |
| L3 | HA-11 latency | `51 samples` (`1.0625 ms`) | `HA-11`: latency `< 20 ms` | PASS |
| L3 | HA-12 OSPL/EIN proxy | OSPL range `5.81 dB`, EIN(in-ref) `-218.06 dBFS` | `HA-12`: OSPL range `<= 20 dB`, EIN `<= -70 dBFS` | PASS |
| L4 | WDRC intrinsic tau | `tau_atk=4.996 ms`, `tau_rel=95.807 ms` | `WDRC-INTRINSIC`: tau error `<= 20%`, fit `R²>=0.98` | PASS |
| L5 | HA-8 HASPI/HASQI | HASPI `0.4073`, HASQI `0.1832` | Signoff thresholds from locked `paper_thresholds.yaml` (proxy-model gate) | PASS |
| FPGA | Post-PnR synthesis ingest | Fmax `67.16 MHz`, LUT4 `2782`, DSP `16` | Informational implementation evidence (not cocotb gate) | PASS |

## Evidence Files

- L1/L2/L3/L4: `verification/reports/HA_*.json`, `verification/reports/test*.json`, plots/logs
- L5 proxy: `verification/reports/HA_7_output_snr.json`, `HA_8_haspi_hasqi.json`, `HA_9_reverb_eval.json`, `HA_10_modulation_metrics.json`
- FPGA: `verification/reports/fpga_synthesis.json`

## Claim Boundaries

- RTL performance claims should use L1-L4 evidence.
- HA-7..HA-10 are proxy-model objective evaluations.
- HA-12 values are digital-domain proxies (dBFS), not SPL coupler measurements.
- Simulation clock settings are not silicon timing closure evidence by themselves.
