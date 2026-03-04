#!/usr/bin/env python3
"""Clock claim note generation and consistency checks for paper-facing reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "verification" / "reports"
PAPER = REPORTS / "paper"

REQUIRED_CLAIM_STATEMENTS = [
    "100 MHz results are simulation-reference for PDM-path tests.",
    "FPGA timing evidence is Fmax-based implementation evidence.",
    "No claim of 100 MHz silicon operation is made unless timing closure at 100 MHz is demonstrated.",
]

REQUIRED_REPORTS = {
    "test1_thdn_results_analog.json": {"simulation-reference", "silicon-correlated"},
    "HA_4_thd.json": {"simulation-reference", "silicon-correlated"},
    "HA_12_ospl_ein.json": {"audio-domain"},
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Missing report: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid JSON object: {path}")
    return data


def _check_clock_fields(name: str, data: dict[str, Any], allowed_contexts: set[str]) -> list[str]:
    errs: list[str] = []
    if "clock_hz" not in data:
        errs.append(f"{name}: missing clock_hz")
    if "clock_context" not in data:
        errs.append(f"{name}: missing clock_context")
        return errs
    ctx = str(data["clock_context"])
    if ctx not in allowed_contexts:
        errs.append(f"{name}: clock_context='{ctx}' not in {sorted(allowed_contexts)}")
    try:
        hz = float(data["clock_hz"])
        if hz <= 0:
            errs.append(f"{name}: clock_hz must be > 0")
    except Exception:
        errs.append(f"{name}: invalid clock_hz value")
    return errs


def write_clock_note() -> tuple[Path, Path]:
    p_fpga = REPORTS / "fpga_synthesis.json"
    p_l1 = REPORTS / "test1_thdn_results_analog.json"
    p_ha4 = REPORTS / "HA_4_thd.json"
    p_ha12 = REPORTS / "HA_12_ospl_ein.json"

    fpga = _load_json(p_fpga)
    l1 = _load_json(p_l1)
    ha4 = _load_json(p_ha4)
    ha12 = _load_json(p_ha12)

    fmax_mhz = float(fpga.get("fmax_mhz"))
    l1_clk_hz = int(l1.get("clock_hz"))
    l1_ctx = str(l1.get("clock_context"))
    ha4_clk_hz = int(ha4.get("clock_hz"))
    ha4_ctx = str(ha4.get("clock_context"))
    ha12_clk_hz = int(ha12.get("clock_hz"))
    ha12_ctx = str(ha12.get("clock_context"))

    PAPER.mkdir(parents=True, exist_ok=True)
    out_json = PAPER / "clock_correlation.json"
    out_md = PAPER / "clock_correlation.md"

    payload = {
        "required_statement": REQUIRED_CLAIM_STATEMENTS,
        "reports": {
            "test1_thdn_results_analog": {
                "clock_hz": l1_clk_hz,
                "clock_context": l1_ctx,
            },
            "HA_4_thd": {
                "clock_hz": ha4_clk_hz,
                "clock_context": ha4_ctx,
            },
            "HA_12_ospl_ein": {
                "clock_hz": ha12_clk_hz,
                "clock_context": ha12_ctx,
            },
            "fpga_synthesis": {
                "fmax_mhz": fmax_mhz,
                "lut4": fpga.get("lut4"),
                "ff": fpga.get("ff"),
                "bsram18": fpga.get("bsram18"),
                "dsp": fpga.get("dsp"),
                "power_mw": fpga.get("power_mw"),
            },
        },
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md = [
        "# Clock Correlation Note",
        "",
        "## Required Claim Boundary",
        "",
        "1. 100 MHz results are simulation-reference for PDM-path tests.",
        "2. FPGA timing evidence is Fmax-based implementation evidence.",
        "3. No claim of 100 MHz silicon operation is made unless timing closure at 100 MHz is demonstrated.",
        "",
        "## Artifact Snapshot",
        "",
        f"- test1_thdn_results_analog: `{l1_clk_hz} Hz`, context `{l1_ctx}`",
        f"- HA_4_thd: `{ha4_clk_hz} Hz`, context `{ha4_ctx}`",
        f"- HA_12_ospl_ein: `{ha12_clk_hz} Hz`, context `{ha12_ctx}`",
        f"- FPGA Fmax from synthesis/PnR ingest: `{fmax_mhz:.2f} MHz`",
    ]
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"[clock-note] Wrote {out_json}")
    print(f"[clock-note] Wrote {out_md}")
    return out_json, out_md


def check_clock_consistency() -> int:
    errors: list[str] = []

    loaded: dict[str, dict[str, Any]] = {}
    for rel, allowed_ctx in REQUIRED_REPORTS.items():
        data = _load_json(REPORTS / rel)
        loaded[rel] = data
        errors.extend(_check_clock_fields(rel, data, allowed_ctx))

    fpga = _load_json(REPORTS / "fpga_synthesis.json")
    if fpga.get("fmax_mhz") is None:
        errors.append("fpga_synthesis.json: missing fmax_mhz")
    else:
        try:
            fmax_mhz = float(fpga["fmax_mhz"])
            if fmax_mhz <= 0:
                errors.append("fpga_synthesis.json: fmax_mhz must be > 0")
        except Exception:
            errors.append("fpga_synthesis.json: invalid fmax_mhz value")

    # Contradiction guard: silicon-correlated report cannot claim a clock above FPGA Fmax.
    if fpga.get("fmax_mhz") is not None:
        try:
            fmax_mhz = float(fpga["fmax_mhz"])
            for rel in ["test1_thdn_results_analog.json", "HA_4_thd.json"]:
                d = loaded[rel]
                if str(d.get("clock_context")) != "silicon-correlated":
                    continue
                clk_mhz = float(d.get("clock_hz")) / 1e6
                if clk_mhz > fmax_mhz + 1e-6:
                    errors.append(
                        f"{rel}: silicon-correlated clock {clk_mhz:.2f} MHz exceeds FPGA Fmax {fmax_mhz:.2f} MHz"
                    )
        except Exception:
            pass

    # Ensure note artifacts exist and contain the required boundary sentence.
    note_md = PAPER / "clock_correlation.md"
    note_json = PAPER / "clock_correlation.json"
    if not note_md.exists():
        errors.append("paper/clock_correlation.md is missing")
    if not note_json.exists():
        errors.append("paper/clock_correlation.json is missing")
    if note_md.exists():
        txt = note_md.read_text(encoding="utf-8")
        if REQUIRED_CLAIM_STATEMENTS[0] not in txt:
            errors.append("paper/clock_correlation.md missing required simulation-reference statement")

    if errors:
        print("[clock-check] FAIL")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("[clock-check] PASS")
    return 0


def _cmd_write_note(_args: argparse.Namespace) -> int:
    write_clock_note()
    return 0


def _cmd_check(_args: argparse.Namespace) -> int:
    return check_clock_consistency()


def _cmd_all(_args: argparse.Namespace) -> int:
    write_clock_note()
    return check_clock_consistency()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_note = sub.add_parser("write-note", help="Write clock-correlation note artifacts")
    p_note.set_defaults(func=_cmd_write_note)

    p_check = sub.add_parser("check", help="Validate clock metadata and claim boundary artifacts")
    p_check.set_defaults(func=_cmd_check)

    p_all = sub.add_parser("all", help="Run note generation then consistency checks")
    p_all.set_defaults(func=_cmd_all)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"[clock-control] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
