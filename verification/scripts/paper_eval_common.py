#!/usr/bin/env python3
"""Shared helpers for paper evaluation scripts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy import signal as sp_signal

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid config file: {path}")
    return data


def load_eval_cfg(root: Path) -> dict[str, Any]:
    return load_yaml(root / "verification" / "config" / "paper_eval.yaml")


def load_lock_entries(root: Path, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    ls_cfg = cfg.get("data", {}).get("librispeech", {})
    lock_rel = ls_cfg.get("lock_manifest", "verification/config/librispeech_subset.lock.json")
    lock_path = root / lock_rel
    if not lock_path.exists():
        raise RuntimeError(f"Missing lock manifest: {lock_path}")
    with lock_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    entries = list(payload.get("entries", []))
    if not entries:
        raise RuntimeError(f"Lock manifest has no entries: {lock_path}")
    return entries


def resample_if_needed(x: np.ndarray, fs_in: int, fs_out: int) -> np.ndarray:
    if fs_in == fs_out:
        return x
    g = math.gcd(fs_in, fs_out)
    up = fs_out // g
    down = fs_in // g
    return sp_signal.resample_poly(x, up, down)

