#!/usr/bin/env python3
"""Build the paper figure set (Fig.1..Fig.8) from report JSON artifacts."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TB_DIR = ROOT / "verification" / "tb"
if str(TB_DIR) not in sys.path:
    sys.path.insert(0, str(TB_DIR))

from paper_plotter import apply_paper_style, figure_size, save_figure, CB_PALETTE


REPORTS = ROOT / "verification" / "reports"
PAPER = REPORTS / "paper"
PAPER.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _to_float_map(payload: dict) -> dict[float, float]:
    """
    Accept either legacy {freq: value} map or wrapped {"results": {...}} payload.
    """
    data = payload.get("results", payload)
    if not isinstance(data, dict):
        return {}
    out: dict[float, float] = {}
    for k, v in data.items():
        try:
            out[float(k)] = float(v)
        except Exception:
            continue
    return out


def _group_mean_curve(rows: list[dict], group_key: str, x_key: str, y_key: str) -> dict[str, dict[float, float]]:
    grouped: dict[str, dict[float, list[float]]] = {}
    for r in rows:
        try:
            g = str(r[group_key])
            x = float(r[x_key])
            y = float(r[y_key])
        except Exception:
            continue
        if not np.isfinite(y):
            continue
        grouped.setdefault(g, {}).setdefault(x, []).append(y)

    out: dict[str, dict[float, float]] = {}
    for g, by_x in grouped.items():
        out[g] = {x: float(np.mean(vals)) for x, vals in sorted(by_x.items())}
    return out


def _fig1_architecture() -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    apply_paper_style()
    fig, ax = plt.subplots(figsize=figure_size("two_column"))
    ax.axis("off")

    boxes = [
        (0.02, 0.62, 0.19, 0.22, "Input\nSpeech/Noise"),
        (0.25, 0.62, 0.18, 0.22, "Filterbank\n(10-band)"),
        (0.47, 0.62, 0.18, 0.22, "WDRC\n(LUT + env)"),
        (0.69, 0.62, 0.16, 0.22, "DSM\n1-bit PDM"),
        (0.02, 0.18, 0.20, 0.22, "Layer-1\nAES17"),
        (0.26, 0.18, 0.20, 0.22, "Layer-2\nFilterbank"),
        (0.50, 0.18, 0.20, 0.22, "Layer-3/4\nHA + tau"),
        (0.74, 0.18, 0.22, 0.22, "HA-7..10\nObjective outcomes"),
    ]
    for x, y, w, h, txt in boxes:
        ax.add_patch(plt.Rectangle((x, y), w, h, facecolor="white", edgecolor="#444444", linewidth=1.0))
        ax.text(x + w / 2.0, y + h / 2.0, txt, ha="center", va="center")

    arrows = [
        ((0.21, 0.73), (0.25, 0.73)),
        ((0.43, 0.73), (0.47, 0.73)),
        ((0.65, 0.73), (0.69, 0.73)),
    ]
    for (x1, y1), (x2, y2) in arrows:
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", lw=1.0))

    ax.set_title("Fig.1 — System and Verification-Layer Diagram")
    save_figure(fig, str(PAPER / "fig1_system_layers"), profile="paper", raster_dpi=600)
    plt.close(fig)


def _fig2_thdn() -> None:
    p_core = REPORTS / "test1_thdn_results_core.json"
    p_ana = REPORTS / "test1_thdn_results_analog.json"
    if not (p_core.exists() and p_ana.exists()):
        return

    core = _to_float_map(_load_json(p_core))
    ana = _to_float_map(_load_json(p_ana))
    if not core or not ana:
        return

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    apply_paper_style()
    fig, ax = plt.subplots(figsize=figure_size("two_column"))

    f = sorted(core.keys())
    ax.semilogx(f, [core[x] for x in f], "o-", color=CB_PALETTE["blue"], markersize=4, label="Core THD+N")
    ax.semilogx(f, [ana[x] for x in f], "s-", color=CB_PALETTE["orange"], markersize=4, label="Analog THD+N")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("THD+N (dBc)")
    ax.set_title("Fig.2 — DSM THD+N vs Frequency")
    ax.grid(True, which="both", color="#d0d0d0", linewidth=0.45)
    ax.legend(frameon=True, facecolor="white", edgecolor="#999999")

    save_figure(fig, str(PAPER / "fig2_dsm_thdn_sweep"), profile="paper", raster_dpi=600)
    plt.close(fig)


def _fig3_imd_summary() -> None:
    p_ccif = REPORTS / "test3_ccif_imd.png"
    p_smpte = REPORTS / "test4_smpte_imd.png"

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    # Preferred path: combine original IMD PSD snapshots when available.
    if p_ccif.exists() and p_smpte.exists():
        try:
            import matplotlib.image as mpimg
        except Exception:
            return

        apply_paper_style()
        fig, axs = plt.subplots(1, 2, figsize=figure_size("two_column"))
        for ax, p, t in [
            (axs[0], p_ccif, "CCIF IMD"),
            (axs[1], p_smpte, "SMPTE IMD"),
        ]:
            ax.imshow(mpimg.imread(str(p)))
            ax.set_title(t)
            ax.axis("off")

        save_figure(fig, str(PAPER / "fig3_dsm_imd_summary"), profile="paper", raster_dpi=600)
        plt.close(fig)
        return

    # Fallback path: synthesize a compact comparison from summary markdown.
    summary_md = PAPER / "paper_results_summary.md"
    if not summary_md.exists():
        return
    txt = summary_md.read_text(encoding="utf-8")

    m_ccif = re.search(r"CCIF IMD.*?`(-?\d+(?:\.\d+)?)\s*dBr`", txt)
    m_smpte = re.search(r"SMPTE IMD.*?`(-?\d+(?:\.\d+)?)\s*dBr`", txt)
    if not (m_ccif and m_smpte):
        return

    ccif = float(m_ccif.group(1))
    smpte = float(m_smpte.group(1))

    apply_paper_style()
    fig, ax = plt.subplots(figsize=figure_size("one_column"))
    labels = ["CCIF (1 kHz diff)", "SMPTE (worst sideband)"]
    vals = [ccif, smpte]
    x = np.arange(len(labels))
    colors = [CB_PALETTE["blue"], CB_PALETTE["orange"]]
    ax.bar(x, vals, color=colors, edgecolor=CB_PALETTE["black"], linewidth=0.5)
    ax.axhline(-80.0, color=CB_PALETTE["blue"], linestyle="--", linewidth=1.0, label="CCIF gate (-80 dBr)")
    ax.axhline(-35.0, color=CB_PALETTE["orange"], linestyle="--", linewidth=1.0, label="SMPTE gate (-35 dBr)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Level (dBr)")
    ax.set_title("Fig.3 — DSM IMD Summary")
    ax.grid(True, axis="y", color="#d0d0d0", linewidth=0.45)
    ax.legend(frameon=True, facecolor="white", edgecolor="#999999")

    for i, v in enumerate(vals):
        ax.text(i, v + 1.0, f"{v:.2f}", ha="center", va="bottom", fontsize=8)

    save_figure(fig, str(PAPER / "fig3_dsm_imd_summary"), profile="paper", raster_dpi=600)
    plt.close(fig)


def _fig4_filterbank() -> None:
    p = REPORTS / "L2_1_fb_freq_response.json"
    if not p.exists():
        return
    d = {float(k): float(v) for k, v in _load_json(p).items()}

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    apply_paper_style()
    fig, ax = plt.subplots(figsize=figure_size("two_column"))
    f = sorted(d.keys())
    ref = d.get(1000.0, d[f[len(f)//2]])
    dev = [d[x] - ref for x in f]
    ax.semilogx(f, dev, "o-", color=CB_PALETTE["blue"], markersize=4)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB rel. 1 kHz)")
    ax.set_title("Fig.4 — Filterbank Measured Magnitude Response")
    ax.grid(True, which="both", color="#d0d0d0", linewidth=0.45)

    save_figure(fig, str(PAPER / "fig4_filterbank_response"), profile="paper", raster_dpi=600)
    plt.close(fig)


def _fig5_wdrc_io() -> None:
    p = REPORTS / "HA_1_io_curve.json"
    if not p.exists():
        return
    d = _load_json(p)
    perf = d.get("per_frequency", {})

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    apply_paper_style()
    fig, axs = plt.subplots(1, 2, figsize=figure_size("two_column"))

    for i, (k, row) in enumerate(sorted(perf.items(), key=lambda x: float(x[0]))):
        curve = row.get("curve", [])
        xin = [float(r["input_dbfs"]) for r in curve]
        yout = [float(r["output_dbfs"]) for r in curve]
        ygain = [float(r["gain_db"]) for r in curve]
        c = [CB_PALETTE["blue"], CB_PALETTE["orange"], CB_PALETTE["green"], CB_PALETTE["purple"]][i % 4]
        axs[0].plot(xin, yout, "o-", color=c, label=f"{k} Hz")
        axs[1].plot(xin, ygain, "s-", color=c, label=f"{k} Hz")

    axs[0].set_title("I/O Curve")
    axs[0].set_xlabel("Input (dBFS)")
    axs[0].set_ylabel("Output (dBFS)")
    axs[0].grid(True, color="#d0d0d0", linewidth=0.45)

    axs[1].set_title("Net Gain")
    axs[1].set_xlabel("Input (dBFS)")
    axs[1].set_ylabel("Gain (dB)")
    axs[1].grid(True, color="#d0d0d0", linewidth=0.45)
    axs[1].legend(frameon=True, facecolor="white", edgecolor="#999999")

    fig.suptitle("Fig.5 — WDRC Multiband I/O and Gain Profile")
    fig.tight_layout()
    save_figure(fig, str(PAPER / "fig5_wdrc_io_curves"), profile="paper", raster_dpi=600)
    plt.close(fig)


def _fig6_tau() -> None:
    p = REPORTS / "HA_2_attack_release.json"
    if not p.exists():
        return
    d = _load_json(p)

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    atk = np.asarray(d.get("attack_env_db", []), dtype=np.float64)
    rel = np.asarray(d.get("release_env_db", []), dtype=np.float64)
    if atk.size == 0 or rel.size == 0:
        return

    apply_paper_style()
    fig, axs = plt.subplots(1, 2, figsize=figure_size("two_column"))

    axs[0].plot(atk[:, 0], atk[:, 1], color=CB_PALETTE["blue"])
    axs[0].set_title(f"Attack tau ~ {float(d.get('attack_tau_est_ms', float('nan'))):.2f} ms")
    axs[0].set_xlabel("Time (ms)")
    axs[0].set_ylabel("Envelope (dBFS)")
    axs[0].grid(True, color="#d0d0d0", linewidth=0.45)

    axs[1].plot(rel[:, 0], rel[:, 1], color=CB_PALETTE["orange"])
    axs[1].set_title(f"Release tau ~ {float(d.get('release_tau_est_ms', float('nan'))):.2f} ms")
    axs[1].set_xlabel("Time (ms)")
    axs[1].set_ylabel("Envelope (dBFS)")
    axs[1].grid(True, color="#d0d0d0", linewidth=0.45)

    fig.suptitle("Fig.6 — End-to-End Attack/Release Envelope Fits")
    fig.tight_layout()
    save_figure(fig, str(PAPER / "fig6_attack_release_tau"), profile="paper", raster_dpi=600)
    plt.close(fig)


def _fig7_output_snr() -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    # Fig.7a: anechoic/noise-type sweep from HA-7.
    p7 = REPORTS / "HA_7_output_snr.json"
    if p7.exists():
        d7 = _load_json(p7)
        rows = list(d7.get("rows", []))
        curves = _group_mean_curve(rows, "noise_type", "input_snr_db", "output_snr_db")
        if curves:
            apply_paper_style()
            fig, ax = plt.subplots(figsize=figure_size("two_column"))
            colors = [CB_PALETTE["blue"], CB_PALETTE["orange"], CB_PALETTE["green"], CB_PALETTE["purple"]]
            for i, (name, xy) in enumerate(sorted(curves.items())):
                xs = list(sorted(xy.keys()))
                ys = [xy[x] for x in xs]
                ax.plot(xs, ys, marker="o", color=colors[i % len(colors)], label=name)
            ax.set_xlabel("Input SNR (dB)")
            ax.set_ylabel("Output SNR (dB)")
            ax.set_title("Output SNR Sweep")
            ax.grid(True, which="major", color="#d0d0d0", linewidth=0.6)
            ax.legend(frameon=True, facecolor="white", edgecolor="#999999")
            save_figure(fig, str(PAPER / "fig7_output_snr_sweep"), profile="paper", raster_dpi=600)
            plt.close(fig)

    # Fig.7b: RT60-conditioned sweep from HA-9.
    p9 = REPORTS / "HA_9_reverb_eval.json"
    if p9.exists():
        d9 = _load_json(p9)
        rows9 = list(d9.get("rows", []))
        # Keep the same line semantics as literature test plotting:
        # each line is (noise_type, RT60) pair.
        rows_cond = []
        for r in rows9:
            rr = dict(r)
            try:
                rr["cond"] = f"{rr['noise_type']}, RT60={float(rr['rt60_s']):.1f}s"
                rows_cond.append(rr)
            except Exception:
                continue
        curves9 = _group_mean_curve(rows_cond, "cond", "input_snr_db", "output_snr_db")
        if curves9:
            apply_paper_style()
            fig, ax = plt.subplots(figsize=figure_size("two_column"))
            colors = [CB_PALETTE["blue"], CB_PALETTE["orange"], CB_PALETTE["green"], CB_PALETTE["purple"], CB_PALETTE["red"]]
            for i, (name, xy) in enumerate(sorted(curves9.items())):
                xs = list(sorted(xy.keys()))
                ys = [xy[x] for x in xs]
                ax.plot(xs, ys, marker="o", color=colors[i % len(colors)], label=name)
            ax.set_xlabel("Input SNR (dB)")
            ax.set_ylabel("Output SNR (dB)")
            ax.set_title("Output SNR Under Reverberation")
            ax.grid(True, which="major", color="#d0d0d0", linewidth=0.6)
            ax.legend(frameon=True, facecolor="white", edgecolor="#999999", ncols=2)
            save_figure(fig, str(PAPER / "fig7_output_snr_reverb"), profile="paper", raster_dpi=600)
            plt.close(fig)


def _fig8_haspi_hasqi() -> None:
    p = REPORTS / "HA_8_haspi_hasqi.json"
    if not p.exists():
        return
    d = _load_json(p)
    rows = list(d.get("rows", []))
    if not rows:
        return

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    apply_paper_style()
    fig, axs = plt.subplots(1, 2, figsize=figure_size("two_column"))

    for ax, metric, title in [
        (axs[0], "haspi_v2", "HASPI-v2"),
        (axs[1], "hasqi_v2", "HASQI-v2"),
    ]:
        curves = _group_mean_curve(rows, "noise_type", "input_snr_db", metric)
        plotted = False
        colors = [CB_PALETTE["blue"], CB_PALETTE["orange"], CB_PALETTE["green"]]
        for i, (name, xy) in enumerate(sorted(curves.items())):
            xs = list(sorted(xy.keys()))
            ys = [xy[x] for x in xs]
            if not xs:
                continue
            ax.plot(xs, ys, marker="o", color=colors[i % len(colors)], label=name)
            plotted = True
        if not plotted:
            ax.text(0.5, 0.5, "No finite HASPI/HASQI data", ha="center", va="center", transform=ax.transAxes, fontsize=8)
        ax.set_xlabel("Input SNR (dB)")
        ax.set_ylabel(metric)
        ax.set_title(title)
        ax.grid(True, which="major", color="#d0d0d0", linewidth=0.6)

    if axs[0].lines:
        axs[0].legend(frameon=True, facecolor="white", edgecolor="#999999")
    save_figure(fig, str(PAPER / "fig8_haspi_hasqi"), profile="paper", raster_dpi=600)
    plt.close(fig)


def _fig8_modulation_metrics() -> None:
    p = REPORTS / "HA_10_modulation_metrics.json"
    if not p.exists():
        return
    d = _load_json(p)
    rows = list(d.get("rows", []))
    if not rows:
        return

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    def _mean(key: str) -> float:
        vals = []
        for r in rows:
            try:
                v = float(r[key])
            except Exception:
                continue
            if np.isfinite(v):
                vals.append(v)
        return float(np.mean(vals)) if vals else float("nan")

    apply_paper_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figure_size("two_column"))

    keys1 = ["ecr", "dr_db"]
    labels1 = ["ECR", "DR (dB)"]
    means1 = [_mean(k) for k in keys1]
    x1 = np.arange(len(keys1))
    ax1.bar(x1, means1, color=CB_PALETTE["blue"], edgecolor=CB_PALETTE["black"], linewidth=0.4)
    ax1.set_xticks(x1)
    ax1.set_xticklabels(labels1)
    ax1.set_title("ECR & Dynamic Range")
    ax1.grid(True, axis="y", color="#d0d0d0", linewidth=0.6)
    ax1.set_ylabel("Value")

    keys2 = ["fes", "fbr"]
    labels2 = ["FES", "FBR"]
    means2 = [_mean(k) for k in keys2]
    x2 = np.arange(len(keys2))
    ax2.bar(x2, means2, color=CB_PALETTE["orange"], edgecolor=CB_PALETTE["black"], linewidth=0.4)
    ax2.set_xticks(x2)
    ax2.set_xticklabels(labels2)
    ax2.set_title("Modulation Spectrum Stats")
    ax2.grid(True, axis="y", color="#d0d0d0", linewidth=0.6)
    ax2.set_ylabel("Value")

    fig.suptitle("Modulation Spectrum Analysis (Mean Across Conditions)", fontsize=9)
    fig.tight_layout()
    save_figure(fig, str(PAPER / "fig8_modulation_metrics"), profile="paper", raster_dpi=600)
    plt.close(fig)


def main() -> None:
    _fig1_architecture()
    _fig2_thdn()
    _fig3_imd_summary()
    _fig4_filterbank()
    _fig5_wdrc_io()
    _fig6_tau()
    _fig7_output_snr()
    _fig8_haspi_hasqi()
    _fig8_modulation_metrics()

    # Emit quality summary for generated figures.
    rows = []
    for p in sorted(PAPER.glob("fig*.pdf")) + sorted(PAPER.glob("fig*.png")):
        rows.append(str(p.relative_to(ROOT)).replace(os.sep, "/"))
    out = PAPER / "figure_index.json"
    out.write_text(json.dumps({"figures": rows}, indent=2), encoding="utf-8")
    print(f"[paper-fig] Indexed {len(rows)} figure files -> {out}")


if __name__ == "__main__":
    main()
