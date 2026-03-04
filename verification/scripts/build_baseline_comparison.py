#!/usr/bin/env python3
"""Build same-dataset baseline comparison (unprocessed vs NAL-R vs WDRC proxy)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

try:
    import soundfile as sf  # type: ignore
except Exception:  # pragma: no cover
    sf = None

ROOT = Path(__file__).resolve().parents[2]
TB_DIR = ROOT / "verification" / "tb"
if str(TB_DIR) not in sys.path:
    sys.path.insert(0, str(TB_DIR))

from objective_speech_metrics import (  # noqa: E402
    aggregate_mean_std,
    apply_rir,
    haspi_hasqi_scores,
    make_babble_noise,
    make_speech_shaped_noise,
    output_snr_db,
    process_unprocessed,
    process_with_nalr,
    process_with_proxy,
)
from paper_eval_common import (  # noqa: E402
    load_eval_cfg as _load_eval_cfg,
    load_lock_entries as _load_lock_entries,
    resample_if_needed as _resample_if_needed,
)


def _load_audio_pool(
    cfg: dict[str, Any],
    fs_target: int = 48000,
    n_utt_override: int | None = None,
) -> list[dict[str, Any]]:
    if sf is None:
        raise RuntimeError("soundfile is required to build same-dataset baseline comparison")

    eval_cfg = cfg.get("evaluation", {})
    n_utt = int(n_utt_override if n_utt_override is not None else eval_cfg.get("utterance_count", 20))
    entries = _load_lock_entries(ROOT, cfg)

    pool: list[dict[str, Any]] = []
    for row in entries[:n_utt]:
        rel = row.get("relative_path")
        if not rel:
            continue
        p = ROOT / rel
        if not p.exists():
            raise RuntimeError(f"Locked utterance missing on disk: {p}")
        x, fs_in = sf.read(str(p), always_2d=False)
        x = np.asarray(x, dtype=np.float64)
        if x.ndim > 1:
            x = np.mean(x, axis=1)
        x = _resample_if_needed(x, int(fs_in), fs_target)

        max_n = int(1.5 * fs_target)
        x = x[:max_n]
        if x.size < int(0.4 * fs_target):
            continue

        peak = float(np.max(np.abs(x))) if x.size else 0.0
        if peak > 1e-12:
            x = 0.9 * x / peak
        pool.append({"utterance_id": row.get("utterance_id", p.stem), "signal": x})

    if len(pool) < n_utt:
        raise RuntimeError(
            f"Only {len(pool)} usable utterances loaded from lock; expected {n_utt}. "
            "Run fetch/lock workflow first."
        )
    return pool[:n_utt]


def _rir_for_rt60(cfg: dict[str, Any], rt60_s: float, fs: int = 48000) -> np.ndarray:
    data_cfg = cfg.get("data", {})
    rir_cfg = data_cfg.get("rirs", {})
    out_dir = ROOT / rir_cfg.get("out_dir", "verification/data/rirs")
    npy = out_dir / f"rir_rt60_{rt60_s:.1f}s.npy"

    if npy.exists():
        h = np.asarray(np.load(npy), dtype=np.float64)
        if h.size > 0:
            return h

    if rt60_s <= 0.0:
        h = np.zeros(256, dtype=np.float64)
        h[0] = 1.0
        return h

    n = int(max(0.6, 1.1 * rt60_s) * fs)
    t = np.arange(n, dtype=np.float64) / fs
    h = np.exp(-np.log(1000.0) * t / rt60_s)
    h[0] = 1.0
    peak = float(np.max(np.abs(h)))
    if peak > 1e-12:
        h /= peak
    return h


def _method_metrics(rows: list[dict[str, Any]], method: str, scenario: str) -> dict[str, Any]:
    subset = [r for r in rows if r["method"] == method and r["scenario"] == scenario]
    summary = aggregate_mean_std(subset, ["output_snr_db", "haspi_v2", "hasqi_v2"])
    finite_haspi = sum(1 for r in subset if math.isfinite(float(r["haspi_v2"])))
    finite_hasqi = sum(1 for r in subset if math.isfinite(float(r["hasqi_v2"])))
    return {
        "rows": len(subset),
        "finite_haspi": finite_haspi,
        "finite_hasqi": finite_hasqi,
        "summary": summary,
    }


def _write_md(path: Path, rows: list[dict[str, Any]]) -> None:
    methods = ["unprocessed", "nalr", "wdrc_proxy"]
    scenarios = ["anechoic", "reverb"]
    out = ["# Baseline Comparison (Same Dataset)", ""]
    out.append("| Scenario | Method | Rows | Mean Output SNR (dB) | Mean HASPI-v2 | Mean HASQI-v2 | Finite HASPI | Finite HASQI |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for scenario in scenarios:
        for method in methods:
            m = _method_metrics(rows, method, scenario)
            s = m["summary"]
            out.append(
                "| "
                + f"{scenario} | {method} | {m['rows']} | "
                + f"{s['output_snr_db']['mean']:.4f} | {s['haspi_v2']['mean']:.4f} | {s['hasqi_v2']['mean']:.4f} | "
                + f"{m['finite_haspi']} | {m['finite_hasqi']} |"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-utterances",
        type=int,
        default=None,
        help="Optional override for utterance count (useful for quick local validation).",
    )
    ap.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N rows.",
    )
    args = ap.parse_args()

    # Fail loudly if NAL-R dependency is unavailable.
    try:
        from clarity.enhancer.nalr import NALR  # type: ignore  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "clarity.enhancer.nalr.NALR is required but unavailable. Install requirements first."
        ) from exc

    cfg = _load_eval_cfg(ROOT)
    eval_cfg = cfg.get("evaluation", {})
    seed = int(cfg.get("random_seed", 20260227))
    snr_grid = [float(x) for x in eval_cfg.get("input_snr_db", [-10, -5, 0, 5, 10])]
    noise_types = [str(x) for x in eval_cfg.get("noise_types", ["speech_shaped", "babble"])]
    rt60_grid = [float(x) for x in eval_cfg.get("rt60_s", [0.0, 0.3, 0.6])]
    audiogram = cfg.get("metrics", {}).get("haspi_hasqi", {}).get("audiogram", None)

    fs = 48000.0
    pool = _load_audio_pool(cfg, fs_target=int(fs), n_utt_override=args.max_utterances)
    signals = [np.asarray(p["signal"], dtype=np.float64) for p in pool]

    methods = ["unprocessed", "nalr", "wdrc_proxy"]
    scenarios: list[tuple[str, list[float]]] = [("anechoic", [0.0]), ("reverb", rt60_grid)]
    rows: list[dict[str, Any]] = []
    total_steps = (
        len(pool)
        * sum(len(rt_list) for _sc, rt_list in scenarios)
        * len(noise_types)
        * len(snr_grid)
        * len(methods)
    )
    done = 0
    print(
        f"[baselines] Starting: utterances={len(pool)} "
        f"noise_types={len(noise_types)} snr_points={len(snr_grid)} "
        f"methods={len(methods)} total_rows={total_steps}"
    )

    for u_idx, utt in enumerate(pool):
        uid = str(utt["utterance_id"])
        clean = np.asarray(utt["signal"], dtype=np.float64)

        for scenario, scenario_rt60s in scenarios:
            for rt_idx, rt60_s in enumerate(scenario_rt60s):
                for n_idx, noise_type in enumerate(noise_types):
                    if noise_type == "speech_shaped":
                        noise = make_speech_shaped_noise(
                            clean, seed=seed + 1000 * u_idx + 100 * rt_idx + n_idx
                        )
                    else:
                        noise = make_babble_noise(
                            signals, clean.size, seed=seed + 3000 * u_idx + 300 * rt_idx + n_idx
                        )

                    if rt60_s > 0.0:
                        h = _rir_for_rt60(cfg, rt60_s, fs=int(fs))
                        clean_cond = apply_rir(clean, h)
                        noise_cond = apply_rir(noise, h)
                    else:
                        clean_cond = clean
                        noise_cond = noise

                    for snr_db in snr_grid:
                        for method in methods:
                            if method == "unprocessed":
                                out = process_unprocessed(clean_cond, noise_cond, fs=fs, snr_db=snr_db)
                            elif method == "nalr":
                                out = process_with_nalr(
                                    clean_cond,
                                    noise_cond,
                                    fs=fs,
                                    snr_db=snr_db,
                                    audiogram=audiogram,
                                    nfir=220,
                                )
                            else:
                                out = process_with_proxy(
                                    clean=clean_cond,
                                    noise=noise_cond,
                                    fs=fs,
                                    snr_db=snr_db,
                                    knee_dbfs=-40.0,
                                    ratio=3.0,
                                    max_gain_db=18.0,
                                    tau_attack_ms=5.0,
                                    tau_release_ms=100.0,
                                )

                            hs = haspi_hasqi_scores(
                                clean_ref=out["clean_in"],
                                processed=out["mix_out"],
                                fs=fs,
                                audiogram=audiogram,
                            )
                            rows.append(
                                {
                                    "scenario": scenario,
                                    "method": method,
                                    "utterance_id": uid,
                                    "noise_type": noise_type,
                                    "rt60_s": float(rt60_s),
                                    "input_snr_db": float(snr_db),
                                    "output_snr_db": float(output_snr_db(out["clean_out"], out["noise_out"])),
                                    "haspi_v2": float(hs.get("haspi_v2", float("nan"))),
                                    "hasqi_v2": float(hs.get("hasqi_v2", float("nan"))),
                                    "haspi_backend": str(hs.get("backend", "unavailable")),
                                }
                            )
                            done += 1
                            if args.progress_every > 0 and (done % args.progress_every == 0 or done == total_steps):
                                pct = 100.0 * done / max(1, total_steps)
                                print(f"[baselines] Progress: {done}/{total_steps} rows ({pct:.1f}%)")

    methods_summary: dict[str, Any] = {}
    for method in methods:
        methods_summary[method] = {}
        for scenario, _ in scenarios:
            methods_summary[method][scenario] = _method_metrics(rows, method, scenario)

    out_json = ROOT / "verification" / "reports" / "paper" / "baseline_comparison.json"
    out_md = ROOT / "verification" / "reports" / "paper" / "baseline_comparison.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "methods": methods,
        "scenarios": [s for s, _ in scenarios],
        "rows": rows,
        "summary": methods_summary,
        "protocol": {
            "utterance_count": len(pool),
            "noise_types": noise_types,
            "input_snr_db": snr_grid,
            "rt60_s": rt60_grid,
            "random_seed": seed,
        },
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_md(out_md, rows)

    print(f"[baselines] Wrote {len(rows)} rows -> {out_json}")
    print(f"[baselines] Wrote markdown table -> {out_md}")


if __name__ == "__main__":
    main()
