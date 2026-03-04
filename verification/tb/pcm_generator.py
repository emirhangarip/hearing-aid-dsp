"""
stimulus/pcm_generator.py — HiFi PCM Stimulus Library
======================================================
Generates all standard AES17 / IEC 60268-17 test signals as 32-bit signed
integer PCM streams ready to be clocked into ds_modulator.din.

Direct-modulator drive model
─────────────────────────────
ds_modulator expects PCM samples held stable for OSR clock cycles:

    clk  ─┬─┬─┬─┬─ ... ─┬─┬─┬─┬─ ... ─┬─
    din  ──┴─sample_0────┴─sample_1────┴─ ...
           ← OSR cycles → ← OSR cycles →

At fs_system=100 MHz and OSR=64:
    f_audio = 100 MHz / 64 ≈ 1.5625 MHz  ← modulator-internal audio rate
    
But the RTL is typically driven from an I2S receiver that runs at 48 kHz.
We provide both:
  • generate_*()  → list[int]  at fs_audio (to be repeated OSR times by driver)
  • expand_to_bitstream() → the repeated version ready for direct simulation

All frequencies in Hz, all levels in dBFS, all outputs in 32-bit signed int.
"""

from __future__ import annotations

import math
import struct
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import numpy.typing as npt


# ── Constants ─────────────────────────────────────────────────────────────────

WORD_BITS    = 32
FULL_SCALE   = (1 << (WORD_BITS - 1)) - 1   # 2 147 483 647

# Standard AES17 test frequencies (Hz)
AES17_SWEEP_FREQS = [20, 40, 100, 200, 400, 1_000, 2_000,
                     4_000, 8_000, 10_000, 15_000, 20_000]

# Standard linearity levels (dBFS)
AES17_LINEARITY_LEVELS = [-1, -3, -6, -10, -20, -40, -60, -80]


# ── Helper ────────────────────────────────────────────────────────────────────

def _to_int32(signal: npt.NDArray[np.float64]) -> list[int]:
    """Clip and convert a float64 signal (±1.0 normalised) to 32-bit signed ints."""
    clipped = np.clip(signal * FULL_SCALE, -FULL_SCALE, FULL_SCALE)
    return [int(x) for x in np.round(clipped).astype(np.int64)]


def dbfs_to_linear(dbfs: float) -> float:
    """Convert dBFS amplitude to a normalised linear factor (1.0 = FS)."""
    return 10.0 ** (dbfs / 20.0)


# ── Single-tone ───────────────────────────────────────────────────────────────

def generate_sine(
    freq_hz: float,
    n_samples: int,
    amplitude_dbfs: float = -1.0,
    fs: float = 48_000.0,
    phase_rad: float = 0.0,
) -> list[int]:
    """
    Single-tone sine wave, high-precision (float64 accumulator).

    Parameters
    ----------
    freq_hz : float
        Frequency of the test tone in Hz.
    n_samples : int
        Number of PCM samples to generate.
    amplitude_dbfs : float
        Level relative to full-scale digital (dBFS), default −1 dBFS.
    fs : float
        PCM sample rate in Hz.
    phase_rad : float
        Initial phase in radians (default 0).

    Returns
    -------
    list[int]  signed 32-bit integers
    """
    amp = dbfs_to_linear(amplitude_dbfs)
    t   = np.arange(n_samples, dtype=np.float64) / fs
    sig = amp * np.sin(2.0 * np.pi * freq_hz * t + phase_rad)
    return _to_int32(sig)


# ── Silence ───────────────────────────────────────────────────────────────────

def generate_silence(n_samples: int) -> list[int]:
    """All-zero PCM (digital silence for idle-channel test)."""
    return [0] * n_samples


# ── Dithered silence (dynamic range test) ────────────────────────────────────

def generate_dithered_silence(
    n_samples: int,
    dither_amplitude_dbfs: float = -93.0,   # ≈ 1 LSB for 16-bit equivalent
    seed: int = 42,
) -> list[int]:
    """
    TPDF (triangular probability density function) dither.

    Used in the AES17 dynamic-range test: the noise floor is measured with
    dither present but no signal, then the dynamic range is calculated as
    the ratio of full-scale to the noise floor.

    TPDF = sum of two uniform random variables, which has a triangular PDF
    and is the lowest-order dither that eliminates granulation noise.
    """
    rng    = np.random.default_rng(seed)
    amp    = dbfs_to_linear(dither_amplitude_dbfs)
    u1     = rng.uniform(-1.0, 1.0, n_samples)
    u2     = rng.uniform(-1.0, 1.0, n_samples)
    dither = amp * 0.5 * (u1 + u2)   # TPDF, peak = amp
    return _to_int32(dither)


# ── Dynamic range stimulus: -60 dBFS sine + TPDF dither ─────────────────────

def generate_dynamic_range_stimulus(
    n_samples: int,
    fund_hz: float = 1_000.0,
    signal_level_dbfs: float = -60.0,
    fs: float = 48_000.0,
) -> list[int]:
    """
    AES17 §6.4 dynamic range / noise floor test.

    A −60 dBFS sine is combined with TPDF dither at −93 dBFS.
    SNR is measured relative to a full-scale (0 dBFS) hypothetical reference.
    """
    amp    = dbfs_to_linear(signal_level_dbfs)
    t      = np.arange(n_samples, dtype=np.float64) / fs
    sine   = amp * np.sin(2.0 * np.pi * fund_hz * t)

    rng    = np.random.default_rng(99)
    dither = 0.5 * (rng.uniform(-1.0, 1.0, n_samples) +
                    rng.uniform(-1.0, 1.0, n_samples)) * dbfs_to_linear(-93.0)

    return _to_int32(sine + dither)


# ── CCIF / ITU IMD twin-tone (19 kHz + 20 kHz) ───────────────────────────────

def generate_ccif_imd(
    n_samples: int,
    f1_hz: float = 19_000.0,
    f2_hz: float = 20_000.0,
    amplitude_dbfs: float = -3.0,   # each tone at half power → sum at 0 dBFS peak
    fs: float = 48_000.0,
) -> list[int]:
    """
    CCIF (IEC 60268-3) twin-tone IMD test.

    Two equal-amplitude tones at f1 and f2.  Key IMD products:
      • Difference frequency : |f2 − f1| = 1 kHz  (falls in audio band)
      • Second order         : f1+f2 = 39 kHz, f2−f1 = 1 kHz
      • Third order          : 2f1−f2 = 18 kHz, 2f2−f1 = 21 kHz

    The 1 kHz product is the most audibly significant and must be < −80 dBr.

    Default amplitudes: each tone at −3 dBFS so the combined peak ≈ 0 dBFS.
    """
    amp = dbfs_to_linear(amplitude_dbfs)
    t   = np.arange(n_samples, dtype=np.float64) / fs
    sig = amp * (np.sin(2.0 * np.pi * f1_hz * t) +
                 np.sin(2.0 * np.pi * f2_hz * t))
    # Hard-clip guard (peak could reach 2×amp)
    sig = np.clip(sig, -1.0, 1.0)
    return _to_int32(sig)


# ── SMPTE IMD (60 Hz + 7 kHz, 4:1 ratio) ────────────────────────────────────

def generate_smpte_imd(
    n_samples: int,
    f_low_hz: float = 60.0,
    f_high_hz: float = 7_000.0,
    level_dbfs: float = -1.0,
    ratio_db: float = 12.0,    # low tone 12 dB louder than high tone (4:1)
    fs: float = 48_000.0,
) -> list[int]:
    """
    SMPTE (AES17 §6.3.3) intermodulation distortion test.

    Low-frequency tone (60 Hz) is 12 dB (4×) louder than the 7 kHz tone.
    Key IMD sidebands around 7 kHz:  7 kHz ± 60 Hz, 7 kHz ± 120 Hz, etc.
    The 7 kHz tone's AM sidebands reveal non-linearity at audible levels.
    """
    amp_total = dbfs_to_linear(level_dbfs)
    amp_low   = amp_total * (10 ** (ratio_db / 20)) / (1 + 10 ** (ratio_db / 20))
    amp_high  = amp_total * 1.0 / (1 + 10 ** (ratio_db / 20))

    t   = np.arange(n_samples, dtype=np.float64) / fs
    sig = (amp_low  * np.sin(2.0 * np.pi * f_low_hz  * t) +
           amp_high * np.sin(2.0 * np.pi * f_high_hz * t))
    sig = np.clip(sig, -1.0, 1.0)
    return _to_int32(sig)


# ── Square wave (slew-rate / dynamic headroom) ────────────────────────────────

def generate_square_wave(
    n_samples: int,
    freq_hz: float = 1_000.0,
    amplitude_dbfs: float = -1.0,
    fs: float = 48_000.0,
) -> list[int]:
    """
    Ideal digital square wave at freq_hz.

    Tests the modulator's ability to handle abrupt transitions without
    clipping the accumulators or producing tonal artefacts at harmonics.
    The spectrum should show the expected Fourier series: odd harmonics only.
    """
    amp = dbfs_to_linear(amplitude_dbfs)
    t   = np.arange(n_samples, dtype=np.float64) / fs
    sig = amp * np.sign(np.sin(2.0 * np.pi * freq_hz * t))
    # sign() returns 0 at zero-crossings — force to +1 or -1
    sig[sig == 0] = 1.0
    return _to_int32(sig)


# ── Frequency-sweep ramp (constant tone per segment) ─────────────────────────

def generate_frequency_sweep(
    frequencies_hz: list[float],
    samples_per_tone: int,
    amplitude_dbfs: float = -1.0,
    fs: float = 48_000.0,
) -> list[tuple[float, list[int]]]:
    """
    Generate a sequence of steady-state tones, one per frequency.

    Returns a list of (frequency_hz, pcm_samples) tuples so the testbench
    can analyse each tone segment separately and build a frequency-response
    or THD+N-vs-frequency curve.

    Parameters
    ----------
    frequencies_hz : list[float]
        List of test frequencies in ascending order.
    samples_per_tone : int
        PCM samples to generate per frequency (must be enough for the DSP
        engine's settle window + analysis window).
        Recommendation: max(4096, int(fs * 0.020))  ← ≥ 20 ms per tone.
    amplitude_dbfs : float
        Level for all tones (default −1 dBFS).
    """
    result: list[tuple[float, list[int]]] = []
    for f in frequencies_hz:
        samples = generate_sine(f, samples_per_tone, amplitude_dbfs, fs)
        result.append((f, samples))
    return result


# ── Linearity sweep (constant freq, varying level) ───────────────────────────

def generate_linearity_sweep(
    levels_dbfs: list[float],
    samples_per_level: int,
    freq_hz: float = 1_000.0,
    fs: float = 48_000.0,
) -> list[tuple[float, list[int]]]:
    """
    Generate 1 kHz tones at multiple amplitudes for linearity / DNR testing.

    Returns list of (level_dbfs, pcm_samples) tuples.
    """
    result: list[tuple[float, list[int]]] = []
    for level in levels_dbfs:
        samples = generate_sine(freq_hz, samples_per_level, level, fs)
        result.append((level, samples))
    return result


# ── Real audio file loader ─────────────────────────────────────────────────────

def load_pcm_file(
    filepath: str | Path,
    target_fs: float = 48_000.0,
    max_samples: Optional[int] = None,
    word_bits: int = 32,
) -> tuple[list[int], float]:
    """
    Load a WAV / FLAC / AIFF audio file as 32-bit signed PCM samples.

    Requires the ``soundfile`` package (pip install soundfile).
    Returns (samples_left_channel, actual_sample_rate).

    The file's native sample rate is returned so the caller can choose
    whether to resample or accept the difference.  No automatic resampling
    is performed (to avoid introducing artefacts in the reference signal).

    If the file has multiple channels, only the first (left) channel is used.
    """
    try:
        import soundfile as sf
    except ImportError:
        raise ImportError(
            "soundfile is required to load audio files.\n"
            "Install with:  pip install soundfile\n"
            "On Linux you may also need:  sudo apt install libsndfile1"
        )

    data, native_fs = sf.read(str(filepath), dtype="float64", always_2d=True)
    mono = data[:, 0]   # left channel only

    if max_samples is not None:
        mono = mono[:max_samples]

    if abs(native_fs - target_fs) > 1.0:
        warnings.warn(
            f"Audio file sample rate ({native_fs:.0f} Hz) differs from "
            f"target ({target_fs:.0f} Hz).  No resampling applied.  "
            f"THD+N results will be inaccurate if rates differ significantly.",
            stacklevel=2,
        )

    # Scale to 32-bit full-scale
    scale = (1 << (word_bits - 1)) - 1
    clipped = np.clip(mono, -1.0, 1.0)
    int_samples = [int(x) for x in np.round(clipped * scale).astype(np.int64)]
    return int_samples, float(native_fs)


# ── Direct-modulator bitstream expander ──────────────────────────────────────

def expand_to_modulator_input(
    pcm_samples: list[int],
    osr: int = 64,
) -> list[int]:
    """
    Repeat each PCM sample OSR times to drive ds_modulator.din directly.

    In the RTL, the I2S receiver feeds one audio sample into the modulator
    for each oversampling period.  When bypassing I2S in unit tests, we
    replicate each sample OSR times so the modulator sees a stable input
    for its full accumulation window.

    Parameters
    ----------
    pcm_samples : list[int]
        Audio-rate PCM samples (e.g., at 48 kHz).
    osr : int
        Oversampling ratio (default 64, matching the RTL parameter).

    Returns
    -------
    list[int]
        Expanded sample list suitable for cycle-by-cycle modulator stimulus.
        Length = len(pcm_samples) × osr.
    """
    result: list[int] = []
    for s in pcm_samples:
        result.extend([s] * osr)
    return result