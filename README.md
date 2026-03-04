# hearing-aid-dsp

RTL hearing-aid DSP implementation and verification flow. The repository includes the RTL design, cocotb-based simulation tests, and paper evidence generation under `verification/`.

## Start Here

From repo root:

```bash
cd verification
source bootstrap_env.sh
python3 -m pip install -r requirements.txt
make -C sim SIM=verilator paper-signoff
```

## Documentation Map

- [verification/README.md](verification/README.md): operational runbook (prerequisites, commands, pass/fail interpretation, troubleshooting)
- [verification/docs/paper_signoff.md](verification/docs/paper_signoff.md): lock/push signoff procedure and artifact checks
- [verification/docs/test_matrix.md](verification/docs/test_matrix.md): complete verification coverage map and evidence boundaries

## Fast Commands

From repo root:

```bash
# L1 paper-core (DSM / HiFi)
make -C verification/sim SIM=verilator

# L3 paper-core hearing-aid tests
make -C verification/sim SIM=verilator hearing-aid

# Full paper flow
make -C verification/sim SIM=verilator paper-signoff
```
