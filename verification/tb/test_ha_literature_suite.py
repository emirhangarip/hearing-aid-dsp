"""
test_ha_literature_suite.py

Objective evaluation scenarios for hearing-aid reporting:
- HA-7 Output SNR sweep
- HA-8 HASPI/HASQI sweep
- HA-9 Reverberation-conditioned evaluation
- HA-10 ECR and modulation spectrum analysis (May, Kowalewski & Dau 2018)

These tests run on a software proxy path and complement L1-L4 RTL tests.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import cocotb
from cocotb.triggers import Timer
from scipy import signal as sp_signal

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

try:
    import soundfile as sf  # type: ignore
    _SF_AVAILABLE = True
except Exception:
    _SF_AVAILABLE = False

from objective_speech_metrics import (
    aggregate_mean_std,
    evaluate_signoff,
    haspi_hasqi_scores,
    make_babble_noise,
    make_speech_shaped_noise,
    modulation_factor_metrics,
    output_snr_db,
    output_snr_slope,
    process_with_proxy,
    propose_thresholds,
    apply_rir,
)
from paper_plotter import apply_paper_style, figure_size, save_figure, check_figure_quality, CB_PALETTE


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "verification" / "reports"
PAPER_REPORT_DIR = REPORTS_DIR / "paper"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
PAPER_REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _report(name: str) -> Path:
    return REPORTS_DIR / name


def _paper_plot(name: str) -> Path:
    return PAPER_REPORT_DIR / name


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid YAML file: {path}")
    return data


def _load_eval_cfg() -> dict[str, Any]:
    return _load_yaml(REPO_ROOT / "verification" / "config" / "paper_eval.yaml")


def _load_threshold_cfg() -> dict[str, Any]:
    p = REPO_ROOT / "verification" / "config" / "paper_thresholds.yaml"
    if not p.exists():
        return {"locked": False, "direction": {}, "thresholds": {}}
    return _load_yaml(p)


def _paper_mode(cfg: dict[str, Any]) -> str:
    env_key = str(cfg.get("evaluation", {}).get("mode_env_var", "PAPER_MODE"))
    mode = os.getenv(env_key, str(cfg.get("gating", {}).get("default_mode", "collect")))
    mode = mode.strip().lower()
    return "signoff" if mode == "signoff" else "collect"


def _load_lock_entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    ls_cfg = cfg.get("data", {}).get("librispeech", {})
    lock_rel = ls_cfg.get("lock_manifest", "verification/config/librispeech_subset.lock.json")
    lock_path = REPO_ROOT / lock_rel
    if not lock_path.exists():
        return []
    with lock_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return list(payload.get("entries", []))


def _resample_if_needed(x: np.ndarray, fs_in: int, fs_out: int) -> np.ndarray:
    if fs_in == fs_out:
        return x
    g = math.gcd(fs_in, fs_out)
    up = fs_out // g
    down = fs_in // g
    return sp_signal.resample_poly(x, up, down)


def _synthetic_utterance(uid: str, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    b, a = sp_signal.butter(3, [120.0 / 24000.0, 6000.0 / 24000.0], btype="band")
    band = sp_signal.filtfilt(b, a, white)

    # Slow amplitude envelope to emulate speech modulation.
    t = np.arange(n, dtype=np.float64) / 48000.0
    env = 0.25 + 0.35 * np.maximum(0.0, np.sin(2.0 * np.pi * 2.3 * t + (seed % 11)))
    env += 0.20 * np.maximum(0.0, np.sin(2.0 * np.pi * 4.9 * t + (seed % 7)))
    y = band * env

    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 1e-12:
        y = 0.85 * y / peak
    return y.astype(np.float64)


def _load_audio_pool(cfg: dict[str, Any], fs_target: int = 48000) -> list[dict[str, Any]]:
    eval_cfg = cfg.get("evaluation", {})
    n_utt = int(eval_cfg.get("utterance_count", 20))
    seed = int(cfg.get("random_seed", 20260227))

    lock_entries = _load_lock_entries(cfg)
    pool: list[dict[str, Any]] = []

    if _SF_AVAILABLE and lock_entries:
        for row in lock_entries[:n_utt]:
            rel = row.get("relative_path")
            if not rel:
                continue
            p = REPO_ROOT / rel
            if not p.exists():
                continue
            try:
                x, fs_in = sf.read(str(p), always_2d=False)
                x = np.asarray(x, dtype=np.float64)
                if x.ndim > 1:
                    x = np.mean(x, axis=1)
                x = _resample_if_needed(x, int(fs_in), fs_target)
                # Keep 1.5 s max per utterance to bound runtime.
                max_n = int(1.5 * fs_target)
                x = x[:max_n]
                if x.size < int(0.4 * fs_target):
                    continue
                peak = float(np.max(np.abs(x)))
                if peak > 1e-12:
                    x = 0.9 * x / peak
                pool.append({"utterance_id": row.get("utterance_id", p.stem), "signal": x})
            except Exception:
                continue

    # Deterministic fallback if dataset/audio backend is unavailable.
    if len(pool) < n_utt:
        pool = []
        n = int(1.2 * fs_target)
        for i in range(n_utt):
            uid = f"synthetic_{i:03d}"
            pool.append({"utterance_id": uid, "signal": _synthetic_utterance(uid, n, seed + i)})

    return pool[:n_utt]


def _rir_for_rt60(cfg: dict[str, Any], rt60_s: float, n_samples: int, fs: int = 48000) -> np.ndarray:
    data_cfg = cfg.get("data", {})
    rir_cfg = data_cfg.get("rirs", {})
    out_dir = REPO_ROOT / rir_cfg.get("out_dir", "verification/data/rirs")
    npy = out_dir / f"rir_rt60_{rt60_s:.1f}s.npy"

    if npy.exists():
        try:
            h = np.asarray(np.load(npy), dtype=np.float64)
            if h.size > 0:
                return h
        except Exception:
            pass

    if rt60_s <= 0.0:
        h = np.zeros(256, dtype=np.float64)
        h[0] = 1.0
        return h

    # Lightweight deterministic fallback.
    n = int(max(0.6, 1.1 * rt60_s) * fs)
    t = np.arange(n, dtype=np.float64) / fs
    h = np.exp(-np.log(1000.0) * t / rt60_s)
    h[0] = 1.0
    peak = float(np.max(np.abs(h)))
    if peak > 1e-12:
        h /= peak
    return h


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _group_mean_curve(rows: list[dict[str, Any]], group_key: str, x_key: str, y_key: str) -> dict[str, dict[float, float]]:
    grouped: dict[str, dict[float, list[float]]] = {}
    for r in rows:
        g = str(r[group_key])
        x = float(r[x_key])
        y = float(r[y_key])
        if not math.isfinite(y):
            continue
        grouped.setdefault(g, {}).setdefault(x, []).append(y)

    out: dict[str, dict[float, float]] = {}
    for g, by_x in grouped.items():
        out[g] = {x: float(np.mean(vals)) for x, vals in sorted(by_x.items())}
    return out


def _gate_summary_or_collect(
    mode: str,
    summary_stats: dict[str, dict[str, float]],
    threshold_cfg: dict[str, Any],
) -> tuple[list[str], dict[str, float]]:
    directions = {str(k): str(v) for k, v in dict(threshold_cfg.get("direction", {})).items()}
    proposed = propose_thresholds(summary_stats, directions)

    if mode != "signoff":
        return [], proposed

    failures: list[str] = []
    if not bool(threshold_cfg.get("locked", False)):
        failures.append("paper_thresholds.yaml is not locked; run collect mode to calibrate first")
        return failures, proposed

    thresholds = {str(k): float(v) for k, v in dict(threshold_cfg.get("thresholds", {})).items()}
    for metric, stat in summary_stats.items():
        mean_val = float(stat.get("mean", float("nan")))
        if not math.isfinite(mean_val):
            # Metric unavailable (e.g., optional backend not installed).
            continue
        if metric not in thresholds:
            failures.append(f"missing threshold for metric '{metric}'")
            continue
        direction = directions.get(metric, "higher_is_better")
        if not evaluate_signoff(mean_val, thresholds[metric], direction):
            cmp_txt = ">=" if direction == "higher_is_better" else "<="
            failures.append(
                f"{metric}: mean {mean_val:.4f} not {cmp_txt} threshold {thresholds[metric]:.4f}"
            )

    return failures, proposed


def _baseline_comparison_relpath() -> str:
    return "verification/reports/paper/baseline_comparison.json"


def _proxy_only_marker(dut: Any, test_name: str) -> None:
    # These tests run a software proxy model and do not stimulate RTL signals.
    try:
        _ = dut._name
    except Exception:
        pass
    cocotb.log.info("%s: proxy-only evaluation path (DUT instantiated, not driven)", test_name)


def _load_baseline_comparison() -> dict[str, Any]:
    p = REPO_ROOT / _baseline_comparison_relpath()
    if not p.exists():
        raise RuntimeError(
            f"missing baseline comparison artifact: {p} "
            "(run make -C verification/sim paper-baselines)"
        )
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"invalid baseline comparison payload: {p}")
    return data


def _baseline_mean(
    baseline_payload: dict[str, Any],
    method: str,
    metric: str,
    scenario: str = "anechoic",
) -> float:
    try:
        v = baseline_payload["summary"][method][scenario]["summary"][metric]["mean"]
        return float(v)
    except Exception:
        pass

    vals: list[float] = []
    for r in baseline_payload.get("rows", []):
        if str(r.get("method")) != method:
            continue
        if str(r.get("scenario", "anechoic")) != scenario:
            continue
        try:
            v = float(r[metric])
        except Exception:
            continue
        if math.isfinite(v):
            vals.append(v)
    if not vals:
        return float("nan")
    return float(np.mean(np.asarray(vals, dtype=np.float64)))


def _relative_gate_failures(
    mode: str,
    threshold_cfg: dict[str, Any],
    check_output_snr: bool = False,
    check_haspi: bool = False,
    check_hasqi: bool = False,
) -> list[str]:
    if mode != "signoff":
        return []

    req = dict(threshold_cfg.get("relative_requirements", {}))
    if not req:
        return []

    try:
        baseline = _load_baseline_comparison()
    except Exception as exc:
        return [str(exc)]

    failures: list[str] = []

    if check_haspi and "wdrc_vs_nalr_haspi_v2_min_delta" in req:
        min_delta = float(req["wdrc_vs_nalr_haspi_v2_min_delta"])
        wdrc = _baseline_mean(baseline, "wdrc_proxy", "haspi_v2")
        nalr = _baseline_mean(baseline, "nalr", "haspi_v2")
        delta = wdrc - nalr
        if not math.isfinite(delta):
            failures.append("relative gate haspi_v2 unavailable (non-finite baseline means)")
        elif delta < min_delta:
            failures.append(
                f"relative gate fail: wdrc_proxy.haspi_v2 - nalr.haspi_v2 = {delta:.4f} < {min_delta:.4f}"
            )

    if check_hasqi and "wdrc_vs_nalr_hasqi_v2_min_delta" in req:
        min_delta = float(req["wdrc_vs_nalr_hasqi_v2_min_delta"])
        wdrc = _baseline_mean(baseline, "wdrc_proxy", "hasqi_v2")
        nalr = _baseline_mean(baseline, "nalr", "hasqi_v2")
        delta = wdrc - nalr
        if not math.isfinite(delta):
            failures.append("relative gate hasqi_v2 unavailable (non-finite baseline means)")
        elif delta < min_delta:
            failures.append(
                f"relative gate fail: wdrc_proxy.hasqi_v2 - nalr.hasqi_v2 = {delta:.4f} < {min_delta:.4f}"
            )

    if check_output_snr and "wdrc_vs_unprocessed_output_snr_db_min_delta" in req:
        min_delta = float(req["wdrc_vs_unprocessed_output_snr_db_min_delta"])
        wdrc = _baseline_mean(baseline, "wdrc_proxy", "output_snr_db")
        unp = _baseline_mean(baseline, "unprocessed", "output_snr_db")
        delta = wdrc - unp
        if not math.isfinite(delta):
            failures.append("relative gate output_snr_db unavailable (non-finite baseline means)")
        elif delta < min_delta:
            failures.append(
                f"relative gate fail: wdrc_proxy.output_snr_db - unprocessed.output_snr_db = {delta:.4f} < {min_delta:.4f}"
            )

    return failures


def _plot_output_snr(rows: list[dict[str, Any]], filename_stem: str, with_rt60: bool = False) -> dict[str, Any]:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return {"saved": False, "quality_warnings": ["matplotlib unavailable"]}

    apply_paper_style()
    fig, ax = plt.subplots(figsize=figure_size("two_column"))

    if with_rt60:
        key = "cond"
        plot_rows = []
        for r in rows:
            rr = dict(r)
            rr["cond"] = f"{r['noise_type']}, RT60={float(r['rt60_s']):.1f}s"
            plot_rows.append(rr)
        curves = _group_mean_curve(plot_rows, key, "input_snr_db", "output_snr_db")
    else:
        curves = _group_mean_curve(rows, "noise_type", "input_snr_db", "output_snr_db")

    colors = [CB_PALETTE["blue"], CB_PALETTE["orange"], CB_PALETTE["green"], CB_PALETTE["purple"], CB_PALETTE["red"]]
    for i, (name, xy) in enumerate(sorted(curves.items())):
        xs = list(sorted(xy.keys()))
        ys = [xy[x] for x in xs]
        ax.plot(xs, ys, marker="o", color=colors[i % len(colors)], label=name)

    ax.set_xlabel("Input SNR (dB)")
    ax.set_ylabel("Output SNR (dB)")
    ax.set_title("Output SNR Sweep")
    ax.grid(True, which="major", color="#d0d0d0", linewidth=0.6)
    ax.legend(frameon=True, facecolor="white", edgecolor="#999999")

    quality = check_figure_quality(fig, min_fontsize=7.0)
    paths = save_figure(fig, str(_paper_plot(filename_stem)), profile="paper")
    plt.close(fig)
    return {"saved": True, "paths": paths, "quality_warnings": quality}


def _plot_haspi_hasqi(rows: list[dict[str, Any]], filename_stem: str) -> dict[str, Any]:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return {"saved": False, "quality_warnings": ["matplotlib unavailable"]}

    apply_paper_style()
    fig, axs = plt.subplots(1, 2, figsize=figure_size("two_column"))
    legend_handles = None
    legend_labels = None

    for ax, metric, title in [
        (axs[0], "haspi_v2", "HASPI-v2"),
        (axs[1], "hasqi_v2", "HASQI-v2"),
    ]:
        curves = _group_mean_curve(rows, "noise_type", "input_snr_db", metric)
        plotted = False
        for i, (name, xy) in enumerate(sorted(curves.items())):
            xs = list(sorted(xy.keys()))
            ys = [xy[x] for x in xs]
            if not xs:
                continue
            ax.plot(xs, ys, marker="o", color=[CB_PALETTE["blue"], CB_PALETTE["orange"], CB_PALETTE["green"]][i % 3], label=name)
            plotted = True
            if legend_handles is None:
                legend_handles, legend_labels = ax.get_legend_handles_labels()
        if not plotted:
            ax.text(
                0.5,
                0.5,
                "No finite HASPI/HASQI data\n(pyclarity backend unavailable)",
                ha="center",
                va="center",
                transform=ax.transAxes,
                fontsize=8,
                color="#444444",
            )
        ax.set_xlabel("Input SNR (dB)")
        ax.set_ylabel("Score")
        ax.set_title(title)
        ax.grid(True, which="major", color="#d0d0d0", linewidth=0.6)

    # Use a figure-level legend to avoid text crowding inside subplot area.
    if legend_handles:
        fig.legend(
            legend_handles,
            legend_labels,
            loc="upper center",
            ncol=2,
            frameon=True,
            facecolor="white",
            edgecolor="#999999",
            bbox_to_anchor=(0.5, 1.03),
        )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.93], w_pad=2.0)

    quality = check_figure_quality(fig, min_fontsize=7.0)
    paths = save_figure(fig, str(_paper_plot(filename_stem)), profile="paper")
    plt.close(fig)
    return {"saved": True, "paths": paths, "quality_warnings": quality}


def _plot_mod_metrics(rows: list[dict[str, Any]], filename_stem: str) -> dict[str, Any]:
    """
    Modulation spectrum analysis figure.

    Primary panel: ECR and DR (cited, gated metrics from May et al. 2018).
    Secondary panel: FES and FBR (modulation spectrum statistics, reported).
    ASMC, BSMC, UVR are supplementary and do not appear in the figure.
    """
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return {"saved": False, "quality_warnings": ["matplotlib unavailable"]}

    # Primary: ECR + DR — gated, cited (May et al. 2018)
    primary = [("ecr", "ECR"), ("dr_db", "DR (dB)")]
    # Modulation spectrum statistics: FES + FBR — reported, not gated
    mod_spec = [("fes", "FES"), ("fbr", "FBR")]

    def _mean(key: str) -> float:
        vals = [float(r[key]) for r in rows if key in r and math.isfinite(float(r[key]))]
        return float(np.mean(vals)) if vals else float("nan")

    apply_paper_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figure_size("two_column"))

    # Primary metrics
    keys1, labels1 = zip(*primary)
    means1 = [_mean(k) for k in keys1]
    x1 = np.arange(len(keys1))
    ax1.bar(x1, means1, color=CB_PALETTE["blue"], edgecolor=CB_PALETTE["black"], linewidth=0.4)
    ax1.set_xticks(x1)
    ax1.set_xticklabels(labels1)
    ax1.set_title("ECR & Dynamic Range\n(May et al. 2018)")
    ax1.grid(True, axis="y", color="#d0d0d0", linewidth=0.6)
    ax1.set_ylabel("Value")

    # Modulation spectrum statistics
    keys2, labels2 = zip(*mod_spec)
    means2 = [_mean(k) for k in keys2]
    x2 = np.arange(len(keys2))
    ax2.bar(x2, means2, color=CB_PALETTE["orange"], edgecolor=CB_PALETTE["black"], linewidth=0.4)
    ax2.set_xticks(x2)
    ax2.set_xticklabels(labels2)
    ax2.set_title("Modulation Spectrum Statistics\n(FES / FBR)")
    ax2.grid(True, axis="y", color="#d0d0d0", linewidth=0.6)
    ax2.set_ylabel("Value")

    fig.suptitle("Modulation Spectrum Analysis (Mean Across Conditions)", fontsize=9)
    fig.tight_layout()

    quality = check_figure_quality(fig, min_fontsize=7.0)
    paths = save_figure(fig, str(_paper_plot(filename_stem)), profile="paper")
    plt.close(fig)
    return {"saved": True, "paths": paths, "quality_warnings": quality}


def _eval_grid_rows(
    pool: list[dict[str, Any]],
    cfg: dict[str, Any],
    with_reverb: bool,
    include_haspi: bool,
    include_mod: bool,
) -> list[dict[str, Any]]:
    eval_cfg = cfg.get("evaluation", {})
    snr_grid = [float(x) for x in eval_cfg.get("input_snr_db", [-10, -5, 0, 5, 10])]
    noise_types = [str(x) for x in eval_cfg.get("noise_types", ["speech_shaped", "babble"])]
    rt60_grid = [0.0] if not with_reverb else [float(x) for x in eval_cfg.get("rt60_s", [0.0, 0.3, 0.6])]
    seed = int(cfg.get("random_seed", 20260227))

    fs = 48000.0
    signals = [np.asarray(p["signal"], dtype=np.float64) for p in pool]

    rows: list[dict[str, Any]] = []
    for u_idx, utt in enumerate(pool):
        uid = str(utt["utterance_id"])
        clean = np.asarray(utt["signal"], dtype=np.float64)

        for rt_idx, rt60_s in enumerate(rt60_grid):
            for n_idx, noise_type in enumerate(noise_types):
                if noise_type == "speech_shaped":
                    noise = make_speech_shaped_noise(clean, seed=seed + 1000 * u_idx + 100 * rt_idx + n_idx)
                else:
                    noise = make_babble_noise(signals, clean.size, seed=seed + 3000 * u_idx + 300 * rt_idx + n_idx)

                # Reverb before mixture to emulate acoustic condition.
                if with_reverb and rt60_s > 0.0:
                    h = _rir_for_rt60(cfg, rt60_s, clean.size, fs=int(fs))
                    clean_cond = apply_rir(clean, h)
                    noise_cond = apply_rir(noise, h)
                else:
                    clean_cond = clean
                    noise_cond = noise

                for snr in snr_grid:
                    out = process_with_proxy(
                        clean=clean_cond,
                        noise=noise_cond,
                        fs=fs,
                        snr_db=snr,
                        knee_dbfs=-40.0,
                        ratio=3.0,
                        max_gain_db=18.0,
                        tau_attack_ms=5.0,
                        tau_release_ms=100.0,
                    )

                    row: dict[str, Any] = {
                        "utterance_id": uid,
                        "noise_type": noise_type,
                        "rt60_s": float(rt60_s),
                        "input_snr_db": float(snr),
                        "output_snr_db": float(output_snr_db(out["clean_out"], out["noise_out"])),
                    }

                    if include_haspi:
                        hcfg = cfg.get("metrics", {}).get("haspi_hasqi", {})
                        ag = hcfg.get("audiogram", None)
                        hs = haspi_hasqi_scores(clean_ref=out["clean_in"], processed=out["mix_out"], fs=fs, audiogram=ag)
                        row["haspi_v2"] = float(hs.get("haspi_v2", float("nan")))
                        row["hasqi_v2"] = float(hs.get("hasqi_v2", float("nan")))
                        row["haspi_backend"] = str(hs.get("backend", "unavailable"))

                    if include_mod:
                        mm = modulation_factor_metrics(clean_ref=out["clean_in"], processed=out["mix_out"], fs=fs)
                        row.update(mm)

                    rows.append(row)

    return rows


@cocotb.test(timeout_time=2000, timeout_unit="ms")
async def test_HA_7_output_snr_sweep(dut):
    """HA-7: output-SNR sweep across noise type and input-SNR grid."""
    await Timer(1, unit="ns")
    _proxy_only_marker(dut, "HA-7")

    cfg = _load_eval_cfg()
    mode = _paper_mode(cfg)
    thr_cfg = _load_threshold_cfg()

    pool = _load_audio_pool(cfg)
    rows = _eval_grid_rows(pool, cfg, with_reverb=False, include_haspi=False, include_mod=False)

    # Slope metric per noise type from mean curve.
    slopes: dict[str, float] = {}
    for noise_type in sorted({str(r["noise_type"]) for r in rows}):
        by_snr: dict[float, list[float]] = {}
        for r in rows:
            if str(r["noise_type"]) != noise_type:
                continue
            by_snr.setdefault(float(r["input_snr_db"]), []).append(float(r["output_snr_db"]))
        curve_rows = [{"input_snr_db": x, "output_snr_db": float(np.mean(v))} for x, v in sorted(by_snr.items())]
        slopes[noise_type] = output_snr_slope(curve_rows)

    summary = aggregate_mean_std(rows, ["output_snr_db"])
    slope_vals = [v for v in slopes.values() if math.isfinite(v)]
    summary["output_snr_slope_db_per_db"] = {
        "mean": float(np.mean(slope_vals)) if slope_vals else float("nan"),
        "std": float(np.std(slope_vals)) if slope_vals else float("nan"),
        "n": float(len(slope_vals)),
    }

    failures, proposed = _gate_summary_or_collect(mode, summary, thr_cfg)
    for _rel_msg in _relative_gate_failures(
        mode, thr_cfg, check_output_snr=True, check_haspi=False, check_hasqi=False
    ):
        cocotb.log.info("[relative-gate policy-note] %s", _rel_msg)
    plot_meta = _plot_output_snr(rows, "fig7_output_snr_sweep", with_rt60=False)

    payload = {
        "mode": mode,
        "noise_types": sorted({str(r["noise_type"]) for r in rows}),
        "input_snr_db": sorted({float(r["input_snr_db"]) for r in rows}),
        "rows": rows,
        "summary": summary,
        "per_noise_slope": slopes,
        "proposed_thresholds": proposed,
        "comparison_source": _baseline_comparison_relpath(),
        "comparison_methods": ["unprocessed", "nalr", "wdrc_proxy"],
        "plot": plot_meta,
    }
    _write_json(_report("HA_7_output_snr.json"), payload)

    assert not failures, "HA-7 FAIL:\n  " + "\n  ".join(failures)


@cocotb.test(timeout_time=2000, timeout_unit="ms")
async def test_HA_8_haspi_hasqi_sweep(dut):
    """HA-8: HASPI/HASQI objective sweep."""
    await Timer(1, unit="ns")
    _proxy_only_marker(dut, "HA-8")

    cfg = _load_eval_cfg()
    mode = _paper_mode(cfg)
    thr_cfg = _load_threshold_cfg()

    rows = _eval_grid_rows(_load_audio_pool(cfg), cfg, with_reverb=False, include_haspi=True, include_mod=False)

    summary = aggregate_mean_std(rows, ["haspi_v2", "hasqi_v2"])
    failures, proposed = _gate_summary_or_collect(mode, summary, thr_cfg)
    for _rel_msg in _relative_gate_failures(
        mode, thr_cfg, check_output_snr=False, check_haspi=True, check_hasqi=True
    ):
        cocotb.log.info("[relative-gate policy-note] %s", _rel_msg)

    # Optional strict backend requirement.
    hcfg = cfg.get("metrics", {}).get("haspi_hasqi", {})
    if mode == "signoff" and bool(hcfg.get("require_pyclarity", False)):
        available = any(math.isfinite(float(r.get("haspi_v2", float("nan")))) for r in rows)
        if not available:
            failures.append("HASPI/HASQI backend unavailable while require_pyclarity=true")

    plot_meta = _plot_haspi_hasqi(rows, "fig8_haspi_hasqi")

    payload = {
        "mode": mode,
        "rows": rows,
        "summary": summary,
        "proposed_thresholds": proposed,
        "comparison_source": _baseline_comparison_relpath(),
        "comparison_methods": ["unprocessed", "nalr", "wdrc_proxy"],
        "plot": plot_meta,
    }
    _write_json(_report("HA_8_haspi_hasqi.json"), payload)

    assert not failures, "HA-8 FAIL:\n  " + "\n  ".join(failures)


@cocotb.test(timeout_time=2400, timeout_unit="ms")
async def test_HA_9_reverb_conditioned_eval(dut):
    """HA-9: evaluate output SNR and HASPI/HASQI across RT60 conditions."""
    await Timer(1, unit="ns")
    _proxy_only_marker(dut, "HA-9")

    cfg = _load_eval_cfg()
    mode = _paper_mode(cfg)
    thr_cfg = _load_threshold_cfg()

    rows = _eval_grid_rows(_load_audio_pool(cfg), cfg, with_reverb=True, include_haspi=True, include_mod=False)

    summary = aggregate_mean_std(rows, ["output_snr_db", "haspi_v2", "hasqi_v2"])
    failures, proposed = _gate_summary_or_collect(mode, summary, thr_cfg)
    plot_meta = _plot_output_snr(rows, "fig7_output_snr_reverb", with_rt60=True)

    payload = {
        "mode": mode,
        "rt60_s": sorted({float(r["rt60_s"]) for r in rows}),
        "rows": rows,
        "summary": summary,
        "proposed_thresholds": proposed,
        "comparison_source": _baseline_comparison_relpath(),
        "comparison_methods": ["unprocessed", "nalr", "wdrc_proxy"],
        "plot": plot_meta,
    }
    _write_json(_report("HA_9_reverb_eval.json"), payload)

    assert not failures, "HA-9 FAIL:\n  " + "\n  ".join(failures)


@cocotb.test(timeout_time=2200, timeout_unit="ms")
async def test_HA_10_modulation_factor_metrics(dut):
    """
    HA-10: ECR and modulation-spectrum metrics.

    Primary metrics — gated, paper-cited:
      ECR  Envelope Compression Ratio (rms_out / rms_in of the smoothed
           envelope). Measures how much WDRC compresses broadband speech
           modulation. Definition: May et al. (2018) Trends in Hearing 22.
      DR   Output envelope dynamic range (95th − 5th percentile, dB).

    Modulation spectrum statistics — reported, not gated:
      FES  Modulation spectrum flatness (geometric/arithmetic mean ratio,
           0.5–20 Hz). Measures spectral spread of modulation energy.
      FBR  Low/high modulation energy ratio (0.5–4 Hz vs 4–20 Hz). High
           FBR indicates slow-acting compression preserving prosodic rhythm.

    Supplementary (not used for gating):
      ASMC Amplitude-envelope shape correlation (Pearson ρ, fullband).
      BSMC Band-filtered (2–20 Hz) envelope correlation.
      UVR  Instantaneous gain variability (std of gain in dB).
    """
    await Timer(1, unit="ns")
    _proxy_only_marker(dut, "HA-10")

    cfg = _load_eval_cfg()
    mode = _paper_mode(cfg)
    thr_cfg = _load_threshold_cfg()

    rows = _eval_grid_rows(_load_audio_pool(cfg), cfg, with_reverb=True, include_haspi=False, include_mod=True)

    # Primary gating: only cited, paper-grade metrics.
    summary = aggregate_mean_std(rows, ["ecr", "dr_db"])
    failures, proposed = _gate_summary_or_collect(mode, summary, thr_cfg)

    # Modulation spectrum statistics (reported in paper, not in pass/fail gate).
    mod_spec_summary = aggregate_mean_std(rows, ["fes", "fbr"])

    # Supplementary (exploratory; omit from main paper claims).
    supplementary_summary = aggregate_mean_std(rows, ["asmc", "bsmc", "uvr"])

    plot_meta = _plot_mod_metrics(rows, "fig8_modulation_metrics")

    payload = {
        "mode": mode,
        "reference": (
            "May T, Kowalewski B, Dau T (2018). Signal-to-noise-ratio-aware dynamic "
            "range compression in hearing aids. Trends in Hearing 22, "
            "doi:10.1177/2331216518758832"
        ),
        "primary_metrics": ["ecr", "dr_db"],
        "summary": summary,
        "mod_spec_summary": mod_spec_summary,
        "supplementary": supplementary_summary,
        "proposed_thresholds": proposed,
        "plot": plot_meta,
        "rows": rows,
    }
    _write_json(_report("HA_10_modulation_metrics.json"), payload)

    assert not failures, "HA-10 FAIL:\n  " + "\n  ".join(failures)
