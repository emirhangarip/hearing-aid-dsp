"""
test_proxy_rtl_capture_suite.py

Generate RTL-rendered outputs for the 18-case proxy-vs-RTL package:
3 utterances x 2 noise types x 3 SNR points (-5/0/+5 dB).

Outputs:
  - verification/reports/paper/rtl_cases/caseXXX_{mix,clean,noise}.wav
  - verification/reports/paper/proxy_rtl_capture_manifest.json
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import wave

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
import numpy as np

try:
    import soundfile as sf  # type: ignore
except Exception:
    sf = None

from objective_speech_metrics import make_babble_noise, make_speech_shaped_noise, mix_at_snr


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "verification" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from paper_eval_common import load_eval_cfg, load_lock_entries, resample_if_needed

FS_SYS = float(os.environ.get("FS_SYS", "12288000"))
FS_AUDIO = float(os.environ.get("PROXY_RTL_AUDIO_FS", "48000"))
CLK_PS = int(os.environ.get("PROXY_RTL_CLK_PS", "10000"))
OSR_EFFECTIVE = max(1, round(FS_SYS / FS_AUDIO))
PCM_FULL_SCALE = float(2 ** 23)  # internal u_tdm_core audio_out Q1.23
DIN_FULL_SCALE = float((1 << 31) - 1)  # wrapper din is signed [31:0]

UTTERANCE_COUNT = int(os.environ.get("PROXY_RTL_UTTERANCES", "3"))
NOISE_TYPES = [s.strip() for s in os.environ.get("PROXY_RTL_NOISE_TYPES", "speech_shaped,babble").split(",") if s.strip()]
SNR_POINTS = [float(s.strip()) for s in os.environ.get("PROXY_RTL_SNR_DB", "-5,0,5").split(",") if s.strip()]
# Keep at least 0.4 s to satisfy lock-entry minimum duration checks.
MAX_SECONDS = float(os.environ.get("PROXY_RTL_MAX_SECONDS", "0.4"))
START_SECONDS = float(os.environ.get("PROXY_RTL_START_SECONDS", "0.0"))
LATENCY_SAMPLES = int(os.environ.get("PROXY_RTL_LATENCY_SAMPLES", "51"))
CASE_SPEC = os.environ.get("PROXY_RTL_CASES", "1-18").strip()
AUTO_SPEECH_WINDOW = str(os.environ.get("PROXY_RTL_AUTO_SPEECH", "1")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

OUT_DIR = REPO_ROOT / os.environ.get("PROXY_RTL_OUT_DIR", "verification/reports/paper/rtl_cases")
MANIFEST_PATH = REPO_ROOT / os.environ.get(
    "PROXY_RTL_MANIFEST",
    "verification/reports/paper/proxy_rtl_capture_manifest.json",
)


def _repo_rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace(os.sep, "/")


def _parse_case_spec(spec: str, total_cases: int) -> set[int]:
    text = str(spec).strip()
    if not text or text == "*":
        return set(range(1, total_cases + 1))

    selected: set[int] = set()
    for raw_tok in text.split(","):
        tok = raw_tok.strip()
        if not tok:
            continue
        if "-" in tok:
            parts = [p.strip() for p in tok.split("-", 1)]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise RuntimeError(f"Invalid PROXY_RTL_CASES token: '{tok}'")
            lo = int(parts[0])
            hi = int(parts[1])
            if lo > hi:
                raise RuntimeError(f"Invalid PROXY_RTL_CASES range '{tok}': start > end")
            for idx in range(lo, hi + 1):
                if idx < 1 or idx > total_cases:
                    raise RuntimeError(
                        f"Case index {idx} out of range 1..{total_cases} in PROXY_RTL_CASES='{spec}'"
                    )
                selected.add(idx)
        else:
            idx = int(tok)
            if idx < 1 or idx > total_cases:
                raise RuntimeError(
                    f"Case index {idx} out of range 1..{total_cases} in PROXY_RTL_CASES='{spec}'"
                )
            selected.add(idx)

    if not selected:
        raise RuntimeError(f"PROXY_RTL_CASES selected no cases: '{spec}'")
    return selected


def _float_to_din_i32(x: np.ndarray) -> list[int]:
    xx = np.asarray(x, dtype=np.float64)
    clipped = np.clip(xx, -1.0, 0.999999999)
    vals = np.round(clipped * DIN_FULL_SCALE).astype(np.int64)
    return [int(v) for v in vals.tolist()]


def _best_energy_start(x: np.ndarray, win_n: int) -> int:
    if win_n <= 0 or x.size <= win_n:
        return 0
    # Pick the highest short-time energy window to avoid silence-heavy clips.
    xx = np.asarray(x, dtype=np.float64)
    energy = xx * xx
    csum = np.concatenate(([0.0], np.cumsum(energy)))
    win_energy = csum[win_n:] - csum[:-win_n]
    if win_energy.size == 0:
        return 0
    return int(np.argmax(win_energy))


def _align_to_input(y: np.ndarray, n_in: int, latency_samples: int) -> np.ndarray:
    yy = np.asarray(y, dtype=np.float64)
    if latency_samples > 0 and yy.size > latency_samples:
        yy = yy[latency_samples:]
    if yy.size >= n_in:
        return yy[:n_in]
    return np.pad(yy, (0, n_in - yy.size))


def _write_wav_pcm32(path: Path, x: np.ndarray, fs: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    xx = np.asarray(x, dtype=np.float64)
    xx = np.clip(xx, -1.0, 0.999999999)
    i32 = np.round(xx * DIN_FULL_SCALE).astype("<i4")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(4)
        wf.setframerate(int(fs))
        wf.writeframes(i32.tobytes())


def _load_audio_pool(cfg: dict, fs_target: int, utterance_count: int) -> list[dict]:
    if sf is None:
        raise RuntimeError("soundfile is required for proxy RTL capture generation")
    entries = load_lock_entries(REPO_ROOT, cfg)
    pool: list[dict] = []
    for row in entries:
        rel = str(row.get("relative_path", "")).strip()
        if not rel:
            continue
        p = REPO_ROOT / rel
        if not p.exists():
            continue
        x, fs_in = sf.read(str(p), always_2d=False)
        x = np.asarray(x, dtype=np.float64)
        if x.ndim > 1:
            x = np.mean(x, axis=1)
        x = resample_if_needed(x, int(fs_in), fs_target)
        max_n = max(1, int(round(float(MAX_SECONDS) * fs_target)))
        if x.size <= 0:
            continue
        if x.size <= max_n:
            start_n = 0
        else:
            req_start_n = max(0, int(round(float(START_SECONDS) * fs_target)))
            if AUTO_SPEECH_WINDOW and float(START_SECONDS) <= 0.0:
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


async def _reset(dut, cycles: int = 20) -> None:
    dut.rst_n.value = 0
    dut.din.value = 0
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 4)


async def _render_signal(dut, signal_f: np.ndarray) -> np.ndarray:
    din_samples = _float_to_din_i32(signal_f)
    captured: list[float] = []
    collecting = True

    async def _capture():
        while collecting:
            await RisingEdge(dut.clk)
            try:
                if int(dut.u_tdm_core.out_valid.value) == 1:
                    raw = dut.u_tdm_core.audio_out.value.to_signed()
                    captured.append(float(raw) / PCM_FULL_SCALE)
            except Exception:
                continue

    cap_task = cocotb.start_soon(_capture())

    for s in din_samples:
        dut.din.value = int(s) & 0xFFFF_FFFF
        await ClockCycles(dut.clk, OSR_EFFECTIVE)

    # Drain to include latency tail for alignment.
    dut.din.value = 0
    for _ in range(max(8, LATENCY_SAMPLES + 8)):
        await ClockCycles(dut.clk, OSR_EFFECTIVE)

    collecting = False
    cap_task.cancel()

    y = np.asarray(captured, dtype=np.float64)
    return _align_to_input(y, len(din_samples), LATENCY_SAMPLES)


@cocotb.test(timeout_time=3_600_000, timeout_unit="ms")
async def test_proxy_rtl_capture_18cases(dut):
    """
    Capture RTL outputs for proxy-vs-RTL validation package.
    """
    if len(NOISE_TYPES) != 2 or len(SNR_POINTS) != 3:
        raise RuntimeError("Expected exactly 2 noise types and 3 SNR points")

    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    await _reset(dut)

    cfg = load_eval_cfg(REPO_ROOT)
    seed = int(cfg.get("random_seed", 20260227))
    fs = int(FS_AUDIO)
    pool = _load_audio_pool(cfg, fs_target=fs, utterance_count=UTTERANCE_COUNT)
    pool_signals = [np.asarray(p["signal"], dtype=np.float64) for p in pool]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[dict] = []
    case_idx = 0
    total_cases = len(pool) * len(NOISE_TYPES) * len(SNR_POINTS)
    selected_cases = _parse_case_spec(CASE_SPEC, total_cases)
    cocotb.log.info(
        f"[proxy-capture] case selection: {len(selected_cases)}/{total_cases} "
        f"(PROXY_RTL_CASES='{CASE_SPEC}')"
    )

    for u_idx, utt in enumerate(pool):
        uid = str(utt["utterance_id"])
        start_eff_s = float(utt.get("start_seconds_effective", START_SECONDS))
        clean = np.asarray(utt["signal"], dtype=np.float64)
        for n_idx, noise_type in enumerate(NOISE_TYPES):
            if noise_type == "speech_shaped":
                noise = make_speech_shaped_noise(clean, seed=seed + 1000 * u_idx + n_idx)
            elif noise_type == "babble":
                noise = make_babble_noise(pool_signals, clean.size, seed=seed + 3000 * u_idx + n_idx)
            else:
                raise RuntimeError(f"Unsupported noise type: {noise_type}")

            for snr_db in SNR_POINTS:
                case_idx += 1
                if case_idx not in selected_cases:
                    continue
                mix, noise_scaled = mix_at_snr(clean, noise, snr_db=float(snr_db))

                await _reset(dut)
                rtl_mix = await _render_signal(dut, mix)
                await _reset(dut)
                rtl_clean = await _render_signal(dut, clean)
                await _reset(dut)
                rtl_noise = await _render_signal(dut, noise_scaled)

                stem = f"case{case_idx:03d}_{uid}_{noise_type}_{int(float(snr_db)):+d}dB".replace(" ", "_")
                p_mix = OUT_DIR / f"{stem}_mix.wav"
                p_clean = OUT_DIR / f"{stem}_clean.wav"
                p_noise = OUT_DIR / f"{stem}_noise.wav"
                _write_wav_pcm32(p_mix, rtl_mix, fs)
                _write_wav_pcm32(p_clean, rtl_clean, fs)
                _write_wav_pcm32(p_noise, rtl_noise, fs)

                manifest_entries.append(
                    {
                        "case_index": int(case_idx),
                        "utterance_id": uid,
                        "noise_type": str(noise_type),
                        "input_snr_db": float(snr_db),
                        "start_seconds_effective": float(start_eff_s),
                        "rtl_mix_out": _repo_rel(p_mix),
                        "rtl_clean_out": _repo_rel(p_clean),
                        "rtl_noise_out": _repo_rel(p_noise),
                    }
                )
                cocotb.log.info(
                    f"[proxy-capture] case {case_idx:02d}: {uid}, {noise_type}, "
                    f"SNR={float(snr_db):+.1f} dB -> {p_mix.name}"
                )

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "sample_rate_hz": fs,
        "utterance_count": int(UTTERANCE_COUNT),
        "noise_types": [str(x) for x in NOISE_TYPES],
        "snr_db": [float(x) for x in SNR_POINTS],
        "case_spec": str(CASE_SPEC),
        "expected_case_count": int(total_cases),
        "captured_case_count": int(len(manifest_entries)),
        "selected_case_indices": sorted(int(x) for x in selected_cases),
        "auto_speech_window": bool(AUTO_SPEECH_WINDOW),
        "start_seconds_request": float(START_SECONDS),
        "start_seconds": float(START_SECONDS),
        "max_seconds": float(MAX_SECONDS),
        "latency_samples_compensated": int(LATENCY_SAMPLES),
        "entries": manifest_entries,
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cocotb.log.info(
        f"[proxy-capture] wrote manifest with {len(manifest_entries)} cases -> {MANIFEST_PATH}"
    )
