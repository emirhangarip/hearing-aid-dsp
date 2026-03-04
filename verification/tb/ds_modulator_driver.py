"""
drivers/ds_modulator_driver.py — Direct ds_modulator Unit-Test Driver
======================================================================
Bypasses the I2S / dd_dac_top_dsd hierarchy and drives ds_modulator.din
directly, one sample per clock cycle.

Why bypass I2S?
───────────────
The I2S path runs at 48 kHz audio rate.  At OSR=64, each audio sample
requires 64 system clock cycles.  For a 25 ms test at 100 MHz:

  Full I2S path  : 25 ms × 100 MHz = 2 500 000 clock edges   (slow)
  Direct drive   : same result, but the driver is simpler and the test
                   does not depend on the i2s_receiver being correct.

The trade-off: direct tests only prove ds_modulator in isolation.  For
system-level confidence you still need test_dac_core.py with I2S.

Port map of ds_modulator
─────────────────────────
  clk       : system clock (100 MHz in the reference design)
  rst_n     : active-low synchronous reset
  din[31:0] : signed PCM input, held stable for OSR cycles per sample
  dout      : 1-bit PDM output sampled every clock

Timing model
─────────────
              ┌──┐  ┌──┐  ┌──┐  ┌──┐
  clk         │  └──┘  └──┘  └──┘  └──  ...
              ───────────────────────────
  din         ──[ sample_k  ]──[ sample_k+1 ]──
              ───────────────────────────
  dout              X  1  0  1  0  1  X         ← captured every rising clk

The driver updates din on each rising clock edge.  The modulator latches
din on that same edge (RTL: always @(posedge clk)).  The cocotb convention
is to drive combinatorial inputs before the next rising edge (i.e., after
the current rising edge but with a #1 delta-cycle delay) so the simulator
sees a stable value when the clock fires.
"""

from __future__ import annotations

from typing import Callable, Optional

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles
from cocotb.handle import SimHandleBase


class DSModulatorDriver:
    """
    Cocotb driver for direct ds_modulator unit testing.

    Instantiate once per test, then call stream_samples() to feed PCM
    and capture PDM simultaneously.

    Parameters
    ----------
    dut : SimHandleBase
        Cocotb DUT handle.  Must expose: clk, rst_n, din, dout.
    clk_period_ns : float
        System clock period in nanoseconds (default 10.0 → 100 MHz; 20.0 → 50 MHz).
    reset_cycles : int
        Number of clock cycles to hold rst_n low at startup (default 20).
    """

    def __init__(
        self,
        dut: SimHandleBase,
        clk_period_ns: float = 10.0,
        reset_cycles: int = 20,
    ) -> None:
        self.dut          = dut
        self.clk_period_ns = clk_period_ns
        self.reset_cycles  = reset_cycles

        self._pdm_bits:  list[int] = []
        self._capturing: bool      = False
        self._capture_generation: int = 0
        self._capture_task = None

    # ─────────────────────────────────────────── startup ─────────────────────

    async def start_clock(self) -> None:
        """Launch the system clock coroutine (idempotent — safe to call once)."""
        cocotb.start_soon(
            Clock(self.dut.clk, self.clk_period_ns, unit="ns").start()
        )

    async def reset(self) -> None:
        """Apply active-low reset for ``reset_cycles`` clock cycles."""
        self.dut.rst_n.value = 0
        self.dut.din.value   = 0
        await ClockCycles(self.dut.clk, self.reset_cycles)
        self.dut.rst_n.value = 1
        await ClockCycles(self.dut.clk, 4)   # drain pipeline
        cocotb.log.info(f"[DS Driver] Reset released after {self.reset_cycles} cycles.")

    # ─────────────────────────────────────────── PDM capture ─────────────────

    def _start_capture(self) -> None:
        """Launch background coroutine that samples dout on every rising clk."""
        self._pdm_bits.clear()
        self._capture_generation += 1
        self._capturing = True
        gen = self._capture_generation
        self._capture_task = cocotb.start_soon(self._capture_loop(gen))

    async def _stop_capture(self) -> list[int]:
        """Stop the capture loop, wait for it to exit, then return collected bits."""
        self._capturing = False
        task = self._capture_task
        if task is not None:
            await task
            self._capture_task = None
        return list(self._pdm_bits)

    async def _capture_loop(self, generation: int) -> None:
        while True:
            await RisingEdge(self.dut.clk)
            if not self._capturing or generation != self._capture_generation:
                break
            try:
                self._pdm_bits.append(int(self.dut.dout.value))
            except Exception:
                pass   # X/Z during reset — skip silently

    # ─────────────────────────────────────────── main driver ─────────────────

    async def stream_samples(
        self,
        samples: list[int],
        osr: float = 1,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> list[int]:
        """
        Drive PCM samples into din and collect PDM output simultaneously.

        Parameters
        ----------
        samples : list[int]
            Signed 32-bit PCM samples.  Each sample is held on din for
            ``osr`` consecutive clock cycles.  Pass pre-expanded samples
            (from ``expand_to_modulator_input``) with osr=1, or pass
            audio-rate samples with osr=64 to do the expansion here.
        osr : float
            Number of clock cycles to hold each sample (default 1 = caller
            has already expanded).  Fractional values (e.g. 1041.667 for
            50 MHz / 48 kHz) use a Bresenham accumulator to alternate
            between floor(osr) and ceil(osr) so the long-run average sample
            rate matches exactly FS_AUDIO.
        on_progress : callable, optional
            Called with (current_sample_idx, total_samples) every 1000 samples
            for progress reporting in long tests.

        Returns
        -------
        list[int]
            PDM bitstream (one bit per clock cycle) captured during the drive.
        """
        self._start_capture()
        n = len(samples)
        total_cycles_approx = int(n * osr)
        cocotb.log.info(
            f"[DS Driver] Streaming {n:,} samples × {osr:.3f} cycles each "
            f"≈ {total_cycles_approx:,} clock cycles "
            f"({total_cycles_approx * self.clk_period_ns / 1e6:.2f} ms)"
        )

        # Bresenham accumulator for fractional OSR
        _osr_whole = int(osr)
        _osr_frac  = osr - _osr_whole
        _accum     = 0.0

        for idx, sample in enumerate(samples):
            # Drive din — change AFTER the rising edge (standard cocotb practice)
            self.dut.din.value = int(sample) & 0xFFFF_FFFF

            # Bresenham: accumulate fraction, carry into integer cycles
            _accum += _osr_frac
            _extra  = int(_accum)
            _accum -= _extra
            await ClockCycles(self.dut.clk, _osr_whole + _extra)

            # Optional progress hook (every 1000 audio samples)
            if on_progress is not None and idx % 1000 == 0:
                on_progress(idx, n)

        # Allow a few extra cycles for pipeline to flush
        await ClockCycles(self.dut.clk, 8)

        bits = await self._stop_capture()
        cocotb.log.info(
            f"[DS Driver] Capture complete: {len(bits):,} PDM bits "
            f"({len(bits) / (1e9 / self.clk_period_ns) * 1e3:.2f} ms)"
        )
        return bits

    # ─────────────────────────────────── convenience: full test in one call ──

    async def run_single_tone_test(
        self,
        pcm_samples: list[int],
        osr: int = 64,
    ) -> list[int]:
        """
        Full lifecycle: start_clock → reset → stream → return PDM bits.

        Convenience wrapper so individual tests can stay concise.
        """
        await self.start_clock()
        await self.reset()
        bits = await self.stream_samples(pcm_samples, osr=osr)
        return bits
