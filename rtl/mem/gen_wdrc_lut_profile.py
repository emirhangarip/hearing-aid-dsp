#!/usr/bin/env python3
"""
Generate hearing-aid WDRC LUT files (Q4.20 gain) for all 10 bands.

Default target profile:
  - Upward compression style, low-level max gain +18 dB
  - Knee around -40 dBFS
  - Compression ratio ~3:1 above knee
  - Smoothly taper gain to unity (0 dB) at high input levels

Address mapping model (matches RTL envelope address logic):
  addr ~= env * 1024, where env ~= (2/pi) * amplitude for a sine tone.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np


DEPTH = 1024
N_BANDS = 10
Q_GAIN = 20
UNITY_Q = 1 << Q_GAIN
MAX_Q = 0x7FFFFF


def _addr_to_input_dbfs(addr: int) -> float:
    """
    Convert LUT address back to an approximate input sine level in dBFS.
    Uses the same envelope approximation used in verification.
    """
    env = (addr + 0.5) / DEPTH
    amp = env * (math.pi / 2.0)
    return 20.0 * math.log10(max(amp, 1e-12))


def _smooth_monotonic(curve_db: np.ndarray, kernel_len: int = 9) -> np.ndarray:
    """Apply light smoothing while preserving a non-increasing gain curve."""
    if kernel_len < 3 or kernel_len % 2 == 0:
        return curve_db

    half = kernel_len // 2
    weights = np.array(
        [half + 1 - abs(i - half) for i in range(kernel_len)],
        dtype=np.float64,
    )
    weights /= np.sum(weights)

    padded = np.pad(curve_db, (half, half), mode="edge")
    smoothed = np.convolve(padded, weights, mode="valid")

    smoothed = np.clip(smoothed, 0.0, float(np.max(curve_db)))
    for i in range(1, len(smoothed)):
        if smoothed[i] > smoothed[i - 1]:
            smoothed[i] = smoothed[i - 1]
    return smoothed


def build_gain_curve_db(
    max_gain_db: float,
    knee_dbfs: float,
    compression_ratio: float,
    smooth_kernel_len: int,
) -> np.ndarray:
    """
    Build the per-address gain profile in dB.

    Piecewise gain model:
      gain = max_gain_db                         (below knee)
      gain = max_gain_db - slope*(in-knee)       (above knee, CR region)
      gain = max(gain, 0 dB)                     (no attenuation)
    """
    if compression_ratio <= 1.0:
        raise ValueError("compression_ratio must be > 1.0")

    slope = 1.0 - (1.0 / compression_ratio)  # dB gain drop per dB input rise
    in_db = np.array([_addr_to_input_dbfs(a) for a in range(DEPTH)], dtype=np.float64)

    gain_db = np.where(
        in_db <= knee_dbfs,
        max_gain_db,
        max_gain_db - slope * (in_db - knee_dbfs),
    )
    gain_db = np.maximum(gain_db, 0.0)
    gain_db = _smooth_monotonic(gain_db, kernel_len=smooth_kernel_len)
    gain_db[0] = max_gain_db  # guarantee exact max gain at addr 0

    return gain_db


def gain_db_to_q420(gain_db: np.ndarray) -> np.ndarray:
    """Convert dB gain to unsigned Q4.20 words, clamped to RTL range."""
    gain_lin = 10.0 ** (gain_db / 20.0)
    q = np.rint(gain_lin * UNITY_Q).astype(np.int64)
    q = np.clip(q, UNITY_Q, MAX_Q)
    return q.astype(np.int32)


def write_luts(out_dir: Path, words: np.ndarray) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{int(w):06x}" for w in words.tolist()]
    payload = "\n".join(lines) + "\n"
    for band in range(N_BANDS):
        (out_dir / f"intop_wdrc_gain_lut_b{band}.mem").write_text(payload, encoding="ascii")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate hearing-aid WDRC LUT profile")
    ap.add_argument("--out-dir", default=".", help="Output directory for intop_wdrc_gain_lut_b*.mem")
    ap.add_argument("--max-gain-db", type=float, default=18.0, help="Maximum low-level gain in dB")
    ap.add_argument("--knee-dbfs", type=float, default=-40.0, help="Compression knee input level in dBFS")
    ap.add_argument("--ratio", type=float, default=3.0, help="Compression ratio above knee (e.g. 3.0 or 4.0)")
    ap.add_argument("--smooth-kernel", type=int, default=9, help="Odd smoothing kernel length (address domain)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    gain_db = build_gain_curve_db(
        max_gain_db=args.max_gain_db,
        knee_dbfs=args.knee_dbfs,
        compression_ratio=args.ratio,
        smooth_kernel_len=args.smooth_kernel,
    )
    words = gain_db_to_q420(gain_db)
    write_luts(out_dir, words)

    # Quick profile summary at representative input levels
    print("[gen_wdrc_lut_profile] Profile summary:")
    for in_db in (-60, -50, -45, -40, -35, -30, -25, -20, -15, -10, -5, 0):
        amp = 10.0 ** (in_db / 20.0)
        env = amp * (2.0 / math.pi)
        addr = min(1023, int(env * (2**23)) >> 13)
        gdb = float(gain_db[addr])
        print(f"  in {in_db:>4.0f} dBFS  -> addr {addr:>4d}  gain {gdb:>6.2f} dB")
    print(f"[gen_wdrc_lut_profile] Wrote {N_BANDS} LUT files to {out_dir}")


if __name__ == "__main__":
    main()

