# hearing-aid-dsp

Verification docs:

- [verification/README.md](verification/README.md)
- [verification/docs/paper_signoff.md](verification/docs/paper_signoff.md)
- [verification/docs/test_matrix.md](verification/docs/test_matrix.md)

Quick paper flow:

```bash
cd verification
source bootstrap_env.sh
python3 -m pip install -r requirements.txt
make -C sim SIM=verilator paper-signoff
```

Fast default behavior:
- `make -C sim` runs paper-core HiFi scenarios.
- `make -C sim SIM=verilator hearing-aid` runs paper-core HA scenarios.
- Use `hifi-full` / `hearing-aid-full` to include optional legacy diagnostics.
