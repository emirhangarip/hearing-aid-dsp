#!/usr/bin/env python3
from __future__ import annotations

import struct
from pathlib import Path


NUM_BANDS = 10
LUT_SIZE = 1024


def read_mem_file(path: Path) -> list[int]:
    values: list[int] = []
    for raw_line in path.read_text(encoding="ascii").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        values.append(int(line, 16))

    if len(values) != LUT_SIZE:
        raise ValueError(f"{path} has {len(values)} entries, expected {LUT_SIZE}")
    return values


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    mem_dir = script_dir.parent / "rtl" / "mem"
    data_dir = script_dir / "data"
    lut_bin = data_dir / "wdrc_luts.bin"
    valid_flag = data_dir / "wdrc_valid.txt"

    data_dir.mkdir(parents=True, exist_ok=True)

    with lut_bin.open("wb") as fh:
        for band in range(NUM_BANDS):
            mem_path = mem_dir / f"intop_wdrc_gain_lut_b{band}.mem"
            values = read_mem_file(mem_path)
            for value in values:
                fh.write(struct.pack("<I", value))

    valid_flag.write_text("VALID\n", encoding="ascii")

    print(f"Wrote {lut_bin}")
    print(f"Wrote {valid_flag}")


if __name__ == "__main__":
    main()
