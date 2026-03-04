# Clock Correlation Note

## Required Claim Boundary

1. 100 MHz results are simulation-reference for PDM-path tests.
2. FPGA timing evidence is Fmax-based implementation evidence.
3. No claim of 100 MHz silicon operation is made unless timing closure at 100 MHz is demonstrated.

## Artifact Snapshot

- test1_thdn_results_analog: `100000000 Hz`, context `simulation-reference`
- HA_4_thd: `100000000 Hz`, context `simulation-reference`
- HA_12_ospl_ein: `100000000 Hz`, context `audio-domain`
- FPGA Fmax from synthesis/PnR ingest: `67.16 MHz`
