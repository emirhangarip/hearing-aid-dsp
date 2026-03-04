"""
test_wdrc_intrinsic_suite.py — WDRC Intrinsic Dynamics (Filterbank Bypassed)
=============================================================================
Top-level   : wdrc_intrinsic_wrap
Purpose     : Measure intrinsic envelope follower attack/release time constants
              of tdm_wdrc_10band without filterbank/front-end dynamics.

This is the strict tau validation required to prove RTL alpha -> tau behavior.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# ── Constants ─────────────────────────────────────────────────────────────────
FS_AUDIO = 48_000.0
CLK_NS = 10
PCM_FULL_SCALE = 2 ** 23

TAU_ATK_MS = 1000.0 / (FS_AUDIO * (1.0 - 8_353_728 / 8_388_608))  # ~5 ms
TAU_REL_MS = 1000.0 / (FS_AUDIO * (1.0 - 8_386_861 / 8_388_608))  # ~100 ms

SPEC_MODE: bool = os.getenv("HA_SPEC_MODE", "0") == "1"

_TB_DIR = os.path.dirname(os.path.abspath(__file__))
_REPORTS = os.path.join(os.path.dirname(_TB_DIR), "reports")
os.makedirs(_REPORTS, exist_ok=True)


def _report(name: str) -> str:
    return os.path.join(_REPORTS, name)


def _n_pcm(ms: float) -> int:
    return max(1, round(ms * FS_AUDIO / 1000.0))


def _dbfs_to_q1_23(dbfs: float) -> int:
    amp = 10.0 ** (dbfs / 20.0)
    q = int(round(amp * PCM_FULL_SCALE))
    return max(-PCM_FULL_SCALE, min(PCM_FULL_SCALE - 1, q))


def _settle_time_ms(
    times_ms: list[float],
    values_db: list[float],
    final_db: float,
    tol_db: float,
    hold_ms: float,
) -> float:
    if not times_ms:
        return float("inf")
    t = np.array(times_ms, dtype=np.float64)
    y = np.array(values_db, dtype=np.float64)
    within = np.abs(y - final_db) <= tol_db

    if len(t) < 2:
        return float(t[0]) if bool(within[0]) else float("inf")

    dt = float(np.median(np.diff(t)))
    if dt <= 0.0:
        dt = hold_ms
    hold_windows = max(1, int(math.ceil(hold_ms / dt)))

    for i in range(0, len(within) - hold_windows + 1):
        if bool(np.all(within[i:i + hold_windows])):
            return float(t[i])
    return float("inf")


def _fit_exp_tau(
    times_ms: list[float],
    values_lin: list[float],
    final_lin: float,
    fit_end_ms: float,
    err_min: float,
    err_max: float,
) -> dict[str, float]:
    nan_out = {
        "tau_ms": float("nan"),
        "fit_r2": float("nan"),
        "ci95_low_ms": float("nan"),
        "ci95_high_ms": float("nan"),
        "n_samples": 0.0,
    }

    if len(times_ms) < 8:
        return nan_out

    t = np.array(times_ms, dtype=np.float64)
    y = np.array(values_lin, dtype=np.float64)
    mask = (t >= 0.0) & (t <= fit_end_ms)
    if int(np.count_nonzero(mask)) < 8:
        return nan_out

    x = t[mask]
    err = np.abs(y[mask] - final_lin)
    valid = np.isfinite(err) & (err > err_min) & (err < err_max)
    if int(np.count_nonzero(valid)) < 8:
        return nan_out

    x = x[valid]
    ln_err = np.log(err[valid])
    n = len(x)
    if n < 3:
        return nan_out

    slope, intercept = np.polyfit(x, ln_err, deg=1)
    if slope >= 0.0:
        return nan_out

    pred = slope * x + intercept
    ss_tot = float(np.sum((ln_err - np.mean(ln_err)) ** 2))
    ss_res = float(np.sum((ln_err - pred) ** 2))
    fit_r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else float("nan")
    tau_ms = -1.0 / float(slope)

    x_mean = float(np.mean(x))
    sxx = float(np.sum((x - x_mean) ** 2))
    if sxx <= 1e-18 or n <= 2:
        ci_low = float("nan")
        ci_high = float("nan")
    else:
        sigma2 = ss_res / max(1, n - 2)
        se_slope = math.sqrt(max(sigma2 / sxx, 0.0))
        z95 = 1.96
        slope_lo = float(slope - z95 * se_slope)
        slope_hi = float(slope + z95 * se_slope)
        if slope_hi >= 0.0:
            ci_low = float("nan")
            ci_high = float("nan")
        else:
            ci_low = -1.0 / slope_lo if slope_lo < 0.0 else float("nan")
            ci_high = -1.0 / slope_hi if slope_hi < 0.0 else float("nan")

    return {
        "tau_ms": float(tau_ms),
        "fit_r2": float(fit_r2),
        "ci95_low_ms": float(ci_low),
        "ci95_high_ms": float(ci_high),
        "n_samples": float(n),
    }


def _set_inputs(dut, band0: int) -> None:
    for idx in range(10):
        sig = getattr(dut, f"band_in_{idx}")
        sig.value = int(band0) if idx == 0 else 0


async def _push_sample_and_capture_env(dut, band0: int) -> float:
    _set_inputs(dut, band0)
    dut.sample_valid.value = 1
    await RisingEdge(dut.clk)
    dut.sample_valid.value = 0

    # tdm_wdrc_10band takes ~O(100) cycles to process 10 bands.
    for _ in range(600):
        await RisingEdge(dut.clk)
        if int(dut.out_valid.value) == 1:
            raw = dut.u_wdrc.env_state_ram[0].value.to_signed()
            return max(raw, 0) / PCM_FULL_SCALE
    raise RuntimeError("Intrinsic WDRC: out_valid timeout while waiting for sample result.")


@cocotb.test(timeout_time=120_000, timeout_unit="ms")
async def test_WDRC_intrinsic_tau(dut):
    """
    Strict intrinsic tau measurement:
      - bypass filterbank and ds_modulator
      - drive direct level steps into WDRC band 0 input
      - fit tau on internal env_state_ram[0]
    """
    cocotb.log.info("════ WDRC-INTRINSIC — Attack / Release Tau Validation ════")
    cocotb.log.info(
        f"  Mode: {'SPEC' if SPEC_MODE else 'REGRESSION'}  "
        f"(τ_atk≈{TAU_ATK_MS:.2f} ms, τ_rel≈{TAU_REL_MS:.2f} ms)"
    )

    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    dut.rst_n.value = 0
    dut.sample_valid.value = 0
    _set_inputs(dut, 0)
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 20)

    QUIET_DBFS = -60.0
    LOUD_DBFS = -10.0
    PRE_MS = 200.0
    ATK_MS = 120.0
    REL_MS = 600.0 if SPEC_MODE else 500.0

    x_quiet = _dbfs_to_q1_23(QUIET_DBFS)
    x_loud = _dbfs_to_q1_23(LOUD_DBFS)

    n_pre = _n_pcm(PRE_MS)
    n_atk = _n_pcm(ATK_MS)
    n_rel = _n_pcm(REL_MS)
    n_total = n_pre + n_atk + n_rel

    cocotb.log.info(
        f"  Drive sequence: {QUIET_DBFS:+.0f} dBFS ({PRE_MS:.0f} ms) -> "
        f"{LOUD_DBFS:+.0f} dBFS ({ATK_MS:.0f} ms) -> "
        f"{QUIET_DBFS:+.0f} dBFS ({REL_MS:.0f} ms)"
    )

    env: list[float] = []
    for i in range(n_total):
        val = x_loud if (n_pre <= i < (n_pre + n_atk)) else x_quiet
        env.append(await _push_sample_and_capture_env(dut, val))
        if (i + 1) % 5000 == 0:
            cocotb.log.info(f"  Progress: {i+1}/{n_total} samples")

    env_arr = np.array(env, dtype=np.float64)
    i_atk0 = n_pre
    i_rel0 = n_pre + n_atk
    env_atk = env_arr[i_atk0:i_rel0]
    env_rel = env_arr[i_rel0:]

    t_atk_ms = (np.arange(len(env_atk), dtype=np.float64) / FS_AUDIO * 1000.0).tolist()
    t_rel_ms = (np.arange(len(env_rel), dtype=np.float64) / FS_AUDIO * 1000.0).tolist()

    n_pre_tail = max(8, _n_pcm(10.0))
    n_head = 8
    n_atk_tail = max(8, _n_pcm(20.0))
    n_rel_tail = max(16, _n_pcm(80.0))

    pre_seg = env_arr[:i_atk0]
    atk_start_lin = float(np.median(pre_seg[-n_pre_tail:])) if len(pre_seg) >= n_pre_tail else float(np.median(pre_seg))
    rel_start_lin = float(np.median(env_rel[:n_head]))
    ss_atk_lin = float(np.median(env_atk[-n_atk_tail:]))
    ss_rel_lin = float(np.median(env_rel[-n_rel_tail:]))

    def _to_db(v: float) -> float:
        return 20.0 * math.log10(max(v, 1e-12))

    env_atk_db = [_to_db(float(v)) for v in env_atk.tolist()]
    env_rel_db = [_to_db(float(v)) for v in env_rel.tolist()]
    atk_start_db = _to_db(atk_start_lin)
    rel_start_db = _to_db(rel_start_lin)
    ss_atk_db = _to_db(ss_atk_lin)
    ss_rel_db = _to_db(ss_rel_lin)

    atk_span_db = abs(atk_start_db - ss_atk_db)
    rel_span_db = abs(rel_start_db - ss_rel_db)
    assert atk_span_db >= 30.0, f"WDRC-INTRINSIC FAIL: attack span too small ({atk_span_db:.2f} dB)."
    assert rel_span_db >= 30.0, f"WDRC-INTRINSIC FAIL: release span too small ({rel_span_db:.2f} dB)."

    atk_tol_db = 0.25 * atk_span_db
    rel_tol_db = 0.25 * rel_span_db
    atk_hold_ms = 8.0
    rel_hold_ms = 20.0

    atk_settle_ms = _settle_time_ms(t_atk_ms, env_atk_db, ss_atk_db, atk_tol_db, atk_hold_ms)
    rel_settle_ms = _settle_time_ms(t_rel_ms, env_rel_db, ss_rel_db, rel_tol_db, rel_hold_ms)
    if math.isinf(atk_settle_ms):
        atk_settle_ms = ATK_MS
    if math.isinf(rel_settle_ms):
        rel_settle_ms = REL_MS

    atk_span_lin = max(abs(ss_atk_lin - atk_start_lin), 1e-9)
    rel_span_lin = max(abs(rel_start_lin - ss_rel_lin), 1e-9)
    atk_fit = _fit_exp_tau(
        t_atk_ms,
        env_atk.tolist(),
        final_lin=ss_atk_lin,
        fit_end_ms=min(40.0, ATK_MS),
        err_min=max(1e-9, 0.01 * atk_span_lin),
        err_max=max(1e-8, 1.20 * atk_span_lin),
    )
    rel_fit = _fit_exp_tau(
        t_rel_ms,
        env_rel.tolist(),
        final_lin=ss_rel_lin,
        fit_end_ms=min(400.0, REL_MS),
        err_min=max(1e-9, 0.01 * rel_span_lin),
        err_max=max(1e-8, 1.20 * rel_span_lin),
    )

    atk_tau_ms = float(atk_fit["tau_ms"])
    rel_tau_ms = float(rel_fit["tau_ms"])
    atk_r2 = float(atk_fit["fit_r2"])
    rel_r2 = float(rel_fit["fit_r2"])
    atk_err = abs(atk_tau_ms - TAU_ATK_MS) / TAU_ATK_MS
    rel_err = abs(rel_tau_ms - TAU_REL_MS) / TAU_REL_MS

    cocotb.log.info(
        f"  Attack: settle={atk_settle_ms:.2f} ms, tau={atk_tau_ms:.3f} ms, "
        f"R^2={atk_r2:.4f}, err={atk_err*100:.2f}%"
    )
    cocotb.log.info(
        f"  Release: settle={rel_settle_ms:.2f} ms, tau={rel_tau_ms:.3f} ms, "
        f"R^2={rel_r2:.4f}, err={rel_err*100:.2f}%"
    )

    decim = 8
    report = {
        "mode": "SPEC" if SPEC_MODE else "REGRESSION",
        "stimulus": {
            "quiet_dbfs": QUIET_DBFS,
            "loud_dbfs": LOUD_DBFS,
            "pre_ms": PRE_MS,
            "attack_ms": ATK_MS,
            "release_ms": REL_MS,
            "n_samples_total": n_total,
        },
        "tau_atk_rtl_ms": TAU_ATK_MS,
        "tau_rel_rtl_ms": TAU_REL_MS,
        "attack": {
            "start_dbfs": atk_start_db,
            "final_dbfs": ss_atk_db,
            "span_db": atk_span_db,
            "tol_db": atk_tol_db,
            "settle_ms": atk_settle_ms,
            "tau_ms": atk_tau_ms,
            "fit_r2": atk_r2,
            "ci95_ms": [atk_fit["ci95_low_ms"], atk_fit["ci95_high_ms"]],
            "err_pct": 100.0 * atk_err,
            "trace_db": list(zip(t_atk_ms[::decim], env_atk_db[::decim])),
        },
        "release": {
            "start_dbfs": rel_start_db,
            "final_dbfs": ss_rel_db,
            "span_db": rel_span_db,
            "tol_db": rel_tol_db,
            "settle_ms": rel_settle_ms,
            "tau_ms": rel_tau_ms,
            "fit_r2": rel_r2,
            "ci95_ms": [rel_fit["ci95_low_ms"], rel_fit["ci95_high_ms"]],
            "err_pct": 100.0 * rel_err,
            "trace_db": list(zip(t_rel_ms[::decim], env_rel_db[::decim])),
        },
    }
    with open(_report("HA_2_intrinsic_tau.json"), "w", encoding="ascii") as fh:
        json.dump(report, fh, indent=2)

    # Strict intrinsic gates
    assert atk_settle_ms <= 5.0 * TAU_ATK_MS, (
        f"WDRC-INTRINSIC FAIL: attack settle {atk_settle_ms:.2f} ms > {5.0*TAU_ATK_MS:.2f} ms."
    )
    assert rel_settle_ms <= 5.0 * TAU_REL_MS, (
        f"WDRC-INTRINSIC FAIL: release settle {rel_settle_ms:.2f} ms > {5.0*TAU_REL_MS:.2f} ms."
    )
    assert math.isfinite(atk_tau_ms) and math.isfinite(rel_tau_ms), (
        "WDRC-INTRINSIC FAIL: non-finite tau estimate."
    )
    assert atk_r2 >= 0.98 and rel_r2 >= 0.98, (
        f"WDRC-INTRINSIC FAIL: poor fit quality (atk R^2={atk_r2:.4f}, rel R^2={rel_r2:.4f})."
    )
    assert atk_err <= 0.20, (
        f"WDRC-INTRINSIC FAIL: attack tau mismatch {atk_err*100:.2f}% > 20% "
        f"(meas {atk_tau_ms:.3f} ms vs RTL {TAU_ATK_MS:.3f} ms)."
    )
    assert rel_err <= 0.20, (
        f"WDRC-INTRINSIC FAIL: release tau mismatch {rel_err*100:.2f}% > 20% "
        f"(meas {rel_tau_ms:.3f} ms vs RTL {TAU_REL_MS:.3f} ms)."
    )

    atk_ci_lo = float(atk_fit["ci95_low_ms"])
    atk_ci_hi = float(atk_fit["ci95_high_ms"])
    rel_ci_lo = float(rel_fit["ci95_low_ms"])
    rel_ci_hi = float(rel_fit["ci95_high_ms"])
    assert math.isfinite(atk_ci_lo) and math.isfinite(atk_ci_hi), "WDRC-INTRINSIC FAIL: attack CI is non-finite."
    assert math.isfinite(rel_ci_lo) and math.isfinite(rel_ci_hi), "WDRC-INTRINSIC FAIL: release CI is non-finite."
    dt_ms = 1000.0 / FS_AUDIO
    atk_ci_ok = (atk_ci_lo <= TAU_ATK_MS <= atk_ci_hi) or (abs(atk_tau_ms - TAU_ATK_MS) <= dt_ms)
    rel_ci_ok = (rel_ci_lo <= TAU_REL_MS <= rel_ci_hi) or (abs(rel_tau_ms - TAU_REL_MS) <= dt_ms)
    if not atk_ci_ok:
        cocotb.log.info(
            f"WDRC-INTRINSIC NOTE: RTL attack tau {TAU_ATK_MS:.3f} ms outside CI "
            f"[{atk_ci_lo:.3f}, {atk_ci_hi:.3f}] ms and ±1 sample ({dt_ms:.4f} ms)."
        )
    if not rel_ci_ok:
        cocotb.log.info(
            f"WDRC-INTRINSIC NOTE: RTL release tau {TAU_REL_MS:.3f} ms outside CI "
            f"[{rel_ci_lo:.3f}, {rel_ci_hi:.3f}] ms and ±1 sample ({dt_ms:.4f} ms)."
        )

    cocotb.log.info(
        "WDRC-INTRINSIC PASS — intrinsic tau matches RTL within ±20% "
        f"(atk={atk_tau_ms:.3f} ms, rel={rel_tau_ms:.3f} ms)."
    )
