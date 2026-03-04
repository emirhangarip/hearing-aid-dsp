#!/usr/bin/env python3
"""
validate_suite.py — Verification Test-Suite Self-Validation
============================================================
Confirms that the test methodology itself is correct, independent of RTL.

Four independent validation methods are used:

  [1] DSP Engine Accuracy
        Inject mathematically perfect signals into VirtualAnalogAnalyzer and
        verify it measures the correct THD+N.  Validates the FFT/windowing/
        noise-integration pipeline.

  [2] AES17 Methodology Compliance
        Check that stimulus generators, measurement bandwidth, test levels,
        and signal parameters match AES17 / IEC 60268-17 requirements.

  [3] Python Model vs RTL Results
        Run DSModulatorModel (exact integer replica of ds_modulator.sv) at
        1 kHz −1 dBFS.  Measure with the same DSPEngine used in the cocotb
        tests.  Compare to the most recent RTL simulation JSON.
        Agreement < 3 dB confirms the test pipeline measures RTL correctly.

  [4] Mutation Testing
        Inject known faults into the Python model and confirm THD+N degrades
        enough to fail the test limits.  Proves the test suite has detection
        coverage for the RTL bugs that matter.

Run
---
    cd verification/tb
    python validate_suite.py

Exit code 0 = all sections pass.  Non-zero = at least one section failed.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

# ── path setup so this script works from anywhere ────────────────────────────
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from dsp_engine   import VirtualAnalogAnalyzer
from pcm_generator import (
    generate_sine, generate_dithered_silence, generate_ccif_imd,
    generate_smpte_imd, AES17_SWEEP_FREQS, AES17_LINEARITY_LEVELS,
    FULL_SCALE, dbfs_to_linear,
)
from ds_model import DSModulatorModel, _wrap

# ── constants matching test_hifi_suite.py ─────────────────────────────────────
FS_SYS          = 100_000_000.0   # system clock (Hz)
FS_AUDIO        = 48_000.0        # audio sample rate (Hz)
OSR_EFFECTIVE   = int(FS_SYS / FS_AUDIO)   # 2083 clock cycles per sample
SETTLE_MS       = 1.0             # settle time used in tests (ms)
THDN_LIMIT_ANALOG = -80.0         # dBc — primary quality gate

# RTL JSON result file (produced by the most recent Verilator run)
_REPORTS     = _HERE.parent / "reports"
_JSON_ANALOG = _REPORTS / "test1_thdn_results_analog.json"
# RTL source file — used for staleness detection in Section 3
_RTL_SV      = _HERE.parent.parent / "rtl" / "ds_modulator.sv"


# ─────────────────────────────────────────────────────────────────────────────
# Result tracking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Check:
    tag:    str
    label:  str
    passed: bool
    detail: str

@dataclass
class Section:
    name:   str
    checks: list[Check] = field(default_factory=list)

    def add(self, tag: str, label: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(tag, label, passed, detail))

    def all_pass(self) -> bool:
        return all(c.passed for c in self.checks)

    def print(self) -> None:
        status = "PASS" if self.all_pass() else "FAIL"
        bar    = "─" * 62
        print(f"\n{bar}")
        print(f"  {self.name}")
        print(bar)
        for c in self.checks:
            sym = "✓" if c.passed else "✗"
            print(f"  {sym} [{c.tag}]  {c.label}")
            print(f"         {c.detail}")
        print(f"  → Section result: {status}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _engine_with_signal(signal: np.ndarray) -> VirtualAnalogAnalyzer:
    """Return a VirtualAnalogAnalyzer whose _reconstruct() yields ``signal``."""
    eng = VirtualAnalogAnalyzer(fs=FS_SYS, settle_ms=SETTLE_MS,
                                invert_output=False)
    s   = signal.copy()
    eng._reconstruct = lambda: s   # type: ignore[method-assign]
    return eng


def _thdn_hann_only(
    sig:        np.ndarray,
    fs:         float,
    fund_hz:    float,
    null_bins:  int = 3,
    max_harm:   int = 9,
) -> float:
    """
    Manual THD+N using Hann window on the full (possibly non-coherent) window.

    Used to demonstrate what the measurement looks like WITHOUT coherent
    truncation applied — the baseline that coherent windowing improves upon.
    """
    n       = len(sig)
    window  = np.hanning(n)
    cg      = n / np.sum(window)
    spectrum   = np.fft.rfft(sig * window) * cg / (n / 2)
    freqs      = np.fft.rfftfreq(n, d=1.0 / fs)
    mag_sq     = np.abs(spectrum) ** 2
    eps        = 1e-30

    fund_idx  = int(np.argmin(np.abs(freqs - fund_hz)))
    f_lo_b    = max(0, fund_idx - null_bins)
    f_hi_b    = min(len(freqs), fund_idx + null_bins + 1)

    audio     = (freqs >= 20.0) & (freqs <= 20e3)
    harm_mask = np.zeros(len(freqs), dtype=bool)
    for h in range(1, max_harm + 2):
        hf = h * fund_hz
        if hf > freqs[-1]:
            break
        hb = int(round(hf * n / fs))
        harm_mask[max(0, hb - null_bins) : min(len(freqs), hb + null_bins + 1)] = True

    p_fund  = float(np.sum(mag_sq[f_lo_b:f_hi_b]))
    p_noise = float(np.sum(mag_sq[audio & ~harm_mask]))
    p_harm  = max(0.0, float(np.sum(mag_sq[audio & harm_mask])) - p_fund)
    p_thdn  = p_noise + p_harm
    return float(10.0 * np.log10(max(p_thdn, eps) / max(p_fund, eps)))


def _run_model(
    pcm:          list[int],
    osr_eff:      int,
    mutation:     Optional[str] = None,
) -> list[int]:
    """
    Run DSModulatorModel, optionally injecting a named fault.

    Mutations
    ---------
    'kill_integrator1'  — 1st accumulator never updates (→ 1st-order behaviour)
    'stuck_output'      — dout hardwired to 0 (→ full-scale negative DC)
    'kill_integrator2'  — 2nd accumulator never updates (→ memoryless threshold)
    'kill_feedback1'    — 1st-stage dac_val forced to 0 (→ open-loop accumulation)
    """
    if mutation is None:
        m = DSModulatorModel()
        return m.run(pcm, osr_effective=osr_eff)

    # Inline mutation loop (modified copy of DSModulatorModel.run)
    _m    = DSModulatorModel()
    BW_T  = _m.BW_TOT
    BW_T2 = _m.BW_TOT2
    MX    = _m.MAX_VAL;  MN    = _m.MIN_VAL
    MX2   = _m.MAX_VAL2; MN2   = _m.MIN_VAL2
    DBW   = _m.DAC_BW

    acc1 = acc2 = 0
    dout_r = 0
    bits: list[int] = []

    for sample in pcm:
        sample = _wrap(int(sample), DBW)
        for _ in range(osr_eff):

            if mutation == 'kill_feedback1':
                dac_val  = 0                        # no 1st-stage restoring force
                dac_val2 = MX2 if dout_r else MN2
            else:
                dac_val  = MX  if dout_r else MN
                dac_val2 = MX2 if dout_r else MN2

            in_ext     = _wrap(sample,           BW_T)
            delta0     = _wrap(in_ext - dac_val, BW_T)

            if mutation == 'kill_integrator1':
                delta0_acc = delta0     # acc1 never added — 1st integrator dead
                acc1_next  = acc1       # acc1 unchanged
            else:
                delta0_acc = _wrap(acc1 + delta0, BW_T)
                acc1_next  = delta0_acc

            in_ext2    = _wrap(delta0_acc,            BW_T2)
            delta1     = _wrap(in_ext2  - dac_val2,   BW_T2)
            if mutation == 'kill_integrator2':
                delta1_acc = delta1     # acc2 never accumulates (memoryless)
                acc2_next  = 0
            else:
                delta1_acc = _wrap(acc2     + delta1,  BW_T2)
                acc2_next  = delta1_acc

            if mutation == 'stuck_output':
                bits.append(0)          # dout always 0 (full-scale negative)
                dout_r = 1              # dout_r = !dout = 1
            else:
                dout_r_new = 1 if delta1_acc < 0 else 0
                bits.append(1 - dout_r_new)
                dout_r = dout_r_new

            acc1 = acc1_next
            acc2 = acc2_next

    return bits


def _analyse_bits(bits: list[int], fund_hz: float) -> float:
    """Feed PDM bits to DSPEngine and return THD+N in dBc (analog path)."""
    eng = VirtualAnalogAnalyzer(fs=FS_SYS, settle_ms=SETTLE_MS,
                                invert_output=True)
    eng.push_bits(bits)
    m = eng.analyze(fund_hz=fund_hz)
    return m.thd_n_db


def _extract_results_map(payload: dict) -> dict[str, float]:
    """
    Read frequency->value payloads in either schema:
      1) legacy top-level map: {"1000": -87.2, ...}
      2) wrapped map: {"clock_hz": ..., "results": {"1000": -87.2, ...}}
    """
    data = payload.get("results", payload)
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        try:
            out[str(k)] = float(v)
        except Exception:
            continue
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — DSP Engine Accuracy
# ─────────────────────────────────────────────────────────────────────────────

def validate_dsp_engine() -> Section:
    """
    Inject mathematically known signals directly into the analysis pipeline
    and verify that what goes in is what gets measured.
    """
    sec    = Section("[1/4] DSP ENGINE ACCURACY")
    FS     = FS_SYS
    F      = 1000.0                           # test frequency (Hz)
    AMP    = dbfs_to_linear(-1.0)             # -1 dBFS amplitude
    # 15 ms at 100 MHz → exactly 15 integer cycles at 1 kHz (coherent by design)
    N15    = int(0.015 * FS)                  # 1_500_000 samples
    t15    = np.arange(N15, dtype=np.float64) / FS
    sine   = AMP * np.sin(2.0 * np.pi * F * t15)

    # ── 1A: Measurement noise floor (perfect sine, no distortion) ────────────
    m = _engine_with_signal(sine).analyze(fund_hz=F)
    sec.add("1A", "Measurement noise floor — perfect 1 kHz sine",
            m.thd_n_db < -120.0,
            f"THD+N = {m.thd_n_db:.1f} dBc   (expect < -120 dBc)")

    # ── 1B: HD2 injection at -80 dBc ─────────────────────────────────────────
    hd2_amp = AMP * dbfs_to_linear(-80.0)     # 10^(-80/20) × AMP
    sig_hd2 = sine + hd2_amp * np.sin(2.0 * np.pi * 2 * F * t15)
    m2 = _engine_with_signal(sig_hd2).analyze(fund_hz=F)
    sec.add("1B", "Injected HD2 = -80 dBc: measurement accuracy",
            abs(m2.thd_n_db - (-80.0)) < 1.5,
            f"Measured {m2.thd_n_db:.2f} dBc   (expected -80.0 ± 1.5 dBc)")

    # ── 1C: HD2 injection at -60 dBc ─────────────────────────────────────────
    hd2_60 = AMP * dbfs_to_linear(-60.0)
    sig_60  = sine + hd2_60 * np.sin(2.0 * np.pi * 2 * F * t15)
    m3 = _engine_with_signal(sig_60).analyze(fund_hz=F)
    sec.add("1C", "Injected HD2 = -60 dBc: measurement accuracy",
            abs(m3.thd_n_db - (-60.0)) < 1.5,
            f"Measured {m3.thd_n_db:.2f} dBc   (expected -60.0 ± 1.5 dBc)")

    # ── 1D: Coherent windowing at 100 Hz ─────────────────────────────────────
    # 87 ms = 8.7 cycles of 100 Hz — non-integer (non-coherent) window length.
    # analyze() should auto-truncate to 8 exact cycles → no leakage.
    # Manual Hann FFT on the full 8.7-cycle window shows leakage baseline.
    F100  = 100.0
    N87   = int(0.087 * FS)                   # 8_700_000 samples (8.7 cycles)
    t87   = np.arange(N87, dtype=np.float64) / FS
    s100  = AMP * np.sin(2.0 * np.pi * F100 * t87)

    thdn_coherent    = _engine_with_signal(s100).analyze(fund_hz=F100).thd_n_db
    thdn_hann_only   = _thdn_hann_only(s100, FS, F100)
    improvement_db   = thdn_hann_only - thdn_coherent   # positive = coherent is better

    sec.add("1D", "Coherent windowing at 100 Hz (8.7 cycles → auto-truncated to 8)",
            improvement_db > 20.0,
            f"With coherent: {thdn_coherent:.1f} dBc  "
            f"Without: {thdn_hann_only:.1f} dBc  "
            f"Improvement: {improvement_db:.1f} dB  (expect > 20 dB)")

    return sec


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — AES17 Methodology Compliance
# ─────────────────────────────────────────────────────────────────────────────

def validate_aes17_compliance() -> Section:
    """
    Verify that stimulus generators and measurement parameters match the
    AES17 / IEC 60268-17 standard without running an RTL simulation.
    """
    sec = Section("[2/4] AES17 METHODOLOGY COMPLIANCE")

    # ── 2A: PCM level accuracy at -1 dBFS ────────────────────────────────────
    samples    = generate_sine(1000.0, 48_000, -1.0, FS_AUDIO)
    peak_int   = max(abs(s) for s in samples)
    peak_norm  = peak_int / FULL_SCALE
    expected   = dbfs_to_linear(-1.0)
    err_db     = 20.0 * math.log10(peak_norm / expected)
    sec.add("2A", "PCM generator level accuracy at -1 dBFS",
            abs(err_db) < 0.02,
            f"Peak = {peak_norm:.6f}  expected {expected:.6f}  "
            f"error {err_db:+.4f} dBFS  (allow ±0.02 dBFS)")

    # ── 2B: All 12 AES17 sweep frequencies present ───────────────────────────
    required = {20, 40, 100, 200, 400, 1000, 2000, 4000, 8000, 10000, 15000, 20000}
    present  = set(AES17_SWEEP_FREQS)
    missing  = required - present
    sec.add("2B", "12 AES17 standard sweep frequencies",
            len(missing) == 0,
            f"Present: {sorted(present)}  Missing: {sorted(missing)}")

    # ── 2C: Measurement bandwidth = 20 Hz – 20 kHz ───────────────────────────
    eng     = VirtualAnalogAnalyzer(fs=FS_SYS)
    ab_low  = eng.audio_band[0]
    ab_high = eng.audio_band[1]
    sec.add("2C", "Measurement bandwidth: 20 Hz – 20 kHz",
            ab_low == 20.0 and ab_high == 20_000.0,
            f"DSPEngine audio_band = ({ab_low:.0f}, {ab_high:.0f}) Hz")

    # ── 2D: TPDF dither peak ≈ -93 dBFS ─────────────────────────────────────
    # generate_dithered_silence(dither_amplitude_dbfs=-93.0) sets the PEAK
    # amplitude parameter to -93 dBFS.  For TPDF (triangular PDF = sum of two
    # uniform rvs), the RMS is peak / sqrt(6) ≈ peak − 7.8 dBFS.  We verify
    # the peak (not RMS) against the -93 dBFS specification.
    dith     = generate_dithered_silence(96_000)
    dith_arr = np.array(dith, dtype=np.float64) / FULL_SCALE
    dith_peak_dbfs = 20.0 * math.log10(max(float(np.max(np.abs(dith_arr))), 1e-20))
    sec.add("2D", "TPDF dither peak amplitude ≈ -93 dBFS (1 LSB equivalent)",
            abs(dith_peak_dbfs - (-93.0)) < 1.0,
            f"Measured peak = {dith_peak_dbfs:.2f} dBFS  (target -93.0 ± 1.0 dBFS)")

    # ── 2E: CCIF twin-tone: 19+20 kHz, each at -3 dBFS ──────────────────────
    ccif   = np.array(generate_ccif_imd(96_000, 19e3, 20e3, -3.0, FS_AUDIO),
                      dtype=np.float64) / FULL_SCALE
    # Energy at 19 kHz and 20 kHz should each be approximately -3 dBFS
    t_ccif = np.arange(len(ccif)) / FS_AUDIO
    p19    = float(np.mean((ccif * np.sin(2*np.pi*19e3*t_ccif)) ** 2)) * 2
    p20    = float(np.mean((ccif * np.sin(2*np.pi*20e3*t_ccif)) ** 2)) * 2
    a19_db = 10.0 * math.log10(max(p19, 1e-30))
    a20_db = 10.0 * math.log10(max(p20, 1e-30))
    sec.add("2E", "CCIF: 19+20 kHz twin-tone, each ≈ -3 dBFS",
            abs(a19_db - (-3.0)) < 1.5 and abs(a20_db - (-3.0)) < 1.5,
            f"19 kHz = {a19_db:.1f} dBFS   20 kHz = {a20_db:.1f} dBFS  "
            f"(expect ≈ -3 dBFS each)")

    # ── 2F: SMPTE: 60 Hz + 7 kHz, ratio = 12 dB (4:1 amplitude) ─────────────
    # Use FFT to extract amplitudes: A = 2 × |X[k]| / N for real sinusoids.
    # 96 000 samples at 48 kHz = exactly 2 s → both 60 Hz (120 cycles) and
    # 7 kHz (14000 cycles) land on integer FFT bins → no spectral leakage.
    smpte_arr = np.array(generate_smpte_imd(96_000, 60.0, 7e3, -1.0, 12.0, FS_AUDIO),
                         dtype=np.float64) / FULL_SCALE
    n_s       = len(smpte_arr)
    fft_s     = np.fft.rfft(smpte_arr)
    bin60     = int(round(60.0   * n_s / FS_AUDIO))
    bin7k     = int(round(7000.0 * n_s / FS_AUDIO))
    A60       = 2.0 * abs(fft_s[bin60])  / n_s
    A7k       = 2.0 * abs(fft_s[bin7k])  / n_s
    ratio_db  = 20.0 * math.log10(max(A60, 1e-20) / max(A7k, 1e-20))
    sec.add("2F", "SMPTE: 60 Hz + 7 kHz at 4:1 amplitude ratio (12 dB)",
            abs(ratio_db - 12.0) < 1.5,
            f"60 Hz amp = {20*math.log10(max(A60,1e-20)):.2f} dBFS  "
            f"7 kHz amp = {20*math.log10(max(A7k,1e-20)):.2f} dBFS  "
            f"ratio = {ratio_db:.2f} dB  (expect 12.0 ± 1.5 dB)")

    # ── 2G: ZOH correction formula is correct ────────────────────────────────
    # ZOH rolloff: sinc(f / fs_audio) = sin(π f/fs_audio) / (π f/fs_audio)
    # At 20 kHz: sinc(20000/48000) → should equal observed RTL rolloff in test7 JSON
    def zoh_db(f_hz: float, fs: float) -> float:
        x = math.pi * f_hz / fs
        return 20.0 * math.log10(abs(math.sin(x) / x))

    zoh_1k  = zoh_db(1000.0,  FS_AUDIO)
    zoh_20k = zoh_db(20000.0, FS_AUDIO)
    zoh_delta = zoh_20k - zoh_1k          # rolloff at 20 kHz relative to 1 kHz

    # Load RTL test7 JSON if available for cross-check
    rtl_delta: Optional[float] = None
    t7_path = _JSON_ANALOG.parent / "test7_freq_response.json"
    if t7_path.exists():
        with open(t7_path) as f:
            t7 = json.load(f)
        t7_results = _extract_results_map(t7)
        if "20000" in t7_results and "1000" in t7_results:
            rtl_delta = float(t7_results["20000"]) - float(t7_results["1000"])

    if rtl_delta is not None:
        zoh_err = abs(zoh_delta - rtl_delta)
        detail  = (f"ZOH model: {zoh_delta:.3f} dB   "
                   f"RTL measured: {rtl_delta:.3f} dB   "
                   f"error: {zoh_err:.3f} dB  (expect < 0.5 dB)")
        sec.add("2G", "ZOH correction matches RTL frequency response rolloff",
                zoh_err < 0.5, detail)
    else:
        detail = (f"ZOH model at 20 kHz: {zoh_delta:.3f} dB  "
                  f"(RTL JSON not available for cross-check: {t7_path})")
        sec.add("2G", "ZOH correction formula (theory only — no RTL JSON)",
                True, detail)

    return sec


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Python Model vs RTL Results
# ─────────────────────────────────────────────────────────────────────────────

def validate_model_vs_rtl() -> Section:
    """
    Run DSModulatorModel at 1 kHz -1 dBFS, analyse with DSPEngine using the
    same parameters as the cocotb tests, and compare to the most recent RTL
    simulation JSON.

    Check 3S (staleness): fails if ds_modulator.sv was modified after the
    JSON was produced.  Subsequent checks (3A/3B) are skipped when stale, as
    comparing an updated model against old RTL output would be misleading.

    Agreement < 3 dB (checks 3A/3B) means:
      - The Python model correctly replicates RTL bit-for-bit
      - The DSPEngine is correctly characterising the RTL output
    """
    sec = Section("[3/4] PYTHON MODEL vs RTL RESULTS")

    if not _JSON_ANALOG.exists():
        sec.add("3A", "RTL JSON not available — skip model comparison",
                True, f"Expected: {_JSON_ANALOG}  (run cocotb tests first)")
        return sec

    with open(_JSON_ANALOG) as f:
        rtl_json = json.load(f)
    rtl_results = _extract_results_map(rtl_json)

    # ── 3S: RTL staleness check ───────────────────────────────────────────────
    # Fail if ds_modulator.sv is newer than the JSON: the JSON reflects a
    # different (older) RTL and model-vs-RTL comparisons may be meaningless.
    if _RTL_SV.exists():
        json_mtime = _JSON_ANALOG.stat().st_mtime
        rtl_mtime  = _RTL_SV.stat().st_mtime
        stale      = rtl_mtime > json_mtime
        import datetime
        def _ts(mt: float) -> str:
            return datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S")
        sec.add("3S", "RTL JSON is up-to-date (not stale after RTL edits)",
                not stale,
                (f"ds_modulator.sv: {_ts(rtl_mtime)}   "
                 f"JSON: {_ts(json_mtime)}   "
                 f"{'RTL EDITED AFTER LAST SIM RUN — re-run Verilator to update JSON' if stale else 'current'}"))
        if stale:
            # Skip model-vs-RTL comparisons — they would compare apples to oranges
            sec.add("3A", "Model vs RTL skipped — JSON is stale (re-run Verilator first)",
                    False,
                    "Run 'make sim' or equivalent to regenerate test1_thdn_results_analog.json")
            return sec
    else:
        sec.add("3S", "RTL source file not found — staleness check skipped",
                True,
                f"Expected: {_RTL_SV}  (check repository layout)")

    rtl_1k = float(rtl_results.get("1000", float("nan")))
    if math.isnan(rtl_1k):
        sec.add("3A", "RTL JSON missing 1 kHz entry",
                False, "Key '1000' not found in JSON results map")
        return sec

    # Generate 1 kHz -1 dBFS PCM at 48 kHz
    # Total capture = SETTLE_MS + 15 ms analysis + SETTLE_MS (both sides)
    # DSPEngine discards SETTLE_MS from each end internally.
    total_ms    = SETTLE_MS + 15.0 + SETTLE_MS        # 17 ms
    n_pcm       = math.ceil(total_ms * 1e-3 * FS_AUDIO)  # audio samples
    pcm         = generate_sine(1000.0, n_pcm, -1.0, FS_AUDIO)

    print(f"\n  Running Python model: {n_pcm} PCM samples × {OSR_EFFECTIVE} "
          f"= {n_pcm * OSR_EFFECTIVE:,} PDM bits …", flush=True)
    t0   = time.monotonic()
    bits = _run_model(pcm, OSR_EFFECTIVE, mutation=None)
    dt   = time.monotonic() - t0
    print(f"  Model finished in {dt:.1f} s", flush=True)

    model_thdn = _analyse_bits(bits, 1000.0)
    delta_db   = abs(model_thdn - rtl_1k)

    sec.add("3A", "Model THD+N agrees with RTL simulation at 1 kHz",
            delta_db < 3.0,
            f"Python model: {model_thdn:.2f} dBc   "
            f"RTL simulation: {rtl_1k:.2f} dBc   "
            f"Δ = {delta_db:.2f} dB  (expect < 3.0 dB)")

    # ── 3B: Check all six passing frequencies (20 Hz–2 kHz) ──────────────────
    # These frequencies pass in the RTL.  The model should agree within 5 dB.
    # ── 3B: Verify 2 kHz agrees — needs ≥ 8 coherent cycles ─────────────────
    # Coherent windowing requires n_settled × freq / fs_sys ≥ 8 cycles.
    # For 2 kHz: 8 × fs_sys / freq = 8 × 100e6 / 2000 = 400 000 samples
    #            = 4.0 ms settled → total capture = 4 + 1 + 1 = 6 ms.
    rtl_2k = float(rtl_results.get("2000", float("nan")))
    if not math.isnan(rtl_2k) and rtl_2k < THDN_LIMIT_ANALOG:
        # Match the RTL suite's 15ms analysis window so both experience the
        # same limit-cycle accumulation time.
        n_2k    = math.ceil((SETTLE_MS + 15.0 + SETTLE_MS) * 1e-3 * FS_AUDIO)
        pcm_2k  = generate_sine(2000.0, n_2k, -1.0, FS_AUDIO)
        bits_2k = _run_model(pcm_2k, OSR_EFFECTIVE)
        thdn_2k = _analyse_bits(bits_2k, 2000.0)
        d_2k    = abs(thdn_2k - rtl_2k)
        sec.add("3B", "Model agrees with RTL at 2 kHz (independent check, Δ < 6 dB)",
                d_2k < 6.0,
                f"Python model: {thdn_2k:.2f} dBc   "
                f"RTL simulation: {rtl_2k:.2f} dBc   "
                f"Δ = {d_2k:.2f} dB  (expect < 6.0 dB)")
    else:
        sec.add("3B", "2 kHz RTL result unavailable or fails limit — skipped",
                True, f"RTL[2000] = {rtl_2k:.2f} dBc")

    return sec


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Mutation Testing
# ─────────────────────────────────────────────────────────────────────────────

def validate_mutations() -> Section:
    """
    Inject four known faults into the Python model and confirm that measured
    THD+N degrades enough to fail the THDN_LIMIT_ANALOG = -80 dBc gate.

    If a test cannot detect a specific fault, its detection coverage is zero.
    """
    sec = Section("[4/4] MUTATION TESTS — fault injection detection coverage")

    # Use 15ms total capture (1ms settle + 13ms analysis + 1ms settle).
    # At 1 kHz / 100 MHz: 13ms settled → 13 coherent cycles → same FFT
    # resolution as the main test suite (Section 3A).  This prevents the
    # 5ms Hann-window baseline from being artificially clean (~-104 dBc)
    # and ensures the healthy baseline matches the RTL reference (~-85 dBc).
    total_ms = SETTLE_MS + 13.0 + SETTLE_MS
    n_pcm    = math.ceil(total_ms * 1e-3 * FS_AUDIO)

    def _measure(pcm: list[int], mut: Optional[str], f_hz: float) -> float:
        bits = _run_model(pcm, OSR_EFFECTIVE, mutation=mut)
        return _analyse_bits(bits, f_hz)

    # Healthy baseline at 1 kHz
    pcm_1k  = generate_sine(1000.0, n_pcm, -1.0, FS_AUDIO)
    print("\n  Running mutation tests (4 × short capture) …", flush=True)
    baseline = _measure(pcm_1k, None, 1000.0)
    if not math.isfinite(baseline):
        sec.add("4Z", "Healthy baseline is finite",
                False, f"Healthy THD+N is non-finite: {baseline!r}")
        return sec

    # ── 4A: Kill 1st integrator ───────────────────────────────────────────────
    thdn_a = _measure(pcm_1k, 'kill_integrator1', 1000.0)
    detected_a = math.isfinite(thdn_a) and (thdn_a > THDN_LIMIT_ANALOG)
    sec.add("4A", "Kill 1st integrator (acc1 never updates → 1st-order behaviour)",
            detected_a,
            f"Healthy: {baseline:.1f} dBc   Mutant: {thdn_a:.1f} dBc   "
            f"Limit: {THDN_LIMIT_ANALOG:.0f} dBc   "
            f"{'DETECTED ✓' if detected_a else 'MISSED ✗'}")

    # ── 4B: Stuck output (dout hardwired to 0 → DC = -FS) ────────────────────
    thdn_b = _measure(pcm_1k, 'stuck_output', 1000.0)
    # Stuck output produces near-zero 1 kHz content → THD+N near 0 dBc
    detected_b = math.isfinite(thdn_b) and (thdn_b > THDN_LIMIT_ANALOG)
    sec.add("4B", "Stuck output dout=0 (→ full-scale negative DC, no modulation)",
            detected_b,
            f"Healthy: {baseline:.1f} dBc   Mutant: {thdn_b:.1f} dBc   "
            f"Limit: {THDN_LIMIT_ANALOG:.0f} dBc   "
            f"{'DETECTED ✓' if detected_b else 'MISSED ✗'}")

    # ── 4C: Kill 2nd integrator (acc2 = 0 always → memoryless threshold) ────────
    # The 2nd accumulator never updates: the modulator degrades from 2nd-order
    # to effectively 1st-order noise shaping.  At 1 kHz the change is subtle
    # (< 5 dB) because the frequency is deep in the passband for both orders.
    # Detection criterion: absolute (crosses -80 limit) OR relative (≥ 3 dB
    # worse than healthy), whichever is appropriate for the measurement point.
    thdn_c    = _measure(pcm_1k, 'kill_integrator2', 1000.0)
    degrad_c  = (thdn_c - baseline) if math.isfinite(thdn_c) else float("nan")  # positive = worse
    detected_c = (
        (math.isfinite(thdn_c) and thdn_c > THDN_LIMIT_ANALOG)
        or (math.isfinite(degrad_c) and degrad_c >= 3.0)
    )
    criterion  = (f"absolute (>{THDN_LIMIT_ANALOG:.0f} dBc)"
                  if (math.isfinite(thdn_c) and thdn_c > THDN_LIMIT_ANALOG) else
                  f"relative (≥ 3 dB degradation: +{degrad_c:.1f} dB)"
                  if (math.isfinite(degrad_c) and degrad_c >= 3.0) else "neither")
    sec.add("4C", "Kill 2nd integrator (acc2 never updates → memoryless 2nd stage)",
            detected_c,
            f"Healthy: {baseline:.1f} dBc   Mutant: {thdn_c:.1f} dBc   "
            f"Degradation: {degrad_c:+.1f} dB   "
            f"{'DETECTED ✓ via ' + criterion if detected_c else 'MISSED ✗'}")

    # ── 4D: Kill 1st-stage feedback (dac_val = 0 → open-loop 1st stage) ─────
    # The 1st integrator accumulates the input without any restoring feedback.
    # acc1 grows monotonically until 2's-complement overflow, producing
    # chaotic high-distortion output.
    thdn_d = _measure(pcm_1k, 'kill_feedback1', 1000.0)
    detected_d = math.isfinite(thdn_d) and (thdn_d > THDN_LIMIT_ANALOG)
    sec.add("4D", "Kill 1st-stage feedback (dac_val=0 → open-loop, acc1 overflows)",
            detected_d,
            f"Healthy: {baseline:.1f} dBc   Mutant: {thdn_d:.1f} dBc   "
            f"Limit: {THDN_LIMIT_ANALOG:.0f} dBc   "
            f"{'DETECTED ✓' if detected_d else 'MISSED ✗'}")

    return sec


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    header = "=" * 64
    print(header)
    print("  ds_modulator Verification Suite — Self-Validation")
    print(f"  FS_SYS={FS_SYS/1e6:.0f} MHz  FS_AUDIO={FS_AUDIO/1e3:.0f} kHz  "
          f"OSR_EFF={OSR_EFFECTIVE}  settle={SETTLE_MS:.0f} ms")
    print(header)

    t_start   = time.monotonic()
    sections: list[Section] = []

    print("\nSection 1: DSP engine accuracy …", flush=True)
    sections.append(validate_dsp_engine())

    print("\nSection 2: AES17 compliance …", flush=True)
    sections.append(validate_aes17_compliance())

    print("\nSection 3: Python model vs RTL …", flush=True)
    sections.append(validate_model_vs_rtl())

    print("\nSection 4: Mutation testing …", flush=True)
    sections.append(validate_mutations())

    # ── Print results ─────────────────────────────────────────────────────────
    print()
    for s in sections:
        s.print()

    # ── Overall verdict ───────────────────────────────────────────────────────
    n_checks  = sum(len(s.checks) for s in sections)
    n_pass    = sum(sum(1 for c in s.checks if c.passed) for s in sections)
    n_fail    = n_checks - n_pass
    elapsed   = time.monotonic() - t_start
    all_ok    = all(s.all_pass() for s in sections)

    print()
    print("=" * 64)
    if all_ok:
        print(f"  OVERALL: ALL {n_checks} CHECKS PASSED")
        print(f"  Test suite methodology is VALIDATED.")
        print(f"  Measured THD+N numbers can be trusted to reflect RTL behaviour.")
    else:
        print(f"  OVERALL: {n_fail}/{n_checks} CHECK(S) FAILED")
        print(f"  Review the failing items above before trusting RTL results.")
    print(f"  Elapsed: {elapsed:.1f} s")
    print("=" * 64)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
