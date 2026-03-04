#!/usr/bin/env python3
"""
Generate deterministic reverberation impulse responses for paper evaluation.

Primary backend: pyroomacoustics shoebox image-source model.
Fallback backend: deterministic synthetic exponentially-decaying FIR.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import wave

import numpy as np

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


try:
    import pyroomacoustics as pra  # type: ignore
    _PRA_AVAILABLE = True
except Exception:
    _PRA_AVAILABLE = False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_cfg(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        cfg = yaml.safe_load(text)
    else:
        cfg = json.loads(text)
    if not isinstance(cfg, dict):
        raise RuntimeError(f"Invalid config YAML: {path}")
    return cfg


def _synth_fallback_rir(rt60_s: float, fs: int, seed: int) -> np.ndarray:
    if rt60_s <= 0.0:
        h = np.zeros(256, dtype=np.float64)
        h[0] = 1.0
        return h

    rng = np.random.default_rng(seed)
    n = int(max(0.8, 1.2 * rt60_s) * fs)
    t = np.arange(n, dtype=np.float64) / float(fs)

    # -60 dB at rt60 by definition -> exp(-ln(1000) * t / rt60)
    decay = np.exp(-math.log(1000.0) * t / rt60_s)

    h = np.zeros(n, dtype=np.float64)
    h[0] = 1.0

    # Early reflections (fixed delays/gains).
    early_ms = [6, 11, 17, 26, 34, 47]
    early_gain = [0.56, 0.41, 0.31, 0.24, 0.19, 0.14]
    for d_ms, g in zip(early_ms, early_gain):
        idx = int(round((d_ms / 1000.0) * fs))
        if idx < n:
            h[idx] += g * decay[idx]

    # Late diffuse tail.
    tail = rng.standard_normal(n) * 0.015
    h += tail * decay
    return h


def _shoebox_rir(
    rt60_s: float,
    fs: int,
    room_dim_m: list[float],
    source_pos_m: list[float],
    mic_pos_m: list[float],
    max_order: int,
) -> np.ndarray:
    if rt60_s <= 0.0:
        h = np.zeros(256, dtype=np.float64)
        h[0] = 1.0
        return h

    if not _PRA_AVAILABLE:
        raise RuntimeError("pyroomacoustics backend unavailable")

    absorption, max_ord_est = pra.inverse_sabine(rt60_s, room_dim_m)
    max_ord = min(max_order, int(max_ord_est))

    room = pra.ShoeBox(
        room_dim_m,
        fs=fs,
        materials=pra.Material(absorption),
        max_order=max_ord,
    )
    room.add_source(source_pos_m)
    room.add_microphone_array(np.array(mic_pos_m, dtype=np.float64).reshape(3, 1))
    room.compute_rir()
    h = np.asarray(room.rir[0][0], dtype=np.float64)
    return h


def _normalise(h: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(h))) if h.size else 0.0
    if peak <= 1e-18:
        return h
    return h / peak


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="verification/config/paper_eval.yaml")
    ap.add_argument("--out-dir", default="", help="Override output directory")
    ap.add_argument("--backend", choices=["auto", "shoebox", "fallback"], default="auto")
    args = ap.parse_args()

    root = _repo_root()
    cfg = _load_cfg(root / args.config)

    seed = int(cfg.get("random_seed", 20260227))
    eval_cfg = cfg.get("evaluation", {})
    data_cfg = cfg.get("data", {})
    rir_cfg = data_cfg.get("rirs", {})

    rt60_values = [float(x) for x in eval_cfg.get("rt60_s", [0.0, 0.3, 0.6])]
    fs = int(rir_cfg.get("sample_rate_hz", 48000))
    room_dim = [float(x) for x in rir_cfg.get("room_dim_m", [6.0, 5.0, 3.0])]
    src_pos = [float(x) for x in rir_cfg.get("source_pos_m", [2.0, 2.0, 1.5])]
    mic_pos = [float(x) for x in rir_cfg.get("mic_pos_m", [4.0, 3.0, 1.5])]
    max_order = int(rir_cfg.get("max_order", 12))

    out_dir = Path(args.out_dir) if args.out_dir else (root / rir_cfg.get("out_dir", "verification/data/rirs"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.backend == "shoebox":
        backend = "shoebox"
    elif args.backend == "fallback":
        backend = "fallback"
    else:
        backend = "shoebox" if _PRA_AVAILABLE else "fallback"

    rows: list[dict] = []
    for idx, rt60_s in enumerate(rt60_values):
        if backend == "shoebox":
            try:
                h = _shoebox_rir(rt60_s, fs, room_dim, src_pos, mic_pos, max_order)
                used = "shoebox"
            except Exception:
                h = _synth_fallback_rir(rt60_s, fs, seed + idx)
                used = "fallback"
        else:
            h = _synth_fallback_rir(rt60_s, fs, seed + idx)
            used = "fallback"

        h = _normalise(h)
        base = f"rir_rt60_{rt60_s:.1f}s"
        npy_path = out_dir / f"{base}.npy"
        wav_path = out_dir / f"{base}.wav"

        np.save(npy_path, h.astype(np.float32))
        wav_i16 = np.clip(h, -1.0, 1.0)
        wav_pcm = (wav_i16 * 32767.0).astype("<i2")
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(fs)
            wf.writeframes(wav_pcm.tobytes())

        rows.append(
            {
                "rt60_s": rt60_s,
                "backend": used,
                "n_samples": int(h.size),
                "npy": str(npy_path.relative_to(root)).replace(os.sep, "/"),
                "wav": str(wav_path.relative_to(root)).replace(os.sep, "/"),
            }
        )

    manifest = {
        "sample_rate_hz": fs,
        "backend": backend,
        "room_dim_m": room_dim,
        "source_pos_m": src_pos,
        "mic_pos_m": mic_pos,
        "max_order": max_order,
        "rows": rows,
    }

    manifest_path = out_dir / "rir_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"[rir] Wrote {len(rows)} RIRs to {out_dir}")
    print(f"[rir] Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
