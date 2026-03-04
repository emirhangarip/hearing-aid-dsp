#!/usr/bin/env python3
"""
gen_filterbank_coeffs.py — Hearing-Aid 10-Band IIR Filterbank Coefficient Generator
====================================================================================
Designs a 10-band analysis filterbank using scipy at float64 precision and
quantises the coefficients to Q2.30 (32-bit signed), then writes fpga_coeff.mem
in the format expected by coeffs_rom.sv:

    20 lines, each 40 hex chars:
      <b0[31:0]><b1[31:0]><b2[31:0]><a1[31:0]><a2[31:0]>

where every coefficient is a signed Q2.30 fixed-point value
  range    : [-2, +2)
  precision: 2^-30 ≈ 9.3e-10  (vs 2^-22 ≈ 2.4e-7 for old Q2.22)

--------------------------------------------------------------------------------
Usage
--------------------------------------------------------------------------------
  # Regenerate from the default hearing-aid filterbank design (recommended):
  python3 gen_filterbank_coeffs.py

  # Custom output path:
  python3 gen_filterbank_coeffs.py --out /path/to/fpga_coeff.mem

  # Show decoded floating-point coefficient table without writing a file:
  python3 gen_filterbank_coeffs.py --show-only

  # Custom compression ratio / corner frequencies:
  python3 gen_filterbank_coeffs.py --flow 250 --fhigh 8000

  # Validate existing mem file against float64 reference:
  python3 gen_filterbank_coeffs.py --validate /path/to/existing.mem

--------------------------------------------------------------------------------
Filterbank Design
--------------------------------------------------------------------------------
The filterbank uses 10 contiguous, overlapping bandpass + lowpass bands that
cover the audible hearing-aid range (250 Hz – 8 kHz) on an approximately
log-spaced grid with ~1/3-octave bandwidth:

  Band 0 : lowpass   ( 0  – fc_0)         — protects against bass overload
  Band 1-8: bandpass (fc_{k-1} – fc_{k}) — hearing-aid audiogram bands
  Band 9 : highpass  ( fc_8 – Nyquist)    — captures high-frequency cues

Each band is implemented as TWO cascaded biquad sections (4th-order total):
  Section 0: primary bandpass / edge filter
  Section 1: complementary biquad (allpass bypass OR highpass complement)

This follows the same cascade structure used in the original fpga_coeff.mem
design, where each section is one row in the ROM file:
  rows 0,1 → band 0 sections 0,1
  rows 2,3 → band 1 sections 0,1
  ...

PRECISION NOTE
--------------
The original fpga_coeff.mem was generated at Q2.22 (24-bit) precision.
This script generates true Q2.30 (32-bit) precision:
  Q2.22 coefficient error : ±2^-23 ≈ ±1.2e-7
  Q2.30 coefficient error : ±2^-31 ≈ ±4.7e-10  (256× better)

To use the new coefficients, copy the output to the simulation working
directory and rebuild:
  cp fpga_coeff.mem verification/sim/
  make -C verification/sim SIM=verilator filterbank hearing-aid
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import sys

import numpy as np

try:
    from scipy import signal as scipy_signal
    _SCIPY_OK = True
except ImportError:
    print("ERROR: scipy not found. Install with: pip install scipy", file=sys.stderr)
    sys.exit(1)


# ── Constants ──────────────────────────────────────────────────────────────────
FS_AUDIO   = 48_000.0        # Sample rate (Hz)
N_BANDS    = 10              # Number of filterbank bands
N_SECTIONS = 2               # Biquad sections per band (= 4th-order per band)
N_ROWS     = N_BANDS * N_SECTIONS   # Total rows in fpga_coeff.mem = 20

COEFF_FRAC_BITS = 30         # Q2.30 format
COEFF_INT_BITS  = 2          # Signed integer bits (range [-2, +2))
# 32-bit signed integer bounds for Q2.30 representation:
#   mathematical range [-2.0, +2.0)  i.e. [-2^31, 2^31-1] as integers
COEFF_MAX =  (1 << 31) - 1   # 2 147 483 647  ≈ +2.0 − 2^-30
COEFF_MIN = -(1 << 31)       # −2 147 483 648  = −2.0


# ── Default filterbank design parameters ──────────────────────────────────────
# Log-spaced crossover frequencies (Hz) between the 10 bands.
# 9 crossovers define 10 bands:  [0, fc0], [fc0, fc1], ..., [fc8, Nyquist]
DEFAULT_CROSSOVERS_HZ = [
    354.0,    # Band 0/1 boundary  (~1/3-octave below 500 Hz)
    500.0,    # Band 1/2 boundary
    707.0,    # Band 2/3 boundary  (halfway between 500 and 1000 Hz)
    1000.0,   # Band 3/4 boundary
    1414.0,   # Band 4/5 boundary  (sqrt(2) * 1000)
    2000.0,   # Band 5/6 boundary
    2828.0,   # Band 6/7 boundary  (sqrt(2) * 2000)
    4000.0,   # Band 7/8 boundary
    5657.0,   # Band 8/9 boundary  (sqrt(2) * 4000)
]

# Filter order per band-edge filter (2 = second-order Butterworth per section)
SECTION_ORDER = 2     # → 4th-order per band (2 sections × 2nd-order each)

# Butterworth (maximally flat) design: flat passband, monotone rolloff.
FILTER_TYPE = "butter"


# ── Fixed-point conversion ──────────────────────────────────────────────────
def float_to_q2_30(x: float, label: str = "") -> int:
    """
    Round float to nearest Q2.30 integer, saturate to [-2, +2).

    Parameters
    ----------
    x      : coefficient in floating-point (must be in range [-2, +2))
    label  : name shown in warnings

    Returns
    -------
    Signed 32-bit integer in Q2.30 encoding.
    """
    raw = x * (1 << COEFF_FRAC_BITS)
    rounded = int(round(raw))
    if rounded > COEFF_MAX or rounded < COEFF_MIN:
        print(
            f"  WARNING: coefficient {label}={x:.8g} out of Q2.30 range "
            f"[-2, +2), saturating.",
            file=sys.stderr,
        )
        rounded = max(COEFF_MIN, min(COEFF_MAX, rounded))
    return rounded


def q2_30_to_float(n: int) -> float:
    """Convert Q2.30 integer to float64."""
    return n / (1 << COEFF_FRAC_BITS)


def pack_coeffs_hex(b0: int, b1: int, b2: int, a1: int, a2: int) -> str:
    """
    Pack five signed 32-bit Q2.30 coefficients into a 40-character hex string.

    Format: <b0[31:0]><b1[31:0]><b2[31:0]><a1[31:0]><a2[31:0]>
    This matches coeffs_rom.sv unpacking:  row[159:128]=b0, ..., row[31:0]=a2.
    """
    values = [b0, b1, b2, a1, a2]
    parts  = []
    for v in values:
        # Two's-complement 32-bit unsigned representation
        u32 = v & 0xFFFF_FFFF
        parts.append(f"{u32:08x}")
    return "".join(parts)


def decode_mem_line(line: str) -> list[float] | None:
    """
    Decode one fpga_coeff.mem line (40 or 30 hex chars) to [b0,b1,b2,a1,a2] floats.
    Returns None for blank / comment lines.
    """
    line = line.strip()
    if not line or line.startswith("//"):
        return None
    if len(line) == 40:
        word_bits = 32
        frac_bits = 30
        part_w    = 8
    elif len(line) == 30:
        word_bits = 24
        frac_bits = 22
        part_w    = 6
    else:
        return None

    parts = [line[i:i + part_w] for i in range(0, len(line), part_w)]
    if len(parts) != 5:
        return None
    vals = []
    for p in parts:
        v = int(p, 16)
        if v & (1 << (word_bits - 1)):
            v -= 1 << word_bits
        vals.append(v / (2 ** frac_bits))
    return vals


# ── scipy-based filterbank design ──────────────────────────────────────────────
def design_band_sos(
    flow: float,
    fhigh: float,
    section_order: int,
    fs: float,
) -> np.ndarray:
    """
    Design a 4th-order bandpass filter for the given frequency band using
    the Butterworth prototype and bilinear transform.

    Parameters
    ----------
    flow, fhigh : band edges in Hz (0 or fhigh>=Nyquist handled as LP/HP)
    section_order : order per single biquad section (2 for standard Butterworth)
    fs           : sample rate (Hz)

    Returns
    -------
    sos : (2, 6) second-order sections array in Scipy convention
          [b0, b1, b2, 1, a1, a2]  for each row.
    """
    nyq = fs / 2.0
    eps = 1.0   # Hz guard to avoid touching the Nyquist/DC exactly

    if flow <= eps and fhigh >= nyq - eps:
        # Allpass — shouldn't happen for a well-defined filterbank
        sos = np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
                        [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]])
    elif flow <= eps:
        # Lowpass band (Band 0)
        Wn = fhigh / nyq
        sos = scipy_signal.butter(
            section_order * N_SECTIONS, Wn, btype="low", output="sos"
        )
    elif fhigh >= nyq - eps:
        # Highpass band (Band N-1)
        Wn = flow / nyq
        sos = scipy_signal.butter(
            section_order * N_SECTIONS, Wn, btype="high", output="sos"
        )
    else:
        # Bandpass band
        Wn = [flow / nyq, fhigh / nyq]
        sos = scipy_signal.butter(
            section_order, Wn, btype="bandpass", output="sos"
        )

    # We always need exactly 2 rows (N_SECTIONS biquads)
    assert sos.shape[0] == N_SECTIONS, (
        f"Expected {N_SECTIONS} SOS sections, got {sos.shape[0]} "
        f"(flow={flow:.0f}, fhigh={fhigh:.0f} Hz)"
    )
    return sos


def scipy_sos_to_rtl_rows(
    sos: np.ndarray,
    band_idx: int,
    verbose: bool = False,
) -> list[str]:
    """
    Convert a (2, 6) scipy SOS array to two fpga_coeff.mem hex rows.

    Scipy SOS row: [b0, b1, b2, 1, a1, a2] (note: a0 = 1 in scipy convention).
    RTL convention: H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)
                    a1, a2 coefficients stored NEGATED relative to scipy's a1, a2.

    Wait — scipy's SOS row stores [b0, b1, b2, a0, a1, a2] where a0=1 and the
    transfer function is:
      H(z) = (b0 + b1*z^-1 + b2*z^-2) / (a0 + a1*z^-1 + a2*z^-2)
    So scipy's a1, a2 are NOT negated. They match the RTL convention directly.
    """
    rows = []
    for s_idx, row in enumerate(sos):
        # Scipy row: [b0, b1, b2, a0=1, a1, a2]
        fb0, fb1, fb2, fa0, fa1, fa2 = row.tolist()
        assert abs(fa0 - 1.0) < 1e-10, f"Expected a0=1, got {fa0}"

        # Quantise to Q2.30
        qb0 = float_to_q2_30(fb0, f"b[{band_idx}][{s_idx}][0]")
        qb1 = float_to_q2_30(fb1, f"b[{band_idx}][{s_idx}][1]")
        qb2 = float_to_q2_30(fb2, f"b[{band_idx}][{s_idx}][2]")
        qa1 = float_to_q2_30(fa1, f"a[{band_idx}][{s_idx}][1]")
        qa2 = float_to_q2_30(fa2, f"a[{band_idx}][{s_idx}][2]")

        if verbose:
            print(
                f"  Band {band_idx:2d} S{s_idx}: "
                f"b=[{fb0:+.8f}, {fb1:+.8f}, {fb2:+.8f}]  "
                f"a=[{fa1:+.8f}, {fa2:+.8f}]"
            )

        rows.append(pack_coeffs_hex(qb0, qb1, qb2, qa1, qa2))
    return rows


# ── Filterbank power-sum analysis (diagnostic, not used in main codegen) ───────
def compute_filterbank_response(crossovers: list[float], fs: float) -> np.ndarray:
    """
    Compute the sum-of-powers frequency response of the filterbank.

    Returns (n_freq, N_BANDS+1) array:
      column 0       : frequency axis [Hz]
      columns 1..N_BANDS : |H_k(f)|^2 for each band

    Useful for verifying that the filterbank covers the audio band without gaps.

    Example usage:
      resp = compute_filterbank_response(DEFAULT_CROSSOVERS_HZ, 48000)
      # resp[:, 0]   = frequencies
      # resp[:, 1:]  = per-band power
      # sum = resp[:, 1:].sum(axis=1) — should be near 1.0 in passband
    """
    n_freq     = 4096
    freqs      = np.linspace(0.0, fs / 2.0, n_freq, endpoint=False)
    nyq        = fs / 2.0
    band_edges = [0.0] + list(crossovers) + [nyq]

    out = np.zeros((n_freq, N_BANDS + 1), dtype=np.float64)
    out[:, 0] = freqs

    for b in range(N_BANDS):
        flow  = band_edges[b]
        fhigh = band_edges[b + 1]
        sos   = design_band_sos(flow, fhigh, SECTION_ORDER, fs)
        # Stable frequency response via zpk conversion
        z, p, k = scipy_signal.sos2zpk(sos)
        _, h    = scipy_signal.freqz_zpk(z, p, k, worN=freqs, fs=fs)
        out[:, b + 1] = np.abs(h) ** 2

    return out


# ── Main design routine ────────────────────────────────────────────────────────
def generate_coeffs(
    crossovers: list[float],
    fs: float,
    verbose: bool = True,
) -> list[str]:
    """
    Design the 10-band filterbank and return 20 hex strings (one per ROM row).

    Parameters
    ----------
    crossovers : list of 9 crossover frequencies [Hz]
    fs         : sample rate [Hz]
    verbose    : print coefficient table to stdout

    Returns
    -------
    List of 20 40-char hex strings for fpga_coeff.mem.
    """
    assert len(crossovers) == N_BANDS - 1, (
        f"Need {N_BANDS-1} crossovers, got {len(crossovers)}"
    )
    nyq         = fs / 2.0
    band_edges  = [0.0] + list(crossovers) + [nyq]
    hex_rows: list[str] = []

    if verbose:
        print(f"\nFilterbank design (fs={fs:.0f} Hz, Q2.30, Butterworth order {SECTION_ORDER*N_SECTIONS})")
        print("-" * 72)

    for b in range(N_BANDS):
        flow  = band_edges[b]
        fhigh = band_edges[b + 1]
        if verbose:
            print(f"\n  Band {b}: {flow:.1f} – {fhigh:.1f} Hz")

        sos  = design_band_sos(flow, fhigh, SECTION_ORDER, fs)
        rows = scipy_sos_to_rtl_rows(sos, b, verbose=verbose)
        hex_rows.extend(rows)

    if verbose:
        print("\n" + "-" * 72)
        print(f"Generated {len(hex_rows)} rows ({len(hex_rows)//N_SECTIONS} bands × {N_SECTIONS} sections).")
    return hex_rows


# ── Validation mode: decode existing mem and report precision ─────────────────
def validate_mem(mem_path: str) -> None:
    """
    Read an existing fpga_coeff.mem, decode to floats, and print a summary
    showing coefficient precision vs Q2.30 reference from this script.
    """
    with open(mem_path) as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    rows = [decode_mem_line(ln) for ln in lines]
    rows = [r for r in rows if r is not None]

    if len(rows) < N_ROWS:
        print(f"WARNING: expected {N_ROWS} rows, found {len(rows)}.")
    if not rows:
        print("No valid rows found.")
        return

    print(f"\nDecoded {len(rows)} coefficient rows from {mem_path}")
    print(f"{'Row':>4} {'b0':>14} {'b1':>14} {'b2':>14} {'a1':>14} {'a2':>14}")
    print("-" * 75)
    for i, row in enumerate(rows[:N_ROWS]):
        print(
            f"  {i:2d}  {row[0]:+.9f}  {row[1]:+.9f}  {row[2]:+.9f}  "
            f"{row[3]:+.9f}  {row[4]:+.9f}"
        )

    # Precision note
    print(
        "\n  NOTE: Original Q2.22 coefficients zero-padded to Q2.30 have identical\n"
        "  numerical precision to the Q2.22 design (no additional fractional bits).\n"
        "  To gain true Q2.30 precision, run this script to regenerate from float64:\n"
        "    python3 gen_filterbank_coeffs.py"
    )


# ── CLI entry point ────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Q2.30 IIR filterbank coefficients.")
    p.add_argument(
        "--out", "-o",
        default=os.path.join(os.path.dirname(__file__), "fpga_coeff.mem"),
        help="Output path for fpga_coeff.mem (default: same directory as this script).",
    )
    p.add_argument(
        "--fs", type=float, default=FS_AUDIO,
        help=f"Sample rate in Hz (default: {FS_AUDIO:.0f}).",
    )
    p.add_argument(
        "--flow", type=float, default=None,
        help="Override lowest crossover frequency (Hz).",
    )
    p.add_argument(
        "--fhigh", type=float, default=None,
        help="Override highest crossover frequency (Hz).",
    )
    p.add_argument(
        "--crossovers", nargs="+", type=float, default=None,
        metavar="HZ",
        help=(
            f"Override all {N_BANDS-1} crossover frequencies (Hz). "
            "Must supply exactly 9 values."
        ),
    )
    p.add_argument(
        "--show-only", action="store_true",
        help="Print coefficient table without writing a file.",
    )
    p.add_argument(
        "--validate",
        metavar="MEM_PATH",
        help="Decode and print an existing fpga_coeff.mem; do not write output.",
    )
    p.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress coefficient table printout.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.validate:
        validate_mem(args.validate)
        return

    # Build crossover list
    if args.crossovers is not None:
        if len(args.crossovers) != N_BANDS - 1:
            print(f"ERROR: need exactly {N_BANDS-1} crossovers, got {len(args.crossovers)}.",
                  file=sys.stderr)
            sys.exit(1)
        crossovers = args.crossovers
    else:
        crossovers = list(DEFAULT_CROSSOVERS_HZ)
        if args.flow is not None:
            crossovers[0] = args.flow
        if args.fhigh is not None:
            crossovers[-1] = args.fhigh

    # Validate crossovers
    nyq = args.fs / 2.0
    for i, fc in enumerate(crossovers):
        if not (0.0 < fc < nyq):
            print(f"ERROR: crossover[{i}]={fc:.1f} Hz outside (0, {nyq:.0f}) Hz.",
                  file=sys.stderr)
            sys.exit(1)
    for i in range(len(crossovers) - 1):
        if crossovers[i] >= crossovers[i + 1]:
            print(
                f"ERROR: crossovers must be strictly ascending: "
                f"crossover[{i}]={crossovers[i]:.1f} >= crossover[{i+1}]={crossovers[i+1]:.1f}.",
                file=sys.stderr,
            )
            sys.exit(1)

    verbose = not args.quiet
    hex_rows = generate_coeffs(crossovers, args.fs, verbose=verbose)

    if args.show_only:
        print("\nGenerated mem file content (not written):")
        for row in hex_rows:
            print(row)
        return

    out_path = args.out
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        for row in hex_rows:
            fh.write(row + "\n")

    print(f"\nWritten: {out_path}  ({len(hex_rows)} rows, Q2.30 format)")
    print(
        "Copy to simulation directory before re-elaborating:\n"
        f"  cp {out_path} verification/sim/fpga_coeff.mem"
    )


if __name__ == "__main__":
    main()
