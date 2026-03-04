# Threshold Lock Provenance

- Generated UTC: `2026-03-02T23:12:44.476928+00:00`
- Mode: `collect_calibration_refresh`
- Threshold config: `verification/config/paper_thresholds.yaml`
- Threshold config SHA256: `e0d196480a319a646575f3783221873b938d8a33fadc84ed18e6cc1293370800`
- Reports used: `4` / `4`

| Metric | Direction | Source file(s) | Selected source | Summary mean/std | Proposed (pre-clamp) | Guard | Guard value | Clamp applied | Final locked |
|---|---|---|---|---:|---:|---|---:|---|---:|
| dr_db | higher_is_better | verification/reports/HA_10_modulation_metrics.json | verification/reports/HA_10_modulation_metrics.json: proposed_thresholds | 7.099721 / 2.540366 | 2.018988 | floor | 5.000000 | yes | 5.000000 |
| ecr | lower_is_better | verification/reports/HA_10_modulation_metrics.json | verification/reports/HA_10_modulation_metrics.json: proposed_thresholds | 0.841217 / 0.439764 | 1.720745 | ceiling | 1.200000 | yes | 1.200000 |
| haspi_v2 | higher_is_better | verification/reports/HA_8_haspi_hasqi.json, verification/reports/HA_9_reverb_eval.json | verification/reports/HA_9_reverb_eval.json: proposed_thresholds | 0.327683 / 0.326690 | -0.325697 | floor | 0.300000 | yes | 0.300000 |
| hasqi_v2 | higher_is_better | verification/reports/HA_8_haspi_hasqi.json, verification/reports/HA_9_reverb_eval.json | verification/reports/HA_9_reverb_eval.json: proposed_thresholds | 0.134246 / 0.098678 | -0.063110 | floor | 0.120000 | yes | 0.120000 |
| output_snr_db | higher_is_better | verification/reports/HA_7_output_snr.json, verification/reports/HA_9_reverb_eval.json | verification/reports/HA_9_reverb_eval.json: proposed_thresholds | -1.252443 / 2.390283 | -6.033008 | floor | -2.000000 | yes | -2.000000 |
| output_snr_slope_db_per_db | higher_is_better | verification/reports/HA_7_output_snr.json | verification/reports/HA_7_output_snr.json: proposed_thresholds | 0.334298 / 0.000036 | 0.334226 | floor | 0.300000 | no | 0.334226 |
