"""
ds_model.py — Python Behavioral Model of ds_modulator.sv
=========================================================
Exact integer arithmetic (two's-complement wrapping) replica of the RTL.

Purpose
-------
1. Test-suite validation: compare Python model output to RTL JSON results.
   If both agree, the DSPEngine is correctly measuring RTL behaviour.
2. Mutation testing: inject known faults and verify the test suite detects them.
3. Architecture exploration: run quickly without Verilator.

RTL mapping (ds_modulator.sv)
------------------------------
localparam BW_EXT  = 8
localparam BW_TOT  = DAC_BW + BW_EXT                 // 40
localparam BW_TOT2 = BW_TOT + $clog2(OSR)            // 46 for OSR=64

MAX_VAL  =  2^31  (1st-stage feedback, BW_TOT wide)
MIN_VAL  = -2^31
MAX_VAL2 = {MAX_VAL, 6'b0} = 2^31 << 6 = +2^37  (left-shift, not sign-extend)
MIN_VAL2 = {MIN_VAL, 6'b0} = -2^31 << 6 = -2^37

2nd-stage feedback = +/-2^37, occupying 0.78% of 46-bit accumulator range (+/-2^45).

Quantizer (symmetric):
  if (dithered_signal == 0) dout_r <= ~dout_r;   // toggle: no zero-crossing bias
  else dout_r <= dithered_signal[BW_TOT2-1];
  assign dout = !dout_r

Usage
-----
    from ds_model import DSModulatorModel
    model = DSModulatorModel()
    pcm   = generate_sine(1000, 960, -1.0, 48000)   # 960 samples at 48 kHz
    bits  = model.run(pcm, osr_effective=2083)        # PDM at 100 MHz
"""

from __future__ import annotations

import math
from typing import Iterable

__all__ = ["DSModulatorModel"]


# ─── helpers ──────────────────────────────────────────────────────────────────

def _clog2(n: int) -> int:
    """Ceiling-log2 matching Verilog $clog2 semantics ($clog2(1) = 0)."""
    if n <= 1:
        return 0
    return math.ceil(math.log2(n))


def _wrap(val: int, bits: int) -> int:
    """
    Two's-complement truncation to a signed ``bits``-wide register.

    Matches Verilog signed-arithmetic overflow: values exceeding the register
    range wrap, they do NOT saturate.  Python integers are arbitrary-precision
    so we must apply this manually.

    Examples
    --------
    _wrap(2**31,  40) →  2147483648  (positive, fits in 40-bit signed)
    _wrap(-2**31, 40) → -2147483648  (negative, fits in 40-bit signed)
    _wrap(2**40,  40) →  0           (wrapped: 2^40 mod 2^40 = 0)
    """
    mask = (1 << bits) - 1
    val  = val & mask                      # keep lower `bits` bits (unsigned)
    if val >= (1 << (bits - 1)):           # top bit set → negative in 2's comp
        val -= (1 << bits)
    return val


# ─── model ────────────────────────────────────────────────────────────────────

class DSModulatorModel:
    """
    Pure-Python integer replica of ds_modulator.sv.

    RTL parameters (must match the synthesised design):
      DAC_BW = 32  — PCM input word width
      OSR    = 64  — governs accumulator bit-widths; NOT the sample-hold period

    The actual number of clock cycles per audio sample is ``osr_effective``
    (= fs_sys / fs_audio = 100 MHz / 48 kHz ≈ 2083) passed at run-time.
    """

    DAC_BW: int = 32
    OSR:    int = 64       # RTL parameter — bit-width only

    def __init__(self) -> None:
        BW_EXT = 8
        self.BW_TOT  = self.DAC_BW + BW_EXT            # 40
        self.BW_TOT2 = self.BW_TOT + _clog2(self.OSR)  # 46

        MID_VAL = 1 << (self.DAC_BW - 1)               # 2^31

        # 1st-stage feedback (BW_TOT = 40 bits)
        self.MAX_VAL = MID_VAL                          # +2^31
        self.MIN_VAL = -MID_VAL                         # -2^31

        # 2nd-stage feedback — RTL: {MAX_VAL, {$clog2(OSR){1'b0}}} (left-shift).
        # MAX_VAL2 = 2^31 << 6 = 2^37, MIN_VAL2 = -2^31 << 6 = -2^37
        # Feedback occupies ±2^37 out of ±2^45 accumulator range.
        clog2_osr = _clog2(self.OSR)                    # 6
        self.MAX_VAL2 = _wrap(self.MAX_VAL << clog2_osr, self.BW_TOT2)  # +2^37
        self.MIN_VAL2 = _wrap(self.MIN_VAL << clog2_osr, self.BW_TOT2)  # -2^37

        self._acc1:   int = 0
        self._acc2:   int = 0
        self._dout_r: int = 0

    # ── public API ──────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Re-initialise internal state (mirrors active-low rst_n in RTL)."""
        self._acc1   = 0
        self._acc2   = 0
        self._dout_r = 0

    def run(
        self,
        pcm_samples: Iterable[int],
        osr_effective: int = 2083,
    ) -> list[int]:
        """
        Simulate the modulator over a sequence of PCM samples.

        Parameters
        ----------
        pcm_samples   : iterable of 32-bit signed integers at audio rate
        osr_effective : system-clock cycles per audio sample
                        (fs_sys / fs_audio = 100 MHz / 48 kHz ≈ 2083)

        Returns
        -------
        list[int]
            PDM bitstream (0 or 1) at system-clock rate.
            Length = len(pcm_samples) × osr_effective.
        """
        self.reset()
        bits: list[int] = []

        # Cache constants to local names for loop speed
        BW_TOT  = self.BW_TOT
        BW_TOT2 = self.BW_TOT2
        MAX_VAL  = self.MAX_VAL
        MIN_VAL  = self.MIN_VAL
        MAX_VAL2 = self.MAX_VAL2
        MIN_VAL2 = self.MIN_VAL2
        DAC_BW   = self.DAC_BW

        acc1   = self._acc1
        acc2   = self._acc2
        dout_r = self._dout_r

        for sample in pcm_samples:
            sample = _wrap(int(sample), DAC_BW)

            for _ in range(osr_effective):

                # ── combinational: feedback selection ─────────────────────
                dac_val  = MAX_VAL  if dout_r else MIN_VAL
                dac_val2 = MAX_VAL2 if dout_r else MIN_VAL2

                # ── 1st integrator (BW_TOT = 40-bit signed) ───────────────
                #   wire in_ext     = {{BW_EXT{din[DAC_BW-1]}}, din}
                #   wire delta_s0_c0 = in_ext - dac_val
                #   wire delta_s0_c1 = DAC_acc_1st + delta_s0_c0
                in_ext      = _wrap(sample,          BW_TOT)   # sign-ext 32→40
                delta0      = _wrap(in_ext - dac_val, BW_TOT)
                delta0_acc  = _wrap(acc1  + delta0,   BW_TOT)

                # ── 2nd integrator (BW_TOT2 = 46-bit signed) ──────────────
                #   wire in_ext2    = {{$clog2(OSR){delta_s0_c1[BW_TOT-1]}}, delta_s0_c1}
                #   wire delta_s1_c0 = in_ext2 - dac_val2
                #   wire delta_s1_c1 = DAC_acc_2nd + delta_s1_c0
                in_ext2     = _wrap(delta0_acc,           BW_TOT2)  # sign-ext 40→46
                delta1      = _wrap(in_ext2  - dac_val2,  BW_TOT2)
                delta1_acc  = _wrap(acc2     + delta1,     BW_TOT2)

                # ── quantizer ─────────────────────────────────────────────
                #   if (dithered_signal == 0)
                #       dout_r <= ~dout_r;    // toggle — symmetric
                #   else
                #       dout_r <= dithered_signal[BW_TOT2-1];
                #   assign dout = !dout_r
                if delta1_acc == 0:
                    dout_r_new = 1 - dout_r  # toggle: eliminates zero-crossing bias
                else:
                    dout_r_new = 1 if delta1_acc < 0 else 0
                bits.append(1 - dout_r_new)   # !dout_r

                # ── clocked register updates ──────────────────────────────
                acc1   = delta0_acc
                acc2   = delta1_acc
                dout_r = dout_r_new

        # Save final state so run() can be resumed (optional)
        self._acc1   = acc1
        self._acc2   = acc2
        self._dout_r = dout_r

        return bits
