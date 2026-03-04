#!/usr/bin/env python3
"""
gen_unity_lut.py — Unity-gain WDRC LUT generator (Layer 2 pre-step)
=====================================================================
Writes 10 intop_wdrc_gain_lut_b*.mem files, each containing 1024
entries of 0x100000 (Q4.20 = 1.0 = unity gain).

These files bypass WDRC compression so that test_filterbank_suite.py
can measure filterbank reconstruction quality in isolation, without any
compression or gain introduced by the WDRC.

Must be run BEFORE the simulator launches, because the RTL reads the
.mem files during elaboration via $readmemh in wdrc_gain_ram.sv.

Usage
-----
    python3 verification/scripts/gen_unity_lut.py [output_dir]

    output_dir  Directory to write .mem files (default: current directory).
                Pass the Makefile sim working directory (SIM_DIR / sim_build).

Q4.20 encoding
--------------
    Unity gain  = 1.0  = 2^20 = 0x100000
    Max gain    = ~8.0 = 0x7FFFFF  (as loaded from hearing-aid LUTs)
    Near-zero   = 0x000001  (minimum non-zero gain)
"""

from __future__ import annotations

import os
import sys

DEPTH = 1024        # LUT depth (10-bit address -> 1024 entries)
N_BANDS = 10        # number of WDRC bands
UNITY_HEX = "100000"  # Q4.20: 0x100000 = 2^20 = 1.0


def gen_unity_luts(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for band in range(N_BANDS):
        fname = os.path.join(out_dir, f"intop_wdrc_gain_lut_b{band}.mem")
        with open(fname, "w", encoding="ascii") as fh:
            for _ in range(DEPTH):
                fh.write(f"{UNITY_HEX}\n")
    abs_dir = os.path.abspath(out_dir)
    print(f"[gen_unity_lut] {N_BANDS} unity-gain LUT files written -> {abs_dir}")


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    gen_unity_luts(target)


if __name__ == "__main__":
    main()

