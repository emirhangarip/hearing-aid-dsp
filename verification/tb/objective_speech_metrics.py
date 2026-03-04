"""
objective_speech_metrics.py

Objective speech/noise evaluation helpers for paper-aligned hearing-aid scenarios.

This module is simulator-agnostic and can be used both by cocotb tests and
offline analysis scripts.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Optional

import numpy as np
from scipy import signal as sp_signal

_CLARITY_GD_PATCHED = False


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def dbfs(x: np.ndarray, floor: float = 1e-12) -> float:
    return 20.0 * math.log10(max(rms(x), floor))


def _norm_peak(x: np.ndarray, peak: float = 0.95) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    p = float(np.max(np.abs(x))) if x.size else 0.0
    if p <= 1e-12:
        return x.copy()
    return (peak / p) * x


def mix_at_snr(clean: np.ndarray, noise: np.ndarray, snr_db: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Scale noise to target input SNR relative to clean and return mixture.

    Returns (mix, scaled_noise).
    """
    c = np.asarray(clean, dtype=np.float64)
    n = np.asarray(noise, dtype=np.float64)
    if c.size != n.size:
        m = min(c.size, n.size)
        c = c[:m]
        n = n[:m]

    rc = rms(c)
    rn = rms(n)
    if rn <= 1e-15:
        return c.copy(), np.zeros_like(c)

    target_rn = rc / (10.0 ** (snr_db / 20.0))
    n_scaled = n * (target_rn / rn)
    mix = c + n_scaled
    return mix, n_scaled


def make_speech_shaped_noise(clean: np.ndarray, seed: int = 0) -> np.ndarray:
    """Create speech-shaped noise by matching clean long-term magnitude spectrum."""
    x = np.asarray(clean, dtype=np.float64)
    n = x.size
    if n <= 0:
        return np.zeros(0, dtype=np.float64)

    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)

    X = np.fft.rfft(x)
    W = np.fft.rfft(white)
    shaped = np.fft.irfft(np.abs(X) * np.exp(1j * np.angle(W)), n=n)
    return _norm_peak(shaped, peak=0.9)


def make_babble_noise(pool: Iterable[np.ndarray], n_samples: int, seed: int = 0, n_talkers: int = 8) -> np.ndarray:
    """Approximate babble by summing randomly shifted utterances from a pool."""
    arr = [np.asarray(a, dtype=np.float64) for a in pool if np.asarray(a).size > 0]
    if not arr:
        return np.zeros(n_samples, dtype=np.float64)

    rng = np.random.default_rng(seed)
    out = np.zeros(n_samples, dtype=np.float64)
    for _ in range(max(1, n_talkers)):
        s = arr[int(rng.integers(0, len(arr)))]
        if s.size < 4:
            continue
        rep = int(np.ceil(n_samples / s.size))
        tiled = np.tile(s, rep)[:n_samples]
        shift = int(rng.integers(0, max(1, n_samples)))
        out += np.roll(tiled, shift)
    return _norm_peak(out, peak=0.9)


def apply_rir(x: np.ndarray, rir: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    h = np.asarray(rir, dtype=np.float64)
    if x.size == 0 or h.size == 0:
        return x.copy()
    y = sp_signal.fftconvolve(x, h, mode="full")
    return y[: x.size]


def wdrc_proxy(
    x: np.ndarray,
    fs: float,
    knee_dbfs: float = -40.0,
    ratio: float = 3.0,
    max_gain_db: float = 18.0,
    tau_attack_ms: float = 5.0,
    tau_release_ms: float = 100.0,
) -> np.ndarray:
    """
    Lightweight wide-dynamic-range compression proxy used for objective tests.

    This is an offline envelope-domain compressor to evaluate scenario logic and
    objective metrics, not a replacement for RTL sign-off tests.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x.copy()

    env_in = np.abs(x)
    a_atk = math.exp(-1.0 / max(1.0, (tau_attack_ms * 1e-3 * fs)))
    a_rel = math.exp(-1.0 / max(1.0, (tau_release_ms * 1e-3 * fs)))

    env = np.zeros_like(env_in)
    env_prev = 0.0
    for i, e in enumerate(env_in):
        if e > env_prev:
            env_prev = a_atk * env_prev + (1.0 - a_atk) * e
        else:
            env_prev = a_rel * env_prev + (1.0 - a_rel) * e
        env[i] = env_prev

    env_db = 20.0 * np.log10(np.maximum(env, 1e-12))

    # Piecewise compression law in dB domain.
    out_db = np.where(
        env_db <= knee_dbfs,
        env_db + max_gain_db,
        (knee_dbfs + max_gain_db) + (env_db - knee_dbfs) / max(ratio, 1.0),
    )
    gain_db = out_db - env_db
    gain_lin = 10.0 ** (gain_db / 20.0)

    y = x * gain_lin
    return np.clip(y, -1.0, 1.0)


def process_with_proxy(clean: np.ndarray, noise: np.ndarray, fs: float, snr_db: float, **wdrc_kwargs: Any) -> dict[str, np.ndarray]:
    """Build mixture and process clean/noise/mix through the same proxy chain."""
    mix, n_scaled = mix_at_snr(clean, noise, snr_db=snr_db)

    y_mix = wdrc_proxy(mix, fs=fs, **wdrc_kwargs)
    y_clean = wdrc_proxy(clean, fs=fs, **wdrc_kwargs)
    y_noise = wdrc_proxy(n_scaled, fs=fs, **wdrc_kwargs)

    return {
        "mix_in": mix,
        "clean_in": clean,
        "noise_in": n_scaled,
        "mix_out": y_mix,
        "clean_out": y_clean,
        "noise_out": y_noise,
    }


def process_unprocessed(clean: np.ndarray, noise: np.ndarray, fs: float, snr_db: float) -> dict[str, np.ndarray]:
    """
    Baseline path with no enhancement.

    Output follows the same schema as `process_with_proxy` so downstream metric
    computation can stay method-agnostic.
    """
    _ = fs
    mix, n_scaled = mix_at_snr(clean, noise, snr_db=snr_db)
    c = np.asarray(clean, dtype=np.float64)
    n = min(c.size, n_scaled.size, mix.size)
    c = c[:n]
    n_scaled = n_scaled[:n]
    mix = mix[:n]
    return {
        "mix_in": mix,
        "clean_in": c,
        "noise_in": n_scaled,
        "mix_out": mix.copy(),
        "clean_out": c.copy(),
        "noise_out": n_scaled.copy(),
    }


def process_with_nalr(
    clean: np.ndarray,
    noise: np.ndarray,
    fs: float,
    snr_db: float,
    audiogram: Optional[dict[str, Any]],
    nfir: int = 220,
) -> dict[str, np.ndarray]:
    """
    NAL-R baseline path using Clarity's NALR implementation.

    The FIR is linear phase. We remove the group delay (nfir//2) and crop back
    to original signal length for clean/noise/mix parity.
    """
    try:
        from clarity.enhancer.nalr import NALR  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "clarity.enhancer.nalr.NALR is required for NAL-R baseline generation"
        ) from exc

    ag_obj = _build_audiogram(audiogram)
    if ag_obj is None:
        raise RuntimeError("Valid audiogram is required for NAL-R baseline generation")

    mix, n_scaled = mix_at_snr(clean, noise, snr_db=snr_db)
    c = np.asarray(clean, dtype=np.float64)
    n_scaled = np.asarray(n_scaled, dtype=np.float64)
    mix = np.asarray(mix, dtype=np.float64)
    n = min(c.size, n_scaled.size, mix.size)
    c = c[:n]
    n_scaled = n_scaled[:n]
    mix = mix[:n]

    nalr = NALR(nfir=int(nfir), sample_rate=float(fs))
    b, _delay = nalr.build(ag_obj)
    gd = int(nfir) // 2

    def _apply(x: np.ndarray) -> np.ndarray:
        y = nalr.apply(b, x)
        if gd > 0 and y.size > gd:
            y = y[gd:]
        y = y[:n]
        if y.size < n:
            y = np.pad(y, (0, n - y.size))
        return np.asarray(y, dtype=np.float64)

    y_mix = _apply(mix)
    y_clean = _apply(c)
    y_noise = _apply(n_scaled)
    return {
        "mix_in": mix,
        "clean_in": c,
        "noise_in": n_scaled,
        "mix_out": y_mix,
        "clean_out": y_clean,
        "noise_out": y_noise,
    }


def output_snr_db(clean_out: np.ndarray, noise_out: np.ndarray) -> float:
    rc = rms(np.asarray(clean_out, dtype=np.float64))
    rn = rms(np.asarray(noise_out, dtype=np.float64))
    return 20.0 * math.log10(max(rc, 1e-15) / max(rn, 1e-15))


def output_snr_slope(rows: list[dict[str, Any]]) -> float:
    """Least-squares slope of output_snr_db vs input_snr_db."""
    if len(rows) < 2:
        return float("nan")
    x = np.array([float(r["input_snr_db"]) for r in rows], dtype=np.float64)
    y = np.array([float(r["output_snr_db"]) for r in rows], dtype=np.float64)
    if np.allclose(np.std(x), 0.0):
        return float("nan")
    m, _b = np.polyfit(x, y, 1)
    return float(m)


def _try_haspi_hasqi_impl() -> tuple[Optional[Callable[..., float]], Optional[Callable[..., float]], str]:
    """
    Resolve HASPI/HASQI callables from available packages.

    Returns (haspi_fn, hasqi_fn, backend_name).
    """
    # Candidate 1: pyclarity package.
    try:
        from pyclarity import haspi_v2 as _haspi  # type: ignore
        from pyclarity import hasqi_v2 as _hasqi  # type: ignore
        return _haspi, _hasqi, "pyclarity"
    except Exception:
        pass

    # Candidate 2: clarity package layout used in some releases.
    try:
        from clarity.evaluator.haspi import haspi_v2 as _haspi  # type: ignore
    except Exception:
        _haspi = None
    try:
        from clarity.evaluator.hasqi import hasqi_v2 as _hasqi  # type: ignore
    except Exception:
        _hasqi = None
    if _haspi is not None or _hasqi is not None:
        return _haspi, _hasqi, "clarity"

    return None, None, "unavailable"


def _build_audiogram(audiogram: Optional[dict[str, Any]]) -> Any:
    """Build a backend audiogram object when possible."""
    if not isinstance(audiogram, dict):
        return None
    freqs = np.asarray(audiogram.get("freqs_hz", []), dtype=np.float64)
    levels = np.asarray(audiogram.get("loss_db_hl", []), dtype=np.float64)
    if freqs.size == 0 or levels.size == 0 or freqs.size != levels.size:
        return None

    # Clarity-toolbox API (used by current requirements).
    try:
        from clarity.utils.audiogram import Audiogram as ClarityAudiogram  # type: ignore

        return ClarityAudiogram(levels=levels, frequencies=freqs)
    except Exception:
        pass

    # pyclarity compatibility fallback.
    try:
        from pyclarity.audiogram import Audiogram as PyClarityAudiogram  # type: ignore

        return PyClarityAudiogram(levels=levels, frequencies=freqs)
    except Exception:
        pass

    return None


def _patch_clarity_group_delay_compat() -> None:
    """
    Patch Clarity's imported group_delay helper for SciPy compatibility.

    Some SciPy releases return a length-1 ndarray for scalar `w`, while
    Clarity assigns into a scalar slot. Converting single-element outputs to
    Python floats preserves expected behavior.
    """
    global _CLARITY_GD_PATCHED
    if _CLARITY_GD_PATCHED:
        return
    _CLARITY_GD_PATCHED = True

    try:
        import clarity.evaluator.haspi.eb as eb  # type: ignore
        from scipy.signal import group_delay as scipy_group_delay  # type: ignore
    except Exception:
        return

    def _group_delay_compat(system: Any, w: Any = 512, whole: bool = False, fs: float = 2.0 * np.pi) -> tuple[Any, Any]:
        w_out, gd_out = scipy_group_delay(system, w=w, whole=whole, fs=fs)
        gd_arr = np.asarray(gd_out)
        if gd_arr.size == 1:
            return w_out, float(gd_arr.reshape(-1)[0])
        return w_out, gd_out

    eb.group_delay = _group_delay_compat


def _extract_metric_score(value: Any) -> float:
    """Extract numeric score from scalar or tuple/list returns."""
    try:
        if isinstance(value, (tuple, list)):
            if not value:
                return float("nan")
            return float(value[0])
        return float(value)
    except Exception:
        return float("nan")


def _call_metric(fn: Callable[..., float], clean_ref: np.ndarray, processed: np.ndarray, fs: float, audiogram: Optional[dict[str, Any]]) -> float:
    _patch_clarity_group_delay_compat()

    c = np.asarray(clean_ref, dtype=np.float64)
    p = np.asarray(processed, dtype=np.float64)
    n = min(c.size, p.size)
    if n <= 0:
        return float("nan")
    c = c[:n]
    p = p[:n]
    ag_obj = _build_audiogram(audiogram)

    # Try known HASPI/HASQI signatures first (Clarity/pyclarity), then fallbacks.
    attempts = [
        lambda: fn(c, fs, p, fs, ag_obj),
        lambda: fn(
            reference=c,
            reference_sample_rate=fs,
            processed=p,
            processed_sample_rate=fs,
            audiogram=ag_obj,
        ),
        lambda: fn(c, p, fs_signal=fs, audiogram=ag_obj),
        lambda: fn(c, p, fs_signal=fs),
        lambda: fn(c, p, fs),
        lambda: fn(c, p),
    ]
    for attempt in attempts:
        try:
            return _extract_metric_score(attempt())
        except Exception:
            continue
    return float("nan")


def haspi_hasqi_scores(
    clean_ref: np.ndarray,
    processed: np.ndarray,
    fs: float,
    audiogram: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compute HASPI-v2/HASQI-v2 if backend is available, else return NaN."""
    hpi_fn, hqi_fn, backend = _try_haspi_hasqi_impl()
    out: dict[str, Any] = {
        "backend": backend,
        "haspi_v2": float("nan"),
        "hasqi_v2": float("nan"),
        "available": bool(hpi_fn is not None and hqi_fn is not None),
    }
    if hpi_fn is not None:
        out["haspi_v2"] = _call_metric(hpi_fn, clean_ref, processed, fs, audiogram)
    if hqi_fn is not None:
        out["hasqi_v2"] = _call_metric(hqi_fn, clean_ref, processed, fs, audiogram)
    return out


def _smooth_envelope(x: np.ndarray, fs: float, cutoff_hz: float = 50.0) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x.copy()
    env = np.abs(sp_signal.hilbert(x))
    wn = min(max(cutoff_hz / (fs / 2.0), 1e-6), 0.99)
    b, a = sp_signal.butter(2, wn, btype="low")
    return sp_signal.filtfilt(b, a, env)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    n = min(a.size, b.size)
    if n < 4:
        return float("nan")
    a = a[:n]
    b = b[:n]
    sa = float(np.std(a))
    sb = float(np.std(b))
    if sa < 1e-12 or sb < 1e-12:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def modulation_factor_metrics(clean_ref: np.ndarray, processed: np.ndarray, fs: float) -> dict[str, float]:
    """
    Compute modulation-factor style metrics used for WDRC scenario comparison.

    Metrics:
    - ASMC: correlation of smoothed fullband envelopes
    - BSMC: correlation of modulation-band (2..20 Hz) envelopes
    - DR: output envelope dynamic range (95th-5th percentile, dB)
    - ECR: envelope compression ratio (rms_out / rms_in)
    - FES: modulation spectrum flatness (0.5..20 Hz)
    - FBR: low/high modulation energy ratio (0.5..4 Hz over 4..20 Hz)
    - UVR: std of instantaneous gain (dB)
    """
    x = np.asarray(clean_ref, dtype=np.float64)
    y = np.asarray(processed, dtype=np.float64)
    n = min(x.size, y.size)
    x = x[:n]
    y = y[:n]

    env_x = _smooth_envelope(x, fs)
    env_y = _smooth_envelope(y, fs)

    asmc = _corr(env_x, env_y)

    # Modulation-band envelope component (2..20 Hz)
    b, a = sp_signal.butter(2, [2.0 / (fs / 2.0), 20.0 / (fs / 2.0)], btype="band")
    mx = sp_signal.filtfilt(b, a, env_x)
    my = sp_signal.filtfilt(b, a, env_y)
    bsmc = _corr(mx, my)

    env_y_db = 20.0 * np.log10(np.maximum(env_y, 1e-12))
    dr_db = float(np.percentile(env_y_db, 95.0) - np.percentile(env_y_db, 5.0))

    ecr = float(rms(env_y) / max(rms(env_x), 1e-12))

    # Modulation spectrum stats of output envelope.
    env_y_d = env_y - np.mean(env_y)
    nfft = int(2 ** np.ceil(np.log2(max(256, env_y_d.size))))
    spec = np.abs(np.fft.rfft(env_y_d, n=nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, d=1.0 / fs)
    band = (freqs >= 0.5) & (freqs <= 20.0)
    p = spec[band]
    if p.size == 0:
        fes = float("nan")
        fbr = float("nan")
    else:
        fes = float(np.exp(np.mean(np.log(np.maximum(p, 1e-20)))) / np.mean(np.maximum(p, 1e-20)))
        low = np.sum(spec[(freqs >= 0.5) & (freqs < 4.0)])
        high = np.sum(spec[(freqs >= 4.0) & (freqs <= 20.0)])
        fbr = float(low / max(high, 1e-20))

    inst_gain_db = 20.0 * np.log10(np.maximum(env_y, 1e-12) / np.maximum(env_x, 1e-12))
    uvr = float(np.std(inst_gain_db))

    return {
        "asmc": float(asmc),
        "bsmc": float(bsmc),
        "dr_db": dr_db,
        "ecr": ecr,
        "fes": fes,
        "fbr": fbr,
        "uvr": uvr,
    }


def aggregate_mean_std(rows: list[dict[str, Any]], metric_names: list[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for name in metric_names:
        vals = np.array([float(r[name]) for r in rows if name in r and math.isfinite(float(r[name]))], dtype=np.float64)
        if vals.size == 0:
            out[name] = {"mean": float("nan"), "std": float("nan"), "n": 0.0}
        else:
            out[name] = {
                "mean": float(np.mean(vals)),
                "std": float(np.std(vals, ddof=0)),
                "n": float(vals.size),
            }
    return out


def propose_thresholds(summary_stats: dict[str, dict[str, float]], direction: dict[str, str]) -> dict[str, float]:
    """Apply baseline mean±2std rule."""
    th: dict[str, float] = {}
    for name, st in summary_stats.items():
        mean = float(st.get("mean", float("nan")))
        std = float(st.get("std", float("nan")))
        if not (math.isfinite(mean) and math.isfinite(std)):
            continue
        d = direction.get(name, "higher_is_better")
        if d == "higher_is_better":
            th[name] = float(mean - 2.0 * std)
        else:
            th[name] = float(mean + 2.0 * std)
    return th


def evaluate_signoff(value: float, threshold: float, direction: str) -> bool:
    if not (math.isfinite(value) and math.isfinite(threshold)):
        return False
    if direction == "higher_is_better":
        return value >= threshold
    return value <= threshold
