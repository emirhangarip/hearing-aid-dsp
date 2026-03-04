#!/usr/bin/env python3
"""Validate proxy-vs-RTL correlation on a fixed 18-case objective matrix.

Expected RTL manifest format (JSON):
{
  "sample_rate_hz": 48000,
  "entries": [
    {
      "utterance_id": "84-121123-0001",
      "noise_type": "speech_shaped",
      "input_snr_db": -5,
      "rtl_mix_out": "verification/reports/paper/rtl_cases/case01_mix.wav",
      "rtl_clean_out": "verification/reports/paper/rtl_cases/case01_clean.wav",
      "rtl_noise_out": "verification/reports/paper/rtl_cases/case01_noise.wav"
    }
  ]
}
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import wave
from typing import Any

import numpy as np
from scipy import stats as sp_stats

try:
    import soundfile as sf  # type: ignore
except Exception:  # pragma: no cover
    sf = None


ROOT = Path(__file__).resolve().parents[2]
TB_DIR = ROOT / "verification" / "tb"
if str(TB_DIR) not in sys.path:
    sys.path.insert(0, str(TB_DIR))

from objective_speech_metrics import (  # noqa: E402
    haspi_hasqi_scores,
    make_babble_noise,
    make_speech_shaped_noise,
    output_snr_db,
    process_with_proxy,
)
from paper_eval_common import (  # noqa: E402
    load_lock_entries as _load_lock_entries,
    load_yaml as _load_yaml,
    resample_if_needed as _resample_if_needed,
)


def _repo_rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace(os.sep, "/")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid JSON file: {path}")
    return data


def _load_wav_pcm(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        n_ch = int(wf.getnchannels())
        fs = int(wf.getframerate())
        sw = int(wf.getsampwidth())
        raw = wf.readframes(wf.getnframes())
    if sw == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise RuntimeError(f"Unsupported WAV sample width ({sw} bytes): {path}")
    if n_ch > 1:
        data = data.reshape(-1, n_ch).mean(axis=1)
    return data, fs


def _load_signal(path: Path, fs_target: int, fs_hint: int | None = None) -> np.ndarray:
    if not path.exists():
        raise RuntimeError(f"Missing signal file: {path}")
    suffix = path.suffix.lower()
    if suffix == ".npy":
        x = np.asarray(np.load(path), dtype=np.float64)
        if x.ndim > 1:
            x = np.mean(x, axis=1)
        fs_in = int(fs_hint if fs_hint is not None else fs_target)
        return _resample_if_needed(x, fs_in, fs_target)

    if sf is not None:
        x, fs_in = sf.read(str(path), always_2d=False)
        x = np.asarray(x, dtype=np.float64)
        if x.ndim > 1:
            x = np.mean(x, axis=1)
        return _resample_if_needed(x, int(fs_in), fs_target)

    if suffix == ".wav":
        x, fs_in = _load_wav_pcm(path)
        return _resample_if_needed(x, int(fs_in), fs_target)

    raise RuntimeError(
        f"Cannot read '{path}'. Install soundfile for this format or provide WAV/NPY."
    )


def _best_energy_start(x: np.ndarray, win_n: int) -> int:
    if win_n <= 0 or x.size <= win_n:
        return 0
    xx = np.asarray(x, dtype=np.float64)
    energy = xx * xx
    csum = np.concatenate(([0.0], np.cumsum(energy)))
    win_energy = csum[win_n:] - csum[:-win_n]
    if win_energy.size == 0:
        return 0
    return int(np.argmax(win_energy))


def _load_audio_pool(
    cfg: dict[str, Any],
    fs_target: int,
    utterance_count: int,
    max_seconds: float,
    start_seconds: float,
    auto_speech_window: bool,
) -> list[dict[str, Any]]:
    if sf is None:
        raise RuntimeError("soundfile is required to load LibriSpeech utterances for proxy-vs-RTL validation")
    entries = _load_lock_entries(ROOT, cfg)
    pool: list[dict[str, Any]] = []
    for row in entries:
        rel = str(row.get("relative_path", "")).strip()
        if not rel:
            continue
        p = ROOT / rel
        if not p.exists():
            continue
        x, fs_in = sf.read(str(p), always_2d=False)
        x = np.asarray(x, dtype=np.float64)
        if x.ndim > 1:
            x = np.mean(x, axis=1)
        x = _resample_if_needed(x, int(fs_in), fs_target)
        max_n = max(1, int(round(float(max_seconds) * fs_target)))
        if x.size <= 0:
            continue
        if x.size <= max_n:
            start_n = 0
        else:
            req_start_n = max(0, int(round(float(start_seconds) * fs_target)))
            if auto_speech_window and float(start_seconds) <= 0.0:
                start_n = _best_energy_start(x, max_n)
            else:
                start_n = min(req_start_n, max(0, x.size - max_n))
        x = x[start_n : start_n + max_n]
        if x.size < int(0.4 * fs_target):
            continue
        peak = float(np.max(np.abs(x))) if x.size else 0.0
        if peak > 1e-12:
            x = 0.9 * x / peak
        pool.append(
            {
                "utterance_id": str(row.get("utterance_id", p.stem)),
                "signal": x,
                "start_seconds_effective": float(start_n) / float(fs_target),
            }
        )
        if len(pool) >= utterance_count:
            break
    if len(pool) < utterance_count:
        raise RuntimeError(
            f"Only {len(pool)} utterances available from lock; expected {utterance_count}"
        )
    return pool


def _case_key(utterance_id: str, noise_type: str, input_snr_db: float) -> str:
    return f"{utterance_id}|{noise_type}|{float(input_snr_db):.3f}"


def _build_proxy_rows(
    cfg: dict[str, Any],
    fs: int,
    utterance_count: int,
    noise_types: list[str],
    snr_points: list[float],
    max_seconds: float,
    start_seconds: float,
    auto_speech_window: bool,
) -> list[dict[str, Any]]:
    seed = int(cfg.get("random_seed", 20260227))
    hcfg = cfg.get("metrics", {}).get("haspi_hasqi", {})
    audiogram = hcfg.get("audiogram", None)

    pool = _load_audio_pool(
        cfg,
        fs_target=fs,
        utterance_count=utterance_count,
        max_seconds=max_seconds,
        start_seconds=start_seconds,
        auto_speech_window=auto_speech_window,
    )
    signals = [np.asarray(p["signal"], dtype=np.float64) for p in pool]

    rows: list[dict[str, Any]] = []
    for u_idx, utt in enumerate(pool):
        uid = str(utt["utterance_id"])
        clean = np.asarray(utt["signal"], dtype=np.float64)
        for n_idx, noise_type in enumerate(noise_types):
            if noise_type == "speech_shaped":
                noise = make_speech_shaped_noise(clean, seed=seed + 1000 * u_idx + n_idx)
            else:
                noise = make_babble_noise(signals, clean.size, seed=seed + 3000 * u_idx + n_idx)
            for snr_db in snr_points:
                out = process_with_proxy(
                    clean=clean,
                    noise=noise,
                    fs=float(fs),
                    snr_db=float(snr_db),
                    knee_dbfs=-40.0,
                    ratio=3.0,
                    max_gain_db=18.0,
                    tau_attack_ms=5.0,
                    tau_release_ms=100.0,
                )
                hs = haspi_hasqi_scores(
                    clean_ref=out["clean_in"],
                    processed=out["mix_out"],
                    fs=float(fs),
                    audiogram=audiogram,
                )
                rows.append(
                    {
                        "utterance_id": uid,
                        "noise_type": noise_type,
                        "input_snr_db": float(snr_db),
                        "clean_ref": np.asarray(out["clean_in"], dtype=np.float64),
                        "proxy_mix_out": np.asarray(out["mix_out"], dtype=np.float64),
                        "proxy_clean_out": np.asarray(out["clean_out"], dtype=np.float64),
                        "proxy_noise_out": np.asarray(out["noise_out"], dtype=np.float64),
                        "proxy_output_snr_db": float(output_snr_db(out["clean_out"], out["noise_out"])),
                        "proxy_haspi_v2": float(hs.get("haspi_v2", float("nan"))),
                        "proxy_hasqi_v2": float(hs.get("hasqi_v2", float("nan"))),
                        "haspi_backend": str(hs.get("backend", "unavailable")),
                    }
                )
    return rows


def _metric_stats(proxy_vals: list[float], rtl_vals: list[float]) -> dict[str, float]:
    pairs = [
        (float(p), float(r))
        for p, r in zip(proxy_vals, rtl_vals)
        if math.isfinite(float(p)) and math.isfinite(float(r))
    ]
    if not pairs:
        return {
            "n": 0.0,
            "bias": float("nan"),
            "mae": float("nan"),
            "spearman_rho": float("nan"),
        }
    p_arr = np.asarray([p for p, _ in pairs], dtype=np.float64)
    r_arr = np.asarray([r for _, r in pairs], dtype=np.float64)
    rho = float("nan")
    if p_arr.size >= 2:
        rho_val = sp_stats.spearmanr(p_arr, r_arr).correlation
        rho = float(rho_val) if rho_val is not None else float("nan")
    return {
        "n": float(p_arr.size),
        "bias": float(np.mean(p_arr - r_arr)),
        "mae": float(np.mean(np.abs(p_arr - r_arr))),
        "spearman_rho": rho,
    }


def _check_acceptance(metric: str, stats: dict[str, float], criteria: dict[str, dict[str, float]]) -> tuple[bool, list[str]]:
    errs: list[str] = []
    n = float(stats.get("n", 0.0))
    bias = float(stats.get("bias", float("nan")))
    mae = float(stats.get("mae", float("nan")))
    rho = float(stats.get("spearman_rho", float("nan")))
    cfg = criteria[metric]
    if n < 2:
        errs.append(f"{metric}: need at least 2 finite pairs, got {int(n)}")
        return False, errs
    if not math.isfinite(bias) or abs(bias) > cfg["bias_abs_max"]:
        errs.append(f"{metric}: |bias|={abs(bias):.4f} > {cfg['bias_abs_max']:.4f}")
    if not math.isfinite(mae) or mae > cfg["mae_max"]:
        errs.append(f"{metric}: MAE={mae:.4f} > {cfg['mae_max']:.4f}")
    if not math.isfinite(rho) or rho < cfg["spearman_rho_min"]:
        errs.append(f"{metric}: Spearman rho={rho:.4f} < {cfg['spearman_rho_min']:.4f}")
    return len(errs) == 0, errs


def _fmt(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return "-"
    if not math.isfinite(x):
        return "nan"
    return f"{x:.4f}"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="verification/config/paper_eval.yaml")
    ap.add_argument(
        "--rtl-manifest",
        default="verification/reports/paper/proxy_rtl_capture_manifest.json",
        help="Manifest describing RTL captured outputs for each matrix case",
    )
    ap.add_argument("--out-json", default="verification/reports/paper/proxy_rtl_correlation.json")
    ap.add_argument("--out-md", default="verification/reports/paper/proxy_rtl_correlation.md")
    ap.add_argument("--utterances", type=int, default=3)
    ap.add_argument("--noise-types", default="speech_shaped,babble")
    ap.add_argument("--snr-db", default="-5,0,5")
    ap.add_argument("--sample-rate-hz", type=int, default=48000)
    ap.add_argument(
        "--max-seconds",
        type=float,
        default=float(os.environ.get("PROXY_RTL_MAX_SECONDS", "0.4")),
        help="Clip duration used to build proxy utterances for correlation",
    )
    ap.add_argument(
        "--start-seconds",
        type=float,
        default=float(os.environ.get("PROXY_RTL_START_SECONDS", "0.0")),
        help="Start offset used before clip extraction for correlation",
    )
    ap.add_argument("--haspi-bias-max", type=float, default=0.05)
    ap.add_argument("--haspi-mae-max", type=float, default=0.07)
    ap.add_argument("--haspi-rho-min", type=float, default=0.85)
    ap.add_argument("--hasqi-bias-max", type=float, default=0.05)
    ap.add_argument("--hasqi-mae-max", type=float, default=0.07)
    ap.add_argument("--hasqi-rho-min", type=float, default=0.85)
    ap.add_argument("--snr-bias-max", type=float, default=1.0)
    ap.add_argument("--snr-mae-max", type=float, default=1.5)
    ap.add_argument("--snr-rho-min", type=float, default=0.85)
    ap.add_argument(
        "--auto-speech-window",
        dest="auto_speech_window",
        action="store_true",
        default=_env_flag("PROXY_RTL_AUTO_SPEECH", default=True),
        help="Auto-select a speech-active clip window when start-seconds is 0",
    )
    ap.add_argument(
        "--no-auto-speech-window",
        dest="auto_speech_window",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    ap.add_argument(
        "--allow-partial-matrix",
        action="store_true",
        default=_env_flag("PROXY_VALIDATE_ALLOW_PARTIAL", default=False),
        help="Allow validating a partial capture manifest (subset of 18-case matrix)",
    )
    args = ap.parse_args()

    try:
        cfg = _load_yaml(ROOT / args.config)
        noise_types = [x.strip() for x in str(args.noise_types).split(",") if x.strip()]
        snr_points = [float(x.strip()) for x in str(args.snr_db).split(",") if x.strip()]
        fs = int(args.sample_rate_hz)
        if len(noise_types) != 2 or len(snr_points) != 3:
            raise RuntimeError("Expected exactly 2 noise types and 3 SNR points for 18-case matrix")

        proxy_rows = _build_proxy_rows(
            cfg=cfg,
            fs=fs,
            utterance_count=int(args.utterances),
            noise_types=noise_types,
            snr_points=snr_points,
            max_seconds=float(args.max_seconds),
            start_seconds=float(args.start_seconds),
            auto_speech_window=bool(args.auto_speech_window),
        )

        rtl_manifest_path = ROOT / args.rtl_manifest
        rtl_manifest = _load_json(rtl_manifest_path)
        entries = list(rtl_manifest.get("entries", []))
        if not entries:
            raise RuntimeError(f"RTL manifest has no entries: {rtl_manifest_path}")
        rtl_fs_hint = rtl_manifest.get("sample_rate_hz")
        rtl_map: dict[str, dict[str, Any]] = {}
        for e in entries:
            if not isinstance(e, dict):
                continue
            try:
                key = _case_key(str(e["utterance_id"]), str(e["noise_type"]), float(e["input_snr_db"]))
            except Exception:
                continue
            rtl_map[key] = e

        hcfg = cfg.get("metrics", {}).get("haspi_hasqi", {})
        audiogram = hcfg.get("audiogram", None)
        case_rows: list[dict[str, Any]] = []
        missing_cases: list[str] = []
        for row in proxy_rows:
            key = _case_key(row["utterance_id"], row["noise_type"], row["input_snr_db"])
            e = rtl_map.get(key)
            if e is None:
                missing_cases.append(key)
                continue
            mix_path = ROOT / str(e.get("rtl_mix_out", ""))
            clean_path = ROOT / str(e.get("rtl_clean_out", ""))
            noise_path = ROOT / str(e.get("rtl_noise_out", ""))
            rtl_mix = _load_signal(mix_path, fs_target=fs, fs_hint=int(rtl_fs_hint) if isinstance(rtl_fs_hint, (int, float)) else None)
            rtl_clean = _load_signal(clean_path, fs_target=fs, fs_hint=int(rtl_fs_hint) if isinstance(rtl_fs_hint, (int, float)) else None)
            rtl_noise = _load_signal(noise_path, fs_target=fs, fs_hint=int(rtl_fs_hint) if isinstance(rtl_fs_hint, (int, float)) else None)

            clean_ref = np.asarray(row["clean_ref"], dtype=np.float64)
            n = min(clean_ref.size, rtl_mix.size, rtl_clean.size, rtl_noise.size)
            if n <= 0:
                raise RuntimeError(f"Invalid empty RTL case: {key}")
            clean_ref = clean_ref[:n]
            rtl_mix = rtl_mix[:n]
            rtl_clean = rtl_clean[:n]
            rtl_noise = rtl_noise[:n]
            hs_rtl = haspi_hasqi_scores(
                clean_ref=clean_ref,
                processed=rtl_mix,
                fs=float(fs),
                audiogram=audiogram,
            )
            rtl_output_snr = float(output_snr_db(rtl_clean, rtl_noise))

            case_rows.append(
                {
                    "case_key": key,
                    "utterance_id": row["utterance_id"],
                    "noise_type": row["noise_type"],
                    "input_snr_db": float(row["input_snr_db"]),
                    "proxy": {
                        "output_snr_db": float(row["proxy_output_snr_db"]),
                        "haspi_v2": float(row["proxy_haspi_v2"]),
                        "hasqi_v2": float(row["proxy_hasqi_v2"]),
                        "backend": row["haspi_backend"],
                    },
                    "rtl": {
                        "output_snr_db": rtl_output_snr,
                        "haspi_v2": float(hs_rtl.get("haspi_v2", float("nan"))),
                        "hasqi_v2": float(hs_rtl.get("hasqi_v2", float("nan"))),
                        "backend": str(hs_rtl.get("backend", "unavailable")),
                        "rtl_mix_out": str(e.get("rtl_mix_out")),
                        "rtl_clean_out": str(e.get("rtl_clean_out")),
                        "rtl_noise_out": str(e.get("rtl_noise_out")),
                    },
                    "delta": {
                        "output_snr_db": float(row["proxy_output_snr_db"]) - rtl_output_snr,
                        "haspi_v2": float(row["proxy_haspi_v2"]) - float(hs_rtl.get("haspi_v2", float("nan"))),
                        "hasqi_v2": float(row["proxy_hasqi_v2"]) - float(hs_rtl.get("hasqi_v2", float("nan"))),
                    },
                }
            )

        expected_cases = int(args.utterances) * len(noise_types) * len(snr_points)
        if missing_cases and not bool(args.allow_partial_matrix):
            raise RuntimeError(
                f"RTL manifest missing {len(missing_cases)} / {expected_cases} cases. "
                f"First missing: {missing_cases[0]}"
            )
        if not case_rows:
            raise RuntimeError("No overlapping cases between proxy matrix and RTL manifest.")

        case_rows = sorted(case_rows, key=lambda r: r["case_key"])
        metrics_data: dict[str, dict[str, Any]] = {}
        criteria = {
            "haspi_v2": {
                "bias_abs_max": float(args.haspi_bias_max),
                "mae_max": float(args.haspi_mae_max),
                "spearman_rho_min": float(args.haspi_rho_min),
            },
            "hasqi_v2": {
                "bias_abs_max": float(args.hasqi_bias_max),
                "mae_max": float(args.hasqi_mae_max),
                "spearman_rho_min": float(args.hasqi_rho_min),
            },
            "output_snr_db": {
                "bias_abs_max": float(args.snr_bias_max),
                "mae_max": float(args.snr_mae_max),
                "spearman_rho_min": float(args.snr_rho_min),
            },
        }
        overall_pass = True
        failures: list[str] = []
        for metric in ["haspi_v2", "hasqi_v2", "output_snr_db"]:
            proxy_vals = [float(r["proxy"][metric]) for r in case_rows]
            rtl_vals = [float(r["rtl"][metric]) for r in case_rows]
            stats = _metric_stats(proxy_vals, rtl_vals)
            metric_pass, errs = _check_acceptance(metric, stats, criteria)
            metrics_data[metric] = {
                "stats": stats,
                "acceptance": criteria[metric],
                "pass": metric_pass,
                "errors": errs,
            }
            if not metric_pass:
                overall_pass = False
                failures.extend(errs)

        out_json = ROOT / args.out_json
        out_md = ROOT / args.out_md
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "matrix": {
                "utterances": int(args.utterances),
                "noise_types": noise_types,
                "input_snr_db": snr_points,
                "allow_partial_matrix": bool(args.allow_partial_matrix),
                "auto_speech_window": bool(args.auto_speech_window),
                "start_seconds": float(args.start_seconds),
                "max_seconds": float(args.max_seconds),
                "expected_cases": expected_cases,
                "actual_cases": len(case_rows),
                "missing_cases": len(missing_cases),
                "missing_case_examples": missing_cases[:5],
                "anechoic_only": True,
            },
            "rtl_manifest": _repo_rel(rtl_manifest_path),
            "criteria": criteria,
            "summary": {
                "overall_pass": overall_pass,
                "metrics": metrics_data,
                "failure_messages": failures,
            },
            "cases": case_rows,
        }
        out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        md_lines = [
            "# Proxy-vs-RTL Correlation (18-case Package)",
            "",
            f"- Generated UTC: `{payload['generated_utc']}`",
            f"- RTL manifest: `{payload['rtl_manifest']}`",
            f"- Cases: `{len(case_rows)}` / `{expected_cases}`",
            f"- Overall pass: `{'PASS' if overall_pass else 'FAIL'}`",
            "",
            f"- Allow partial matrix: `{'yes' if bool(args.allow_partial_matrix) else 'no'}`",
            f"- Missing cases: `{len(missing_cases)}`",
            "",
            "## Scope Note",
            "",
            "- This report compares proxy outputs with captured RTL outputs on the same case matrix.",
            "- It is a correlation diagnostic, not a replacement for L1-L4 RTL electroacoustic verification.",
            "",
            "## Acceptance Summary",
            "",
            "| Metric | N | Bias | MAE | Spearman rho | Pass |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for metric in ["haspi_v2", "hasqi_v2", "output_snr_db"]:
            st = metrics_data[metric]["stats"]
            md_lines.append(
                "| "
                + f"{metric} | {int(st['n'])} | {_fmt(st['bias'])} | {_fmt(st['mae'])} | {_fmt(st['spearman_rho'])} | "
                + f"{'PASS' if metrics_data[metric]['pass'] else 'FAIL'} |"
            )
        if failures:
            md_lines.extend(
                [
                    "",
                    "## Failure Details",
                    "",
                ]
            )
            for msg in failures:
                md_lines.append(f"- {msg}")

        out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

        print(f"[proxy-rtl] Wrote {out_json}")
        print(f"[proxy-rtl] Wrote {out_md}")
        if overall_pass:
            print("[proxy-rtl] PASS")
            return 0
        print("[proxy-rtl] FAIL")
        return 1
    except Exception as exc:
        print(f"[proxy-rtl] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
