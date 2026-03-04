# Proxy-vs-RTL Correlation (18-case Package)

- Generated UTC: `2026-03-04T20:57:18.567117+00:00`
- RTL manifest: `verification/reports/paper/proxy_rtl_capture_manifest.json`
- Cases: `18` / `18`
- Overall pass: `FAIL`

- Allow partial matrix: `no`
- Missing cases: `0`

## Scope Note

- This report compares proxy outputs with captured RTL outputs on the same case matrix.
- It is a correlation diagnostic, not a replacement for L1-L4 RTL electroacoustic verification.

## Acceptance Summary

| Metric | N | Bias | MAE | Spearman rho | Pass |
|---|---:|---:|---:|---:|---|
| haspi_v2 | 18 | 0.0102 | 0.0848 | 0.9298 | FAIL |
| hasqi_v2 | 18 | 0.0168 | 0.0264 | 0.9278 | PASS |
| output_snr_db | 18 | 0.7008 | 0.8838 | 0.8473 | FAIL |

## Failure Details

- haspi_v2: MAE=0.0848 > 0.0700
- output_snr_db: Spearman rho=0.8473 < 0.8500
