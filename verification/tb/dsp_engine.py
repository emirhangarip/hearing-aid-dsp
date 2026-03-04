"""
dsp_engine.py — Virtual Analog Analyzer for PCM Δ∑ Modulator Verification
==========================================================================
Principal Engineer Reference
──────────────────────────────────────────────────────────────────────────
Architecture under test: 2nd-order, 46-bit internal precision (BW_TOT2)
RTL quantizer quirk:  dout = !dout_r
  • dout_r  = sign bit of 2nd accumulator  (1 → accumulator negative)
  • dout_r  = 1 → feedback DAC = +MAX_VAL  → pushes accumulator positive
  • Therefore a POSITIVE pcm input produces MORE dout=1 pulses.
  • Direct mapping: dout=1 → +1.0, dout=0 → -1.0 is CORRECT for amplitude,
    but the PHASE is inverted relative to the input because the feedback sign
    convention (dout_r drives dac_val, not dout) produces a polarity flip.
  • Correction: self._invert_output = True  →  pcm_reconstructed *= -1

AES17 Implementation Notes
──────────────────────────────────────────────────────────────────────────
• Reconstruction filter : 6th-order Butterworth LPF  fc = 20 kHz
• Settling buffer       : First 5 ms of bitstream discarded (= 500 000
                          samples at fs=100 MHz) to bypass filter transients
                          and RTL accumulator warm-up.
• Window                : Hann (coherent gain correction applied)
• THD+N                 : Total in-band power minus fundamental, relative to
                          the fundamental.
• Brick-wall band       : 20 Hz – 20 kHz applied in frequency domain before
                          noise integration.
"""

from __future__ import annotations

import math
import os
import warnings
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import numpy.typing as npt
from scipy import signal as sp_signal

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MPL_AVAILABLE = True
except ImportError:
    _MPL_AVAILABLE = False
    warnings.warn("matplotlib not found – PSD plots will be skipped.", stacklevel=2)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class AudioMetrics:
    """AES17-style audio quality metrics from a single analysis run."""

    thd_n_db:                  float = float("nan")   # THD+N in dBc
    snr_db:                    float = float("nan")   # SNR in dB
    sinad_db:                  float = float("nan")   # SINAD in dB
    sfdr_db:                   float = float("nan")   # Strongest spur in dBc

    noise_rms_fs:              float = float("nan")   # RMS noise, FS-normalised
    dc_offset_fs:              float = float("nan")   # DC offset, FS-normalised

    detected_fund_hz:          float = float("nan")
    fundamental_amplitude_dbfs: float = float("nan")

    limit_cycle_detected:      bool  = False
    limit_cycle_freqs_hz:      list  = field(default_factory=list)

    def __str__(self) -> str:
        dc_pct = self.dc_offset_fs * 100.0
        nr_dbfs = (20 * math.log10(max(self.noise_rms_fs, 1e-18))
                   if not math.isnan(self.noise_rms_fs) else float("nan"))
        lines = [
            "──── AudioMetrics ────────────────────────────",
            f"  THD+N          : {self.thd_n_db:.3f} dBc",
            f"  SNR            : {self.snr_db:.3f} dB",
            f"  SINAD          : {self.sinad_db:.3f} dB",
            f"  SFDR           : {self.sfdr_db:.3f} dBc",
            f"  Noise RMS (FS) : {self.noise_rms_fs:.2e}  ({nr_dbfs:.1f} dBFS)",
            f"  DC Offset (FS) : {self.dc_offset_fs:.6f}  ({dc_pct:.4f} %)",
            f"  Fund detected  : {self.detected_fund_hz:.2f} Hz"
            f" @ {self.fundamental_amplitude_dbfs:.2f} dBFS",
            (f"  Limit cycle    : YES – {self.limit_cycle_freqs_hz}"
             if self.limit_cycle_detected else
             "  Limit cycle    : None detected"),
            "──────────────────────────────────────────────",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# VirtualAnalogAnalyzer
# ---------------------------------------------------------------------------

class VirtualAnalogAnalyzer:
    """
    Reconstruct and characterise the PDM bitstream from ds_modulator RTL.

    Parameters
    ----------
    fs : float
        Bitstream clock in Hz (default 100 MHz).
    lpf_order : int
        Order of Butterworth reconstruction LPF (default 6).
    lpf_cutoff_hz : float
        -3 dB corner of reconstruction filter in Hz (default 20 kHz).
    settle_ms : float
        Milliseconds of warm-up to discard (default 5 ms = 500 k samples).
    audio_band_hz : tuple
        Brick-wall integration limits for AES17 noise measurement (default
        20 Hz – 20 kHz).
    invert_output : bool
        Negate the reconstructed waveform to correct the RTL polarity flip
        introduced by ``assign dout = !dout_r`` (default True).
    limit_cycle_threshold_db : float
        In-band spur level above which a limit-cycle warning is issued,
        relative to the fundamental (default -80 dBc).
    """

    def __init__(
        self,
        fs: float = 100e6,
        lpf_order: int = 6,
        lpf_cutoff_hz: float = 20e3,
        settle_ms: float = 5.0,
        audio_band_hz: tuple = (20.0, 20e3),
        invert_output: bool = True,
        limit_cycle_threshold_db: float = -80.0,
    ) -> None:
        self.fs = float(fs)
        self.lpf_order = int(lpf_order)
        self.lpf_cutoff_hz = float(lpf_cutoff_hz)
        self.settle_ms = float(settle_ms)
        self.audio_band = tuple(float(f) for f in audio_band_hz)
        self.invert_output = bool(invert_output)
        self.limit_cycle_threshold_db = float(limit_cycle_threshold_db)

        self._raw_bits: list[int] = []
        self._sos: Optional[npt.NDArray] = None

        # Cached for plotting
        self._last_freqs:   Optional[npt.NDArray] = None
        self._last_psd:     Optional[npt.NDArray] = None
        self._last_metrics: Optional[AudioMetrics] = None
        self._last_fund_hz: float = 0.0

    # ---------------------------------------------------------------- filter

    def _build_filter(self) -> npt.NDArray:
        if self._sos is None:
            wn = self.lpf_cutoff_hz / (self.fs / 2.0)
            if not (0.0 < wn < 1.0):
                raise ValueError(
                    f"lpf_cutoff_hz={self.lpf_cutoff_hz} Hz produces wn={wn:.4f}; "
                    f"must be in (0, 1) relative to Nyquist = {self.fs/2:.0f} Hz."
                )
            self._sos = sp_signal.butter(
                self.lpf_order, wn, btype="low", analog=False, output="sos"
            )
        return self._sos  # type: ignore[return-value]

    # ----------------------------------------------------------- data feed

    def push_bits(self, bits) -> None:
        """
        Append PDM samples to the internal buffer.

        Accepts any array-like of integers (0 or 1) or booleans.
        May be called multiple times across simulation phases.
        """
        if isinstance(bits, np.ndarray):
            self._raw_bits.extend(bits.ravel().astype(int).tolist())
        else:
            self._raw_bits.extend(int(b) & 1 for b in bits)

    def clear(self) -> None:
        """Reset all accumulated data and cached results."""
        self._raw_bits.clear()
        self._last_freqs = self._last_psd = self._last_metrics = None
        self._last_fund_hz = 0.0

    def n_bits(self) -> int:
        """Number of PDM samples currently buffered."""
        return len(self._raw_bits)

    @property
    def last_freqs(self) -> Optional[npt.NDArray]:
        """Frequency axis (Hz) from the most recent analyze() call."""
        return self._last_freqs

    @property
    def last_psd(self) -> Optional[npt.NDArray]:
        """Power spectrum (amplitude-squared) from the most recent analyze() call."""
        return self._last_psd

    # ------------------------------------------------------ reconstruction

    def _reconstruct(self) -> npt.NDArray[np.float64]:
        """
        Map raw PDM bits → bipolar float → LPF → settled waveform.

        RTL inversion correction
        ─────────────────────────
        The RTL uses ``assign dout = !dout_r`` where dout_r is the sign bit
        of the 2nd-stage accumulator.  Sign bit = 0 (positive) → dout_r=0
        → dout=1.  Since a *positive* PCM input drives the accumulator
        positive, dout=1 corresponds to positive signal, so the amplitude
        mapping (1→+1, 0→-1) is *correct* for magnitude, but the **phase**
        ends up inverted because the loop feedback is driven by dout_r, not
        dout.  Multiplying by -1 restores the correct polarity.
        """
        if len(self._raw_bits) == 0:
            raise RuntimeError("No PDM bits pushed.  Call push_bits() first.")

        raw = np.asarray(self._raw_bits, dtype=np.float64)

        # 0/1  →  ±1.0 bipolar
        bipolar = 2.0 * raw - 1.0

        # Polarity correction for RTL inversion
        if self.invert_output:
            bipolar *= -1.0

        # Zero-phase Butterworth reconstruction (sosfiltfilt avoids transients)
        sos = self._build_filter()
        filtered = sp_signal.sosfiltfilt(sos, bipolar)

        # Discard settling buffer on both ends.
        # sosfiltfilt is zero-phase but introduces edge transients at the
        # beginning and end of the record.
        n_settle = int(round(self.settle_ms * 1e-3 * self.fs))
        if (2 * n_settle) >= len(filtered):
            raise ValueError(
                f"Settling buffers ({2*n_settle} samples total = "
                f"{2*self.settle_ms:.2f} ms) exceed available bitstream length "
                f"({len(filtered)} samples). "
                f"Increase simulation capture time."
            )
        settled = filtered[n_settle:-n_settle]
        print(
            f"[DSP Engine] Bitstream: {len(raw):,} bits  |  "
            f"Settled: {len(settled):,} samples  "
            f"({len(settled)/self.fs*1e3:.2f} ms)"
        )
        return settled

    # --------------------------------------------------- AES17 analysis

    def analyze(
        self,
        fund_hz: float,
        max_harmonics: int = 9,
        null_bins: int = 3,
    ) -> AudioMetrics:
        """
        Full AES17-grade analysis for a single-tone test.

        Parameters
        ----------
        fund_hz : float
            Nominal fundamental frequency (Hz) of the test tone.
        max_harmonics : int
            Number of harmonics above the fundamental to null out when
            computing the noise residual (HD2 … HD(max_harmonics+1)).
        null_bins : int
            Half-width (bins) of the rectangular null window applied around
            each harmonic and the fundamental.

        Returns
        -------
        AudioMetrics
        """
        metrics = AudioMetrics()
        waveform = self._reconstruct()

        # ── Coherent windowing ─────────────────────────────────────────────
        # Truncate to an integer number of tone cycles to eliminate spectral
        # leakage from non-integer bin positions (Hann-window sidelobes).
        # This mirrors the same technique used in analyze_core_pdm().
        # Only activates when ≥ 8 complete cycles exist in the settled window;
        # below that threshold the full window is used with the Hann taper.
        if fund_hz > 0.0:
            n_cycles = int(len(waveform) * fund_hz / self.fs)
            if n_cycles >= 8:
                n_coherent = int(round(n_cycles * self.fs / fund_hz))
                if 256 <= n_coherent <= len(waveform):
                    waveform = waveform[:n_coherent]

        n = len(waveform)
        eps = 1e-30

        # ── 1. Hann window with coherent-gain correction ───────────────────
        window     = np.hanning(n)
        cg         = n / np.sum(window)          # amplitude correction
        windowed   = waveform * window

        # ── 2. Single-sided amplitude-squared spectrum ─────────────────────
        spectrum      = np.fft.rfft(windowed) * cg / (n / 2)
        freqs         = np.fft.rfftfreq(n, d=1.0 / self.fs)
        magnitude_sq  = np.abs(spectrum) ** 2

        self._last_freqs   = freqs
        self._last_psd     = magnitude_sq
        self._last_fund_hz = fund_hz

        # ── 3. Locate fundamental ──────────────────────────────────────────
        # In this verification flow, fund_hz is known from the generated
        # stimulus. Lock to the nearest FFT bin to avoid spur mis-detection at
        # very low frequencies where wide-bin searches can pick idle tones.
        freq_resolution = self.fs / n  # Hz per bin
        fund_idx = int(np.argmin(np.abs(freqs - fund_hz)))
        if fund_idx <= 0:
            warnings.warn(
                f"Requested fundamental {fund_hz:.2f} Hz is below/at DC bin for "
                f"FFT resolution {freq_resolution:.2f} Hz."
            )
            fund_idx = 1
        metrics.detected_fund_hz          = float(freqs[fund_idx])
        metrics.fundamental_amplitude_dbfs = float(
            10.0 * np.log10(max(magnitude_sq[fund_idx], eps))
        )
        print(
            f"[DSP Engine] Fundamental detected at {metrics.detected_fund_hz:.2f} Hz  "
            f"({metrics.fundamental_amplitude_dbfs:.2f} dBFS)"
        )

        # ── 4. Audio-band brick-wall mask ──────────────────────────────────
        f_low, f_high = self.audio_band
        audio_mask = (freqs >= f_low) & (freqs <= f_high)

        # ── 5. Harmonic null map ───────────────────────────────────────────
        harmonic_mask = np.zeros(len(freqs), dtype=bool)
        for h in range(1, max_harmonics + 2):
            hf = h * fund_hz
            if hf > freqs[-1]:
                break
            hb  = int(round(hf * n / self.fs))
            lo  = max(0, hb - null_bins)
            hi  = min(len(freqs), hb + null_bins + 1)
            harmonic_mask[lo:hi] = True

        # ── 6. Power budget ────────────────────────────────────────────────
        # Fundamental power
        f_lo_b = max(0, fund_idx - null_bins)
        f_hi_b = min(len(freqs), fund_idx + null_bins + 1)
        p_fund = float(np.sum(magnitude_sq[f_lo_b:f_hi_b]))

        # In-band noise (excluding all harmonics including fundamental)
        noise_mask  = audio_mask & ~harmonic_mask
        p_noise     = float(np.sum(magnitude_sq[noise_mask]))

        # In-band harmonic distortion power (HD2 … HDN, within audio band)
        p_harm_dist = float(np.sum(magnitude_sq[audio_mask & harmonic_mask])) - p_fund

        # THD+N = (HD + noise) / fundamental
        p_thdn = p_noise + max(0.0, p_harm_dist)

        # ── 7. Metrics ─────────────────────────────────────────────────────
        metrics.thd_n_db  = float(10.0 * np.log10(max(p_thdn,  eps) / max(p_fund, eps)))
        metrics.snr_db    = float(10.0 * np.log10(max(p_fund,  eps) / max(p_noise, eps)))
        metrics.sinad_db  = float(10.0 * np.log10(max(p_fund,  eps) / max(p_thdn,  eps)))

        # SFDR: peak spur outside the fundamental window, in-band
        spur_psd               = magnitude_sq.copy()
        spur_psd[f_lo_b:f_hi_b] = 0.0
        sfdr_idx               = int(np.argmax(spur_psd * audio_mask))
        metrics.sfdr_db        = float(
            10.0 * np.log10(max(magnitude_sq[fund_idx], eps) /
                            max(magnitude_sq[sfdr_idx], eps))
        )

        # ── 8. Noise RMS and DC offset ─────────────────────────────────────
        # Noise RMS via spectral residual (no harmonic contamination)
        noise_spectrum           = spectrum.copy()
        noise_spectrum[harmonic_mask] = 0.0
        noise_spectrum[~audio_mask]   = 0.0
        noise_time               = np.fft.irfft(noise_spectrum, n=n) / cg
        metrics.noise_rms_fs     = float(np.sqrt(np.mean(noise_time ** 2)))

        # DC offset from unwindowed settled waveform
        metrics.dc_offset_fs = float(np.mean(waveform))

        # ── 9. Limit-cycle detection ───────────────────────────────────────
        # Use the larger of:
        #  • relative threshold: limit_cycle_threshold_db below the fundamental
        #  • absolute floor: -80 dBFS (prevents false alarms when the signal
        #    level is very low, e.g. a -60 dBFS dynamic-range test tone where
        #    the relative threshold sinks into the shaped noise floor).
        relative_thresh = (10.0 ** (self.limit_cycle_threshold_db / 10.0)) * p_fund
        absolute_floor  = 10.0 ** (-80.0 / 10.0)   # -80 dBFS power
        threshold_lin   = max(relative_thresh, absolute_floor)
        lc_mask         = audio_mask & ~harmonic_mask & (magnitude_sq > threshold_lin)
        lc_idx        = np.where(lc_mask)[0]
        metrics.limit_cycle_detected  = len(lc_idx) > 0
        metrics.limit_cycle_freqs_hz  = [float(freqs[i]) for i in lc_idx]

        self._last_metrics = metrics
        print(metrics)
        return metrics

    def analyze_core_pdm(
        self,
        fund_hz: float,
        decimation: int = 64,
        max_harmonics: int = 9,
        null_bins: int = 3,
    ) -> AudioMetrics:
        """
        Core-only modulator analysis path (digital, reconstruction-independent).

        Method:
        1) Map raw PDM bits to bipolar (+1/-1)
        2) Remove start/end settling regions (same settle_ms policy)
        3) FIR anti-alias polyphase decimate by `decimation` (default OSR=64)
        4) Run THD+N/SNR/SINAD/SFDR over 20 Hz-20 kHz on the decimated stream
        """
        if len(self._raw_bits) == 0:
            raise RuntimeError("No PDM bits pushed. Call push_bits() first.")
        if decimation <= 0:
            raise ValueError(f"decimation must be > 0, got {decimation}")

        raw = np.asarray(self._raw_bits, dtype=np.float64)
        bipolar = 2.0 * raw - 1.0
        if self.invert_output:
            bipolar *= -1.0

        n_settle = int(round(self.settle_ms * 1e-3 * self.fs))
        if (2 * n_settle) >= len(bipolar):
            raise ValueError(
                f"Settling buffers ({2*n_settle} samples total) exceed capture "
                f"length ({len(bipolar)} samples)."
            )
        core = bipolar[n_settle:-n_settle]

        # Proper anti-alias decimator (boxcar decimation aliases SDM
        # high-frequency quantization noise back into audio band).
        core = sp_signal.resample_poly(
            core, up=1, down=decimation, window=("kaiser", 10.0)
        )
        if len(core) < 256:
            raise ValueError(
                f"Not enough samples for core analysis after decimation: {len(core)}."
            )
        fs_core = self.fs / decimation

        metrics = AudioMetrics()
        eps = 1e-30

        # Use an integer number of tone cycles when possible to suppress
        # low-frequency leakage in THD+N (especially around 20 Hz).
        if fund_hz > 0.0:
            n_cycles = int((len(core) * fund_hz) / fs_core)
            if n_cycles >= 8:
                n_coherent = int(round(n_cycles * fs_core / fund_hz))
                if 256 <= n_coherent <= len(core):
                    core = core[:n_coherent]

        # Remove DC before FFT to prevent leakage from DC into nearby bins.
        core_dc = float(np.mean(core))
        core_ac = core - core_dc

        n = len(core)
        window = np.hanning(n)
        cg = n / np.sum(window)
        spectrum = np.fft.rfft(core_ac * window) * cg / (n / 2)
        freqs = np.fft.rfftfreq(n, d=1.0 / fs_core)
        magnitude_sq = np.abs(spectrum) ** 2

        fund_idx = int(np.argmin(np.abs(freqs - fund_hz)))
        if fund_idx <= 0:
            fund_idx = 1
        metrics.detected_fund_hz = float(freqs[fund_idx])
        metrics.fundamental_amplitude_dbfs = float(
            10.0 * np.log10(max(magnitude_sq[fund_idx], eps))
        )

        f_low, f_high = self.audio_band
        audio_mask = (freqs >= f_low) & (freqs <= f_high)

        harmonic_mask = np.zeros(len(freqs), dtype=bool)
        for h in range(1, max_harmonics + 2):
            hf = h * fund_hz
            if hf > freqs[-1]:
                break
            hb = int(round(hf * n / fs_core))
            lo = max(0, hb - null_bins)
            hi = min(len(freqs), hb + null_bins + 1)
            harmonic_mask[lo:hi] = True

        f_lo_b = max(0, fund_idx - null_bins)
        f_hi_b = min(len(freqs), fund_idx + null_bins + 1)
        p_fund = float(np.sum(magnitude_sq[f_lo_b:f_hi_b]))
        noise_mask = audio_mask & ~harmonic_mask
        p_noise = float(np.sum(magnitude_sq[noise_mask]))
        p_harm_dist = float(np.sum(magnitude_sq[audio_mask & harmonic_mask])) - p_fund
        p_thdn = p_noise + max(0.0, p_harm_dist)

        metrics.thd_n_db = float(10.0 * np.log10(max(p_thdn, eps) / max(p_fund, eps)))
        metrics.snr_db = float(10.0 * np.log10(max(p_fund, eps) / max(p_noise, eps)))
        metrics.sinad_db = float(10.0 * np.log10(max(p_fund, eps) / max(p_thdn, eps)))

        spur_psd = magnitude_sq.copy()
        spur_psd[f_lo_b:f_hi_b] = 0.0
        sfdr_idx = int(np.argmax(spur_psd * audio_mask))
        metrics.sfdr_db = float(
            10.0 * np.log10(max(magnitude_sq[fund_idx], eps) / max(magnitude_sq[sfdr_idx], eps))
        )

        noise_spectrum = spectrum.copy()
        noise_spectrum[harmonic_mask] = 0.0
        noise_spectrum[~audio_mask] = 0.0
        noise_time = np.fft.irfft(noise_spectrum, n=n) / cg
        metrics.noise_rms_fs = float(np.sqrt(np.mean(noise_time ** 2)))
        metrics.dc_offset_fs = core_dc

        threshold_lin = (10.0 ** (self.limit_cycle_threshold_db / 10.0)) * p_fund
        lc_mask = audio_mask & ~harmonic_mask & (magnitude_sq > threshold_lin)
        lc_idx = np.where(lc_mask)[0]
        metrics.limit_cycle_detected = len(lc_idx) > 0
        metrics.limit_cycle_freqs_hz = [float(freqs[i]) for i in lc_idx]

        return metrics

    # ----------------------------------------------- idle-channel analysis

    def analyze_silence(self) -> AudioMetrics:
        """
        Idle-channel (Test B) analysis: no fundamental expected.

        Measures DC offset, RMS noise floor in the audio band, and scans
        for limit cycles using a power threshold of -80 dBFS.
        """
        metrics = AudioMetrics()
        waveform = self._reconstruct()
        n        = len(waveform)
        eps      = 1e-30

        metrics.dc_offset_fs = float(np.mean(waveform))

        freqs      = np.fft.rfftfreq(n, d=1.0 / self.fs)
        f_low, f_high = self.audio_band
        audio_mask = (freqs >= f_low) & (freqs <= f_high)

        window   = np.hanning(n)
        cg       = n / np.sum(window)
        spectrum = np.fft.rfft(waveform * window) * cg / (n / 2)
        mag_sq   = np.abs(spectrum) ** 2

        p_noise             = float(np.sum(mag_sq[audio_mask]))
        metrics.noise_rms_fs = float(np.sqrt(max(p_noise, eps)))

        # Limit-cycle: any spur above -80 dBFS (absolute, not relative)
        threshold_lin = 10.0 ** (self.limit_cycle_threshold_db / 10.0)
        lc_idx        = np.where(audio_mask & (mag_sq > threshold_lin))[0]
        metrics.limit_cycle_detected  = len(lc_idx) > 0
        metrics.limit_cycle_freqs_hz  = [float(freqs[i]) for i in lc_idx]

        self._last_freqs   = freqs
        self._last_psd     = mag_sq
        self._last_fund_hz = 0.0
        self._last_metrics = metrics
        print(metrics)
        return metrics

    # --------------------------------------------------- PSD visualisation

    def save_psd_plot(
        self,
        filepath: str,
        title: str = "PDM Output – Power Spectral Density",
        dpi: int = 300,
        profile: str = "debug",
        show_metrics_box: Optional[bool] = None,
        show_harmonic_guides: str = "full",
    ) -> None:
        """
        Save PSD plot with selectable detail profile.

        Parameters
        ----------
        filepath : str
            Output file path (typically .png; caller may also use .pdf).
        title : str
            Plot title.
        dpi : int
            Raster resolution (ignored by vector backends).
        profile : {"debug", "paper"}
            debug: full annotations for engineering investigation.
            paper: reduced clutter for publication figures.
        show_metrics_box : bool | None
            None -> defaults to True in debug, False in paper.
        show_harmonic_guides : {"none", "minimal", "full"}
            Control the amount of harmonic marker overlays.
        """
        if not _MPL_AVAILABLE:
            warnings.warn("matplotlib unavailable – plot skipped.", stacklevel=2)
            return
        if self._last_freqs is None:
            raise RuntimeError("Run analyze() or analyze_silence() first.")
        if profile not in {"debug", "paper"}:
            raise ValueError(f"Invalid profile '{profile}'. Use 'debug' or 'paper'.")
        if show_harmonic_guides not in {"none", "minimal", "full"}:
            raise ValueError(
                f"Invalid show_harmonic_guides '{show_harmonic_guides}'. "
                "Use 'none', 'minimal', or 'full'."
            )

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

        freqs  = self._last_freqs
        psd_db = 10.0 * np.log10(np.maximum(self._last_psd, 1e-30))
        m      = self._last_metrics
        if show_metrics_box is None:
            show_metrics_box = (profile == "debug")

        if profile == "paper":
            fig_size = (7.16, 3.0)
            rc = {
                "font.family":      "serif",
                "font.size":        9,
                "axes.titlesize":   9.5,
                "axes.labelsize":   9,
                "xtick.labelsize":  8,
                "ytick.labelsize":  8,
                "legend.fontsize":  8,
                "lines.linewidth":  1.1,
            }
        else:
            fig_size = (10, 4.5)
            rc = {
                "font.family":      "serif",
                "font.size":        10,
                "axes.titlesize":   11,
                "axes.labelsize":   10,
                "xtick.labelsize":  9,
                "ytick.labelsize":  9,
                "legend.fontsize":  8.5,
                "lines.linewidth":  1.2,
            }

        # ── Plot style ────────────────────────────────────────────────────
        plt.rcParams.update(rc)

        fig, ax = plt.subplots(figsize=fig_size)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")

        # Full spectrum — light gray baseline
        valid = freqs[1:] > 0
        ax.semilogx(
            freqs[1:][valid], psd_db[1:][valid],
            color="#bbbbbb", linewidth=0.6, label="Full spectrum", zorder=1
        )

        # Audio band — blue, drawn on top
        f_low, f_high = self.audio_band
        ab = (freqs >= f_low) & (freqs <= f_high)
        ax.semilogx(
            freqs[ab], psd_db[ab],
            color="#1f77b4", linewidth=1.4,
            label=f"Audio band ({f_low:.0f}\u2013{f_high/1e3:.0f}\u202fkHz)",
            zorder=2,
        )
        ax.axvspan(f_low, f_high, alpha=0.07, color="#1f77b4", zorder=0)

        # Fundamental marker
        if self._last_fund_hz > 0:
            ax.axvline(
                self._last_fund_hz, color="#d62728",
                linestyle="--", linewidth=1.2, zorder=3,
                label=f"Fundamental ({self._last_fund_hz:.0f}\u202fHz)",
            )
            # Harmonics (configurable density)
            if show_harmonic_guides != "none":
                h_max = 6 if show_harmonic_guides == "minimal" else 12
                for h in range(2, h_max):
                    hf = h * self._last_fund_hz
                    if hf > freqs[-1]:
                        break
                    ax.axvline(
                        hf, color="#ff7f0e", linestyle=":",
                        linewidth=0.75 if profile == "paper" else 0.8,
                        alpha=0.5 if show_harmonic_guides == "minimal" else 0.7,
                        zorder=3,
                        label=f"HD{h}" if (show_harmonic_guides == "full" and h <= 5) else None,
                    )

        # 2nd-order noise-shaping theoretical reference (+40 dB/dec)
        ref_f  = np.array([200, 1e3, 5e3, 20e3, 200e3, 2e6, 20e6, 50e6])
        ref_db = -168 + 40 * np.log10(ref_f / 1e3)
        ax.semilogx(
            ref_f, ref_db,
            color="#2ca02c", linewidth=0.9, linestyle="-.", alpha=0.7,
            label="2nd-order N-S ref. (+40\u202fdB/dec)", zorder=2,
        )

        # Axes formatting
        ax.set_xlim(10, self.fs / 2)
        ax.set_ylim(-210, 10)
        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel("Power (dB rel. full-scale)")
        ax.set_title(title, pad=8)
        ax.grid(True, which="major", color="#cccccc", linewidth=0.5 if profile == "debug" else 0.45, zorder=0)
        ax.grid(True, which="minor", color="#eeeeee", linewidth=0.3 if profile == "debug" else 0.25, zorder=0)
        for spine in ax.spines.values():
            spine.set_color("#444444")
            spine.set_linewidth(0.8)
        ax.tick_params(axis="both", colors="#333333", direction="in",
                       top=True, right=True, length=3)

        if profile == "paper":
            ax.legend(
                loc="lower left", frameon=True,
                facecolor="white", edgecolor="#aaaaaa",
                framealpha=0.9, ncol=1,
            )
        else:
            ax.legend(
                loc="upper left", frameon=True,
                facecolor="white", edgecolor="#aaaaaa",
                framealpha=0.9,
            )

        # Metrics annotation box (monospace, top-right)
        if show_metrics_box and m is not None:
            lines_ann: list[str] = []
            if not math.isnan(m.thd_n_db):
                lines_ann.append(f"THD+N   {m.thd_n_db:+.2f} dBc")
            if not math.isnan(m.snr_db):
                lines_ann.append(f"SNR     {m.snr_db:.2f} dB")
            if not math.isnan(m.sfdr_db):
                lines_ann.append(f"SFDR    {m.sfdr_db:.2f} dBc")
            lines_ann.append(f"DC      {m.dc_offset_fs*100:.4f} %FS")
            lines_ann.append(
                f"Noise   {20*math.log10(max(m.noise_rms_fs, 1e-18)):.1f} dBFS"
                if not math.isnan(m.noise_rms_fs) else "Noise   N/A"
            )
            if m.limit_cycle_detected:
                lines_ann.append("! LIMIT CYCLE")
            ax.text(
                0.975, 0.975, "\n".join(lines_ann),
                transform=ax.transAxes, fontsize=8,
                va="top", ha="right",
                fontfamily="monospace", color="#222222",
                bbox=dict(
                    boxstyle="round,pad=0.5",
                    facecolor="white", edgecolor="#888888",
                    alpha=0.92, linewidth=0.8,
                ),
            )

        plt.tight_layout(pad=1.2)
        plt.savefig(filepath, dpi=dpi, facecolor="white", bbox_inches="tight")
        plt.close(fig)

        # Reset rcParams so other plots in the session are not affected
        plt.rcParams.update(plt.rcParamsDefault)

        print(f"[DSP Engine] PSD plot saved → {os.path.abspath(filepath)}")

    # ---------------------------------------- static utility

    @staticmethod
    def plot_filter_response(
        fs: float = 100e6,
        order: int = 6,
        cutoff_hz: float = 20e3,
        filepath: str = "reports/filter_response.png",
    ) -> None:
        """Save the Butterworth LPF frequency response for engineering review."""
        if not _MPL_AVAILABLE:
            return
        wn  = cutoff_hz / (fs / 2.0)
        sos = sp_signal.butter(order, wn, btype="low", analog=False, output="sos")
        w, h = sp_signal.sosfreqz(sos, worN=16384, fs=fs)
        db   = 20 * np.log10(np.maximum(np.abs(h), 1e-20))

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        plt.rcParams.update({
            "font.family": "serif", "font.size": 10,
            "axes.labelsize": 10, "axes.titlesize": 11,
            "xtick.labelsize": 9, "ytick.labelsize": 9,
            "legend.fontsize": 9,
        })
        fig, ax = plt.subplots(figsize=(9, 4))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        ax.semilogx(w[1:], db[1:], color="#1f77b4", linewidth=1.4,
                    label=f"Order-{order} Butterworth LPF")
        ax.axvline(cutoff_hz, color="#d62728", linestyle="--", linewidth=1.0,
                   label=f"$f_c$ = {cutoff_hz/1e3:.0f}\u202fkHz (\u22123\u202fdB)")
        ax.axhline(-3,  color="#555555", linestyle=":", linewidth=0.7)
        ax.axhline(-60, color="#999999", linestyle=":", linewidth=0.6)
        ax.set_xlim(10, fs / 2)
        ax.set_ylim(-200, 5)
        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel("Gain (dB)")
        ax.set_title(
            f"Reconstruction Filter — Butterworth Order {order}, "
            f"$f_c$={cutoff_hz/1e3:.0f}\u202fkHz, $f_s$={fs/1e6:.0f}\u202fMHz"
        )
        ax.grid(True, which="major", color="#cccccc", linewidth=0.5)
        ax.grid(True, which="minor", color="#eeeeee", linewidth=0.3)
        for spine in ax.spines.values():
            spine.set_color("#444444")
            spine.set_linewidth(0.8)
        ax.tick_params(axis="both", colors="#333333", direction="in",
                       top=True, right=True, length=3)
        ax.legend(frameon=True, facecolor="white", edgecolor="#aaaaaa",
                  framealpha=0.9)
        plt.tight_layout(pad=1.2)
        plt.savefig(filepath, dpi=300, facecolor="white", bbox_inches="tight")
        plt.close(fig)
        plt.rcParams.update(plt.rcParamsDefault)
        print(f"[DSP Engine] Filter response → {os.path.abspath(filepath)}")


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Synthetic sanity-check: 1-bit SDM in Python → pipeline validation.
    Not a substitute for RTL simulation but confirms the math is correct.
    """
    import sys

    FS        = 100e6
    FUND_HZ   = 1000.0
    DURATION  = 0.025            # 25 ms → 15 ms of analysis after 5 ms settle
    AMPLITUDE = 0.9              # −0.92 dBFS

    n_bits   = int(FS * DURATION)
    t        = np.arange(n_bits, dtype=np.float64) / FS
    sine_in  = AMPLITUDE * np.sin(2.0 * np.pi * FUND_HZ * t)

    # Simple 1st-order SDM (for pipeline validation only, not RTL)
    acc  = 0.0
    bits: list[int] = []
    for s in sine_in:
        fb = 1.0 if acc >= 0.0 else -1.0
        acc += s - fb
        bits.append(1 if fb > 0.0 else 0)

    az = VirtualAnalogAnalyzer(fs=FS, invert_output=False)
    az.push_bits(bits)
    m  = az.analyze(fund_hz=FUND_HZ)
    az.save_psd_plot("reports/selftest_psd.png",
                     title="Self-Test – Synthetic 1 kHz PDM (1st-order reference)")
    VirtualAnalogAnalyzer.plot_filter_response()
    sys.exit(0)
