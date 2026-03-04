"""
paper_plotter.py

Shared plotting style for paper-ready figures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.artist import Artist
except Exception:  # pragma: no cover
    plt = None  # type: ignore


@dataclass(frozen=True)
class PaperStyle:
    font_family: str = "DejaVu Serif"
    base_fontsize: float = 9.0
    axis_labelsize: float = 9.0
    title_size: float = 9.5
    tick_size: float = 8.0
    legend_size: float = 8.0
    line_width: float = 1.2


CB_PALETTE = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "green": "#009E73",
    "red": "#D55E00",
    "purple": "#CC79A7",
    "gray": "#7F7F7F",
    "black": "#222222",
}


def apply_paper_style(style: PaperStyle | None = None) -> None:
    if plt is None:
        return
    s = style or PaperStyle()
    plt.rcParams.update(
        {
            "font.family": s.font_family,
            "font.size": s.base_fontsize,
            "axes.labelsize": s.axis_labelsize,
            "axes.titlesize": s.title_size,
            "xtick.labelsize": s.tick_size,
            "ytick.labelsize": s.tick_size,
            "legend.fontsize": s.legend_size,
            "lines.linewidth": s.line_width,
            "axes.facecolor": "white",
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )


def figure_size(preset: str) -> tuple[float, float]:
    """
    IEEE-friendly presets.

    - one_column: ~3.5 inch width
    - two_column: ~7.16 inch width
    """
    if preset == "one_column":
        return (3.5, 2.4)
    if preset == "two_column":
        return (7.16, 3.0)
    if preset == "square":
        return (3.5, 3.5)
    return (6.0, 3.6)


def save_figure(
    fig,
    filepath: str,
    profile: str = "paper",
    raster_dpi: int = 600,
    bbox_tight: bool = True,
) -> dict[str, str]:
    """Save vector-first paper figures. Returns written paths."""
    out = {}
    p = Path(filepath)
    p.parent.mkdir(parents=True, exist_ok=True)

    tight = "tight" if bbox_tight else None

    if profile == "paper":
        pdf_path = p.with_suffix(".pdf")
        fig.savefig(str(pdf_path), bbox_inches=tight)
        out["pdf"] = str(pdf_path)

    png_path = p.with_suffix(".png")
    fig.savefig(str(png_path), dpi=raster_dpi if profile == "paper" else 300, bbox_inches=tight)
    out["png"] = str(png_path)

    return out


def _iter_text_artists(fig) -> Iterable[Artist]:
    for ax in fig.axes:
        yield ax.title
        yield ax.xaxis.label
        yield ax.yaxis.label
        for t in ax.get_xticklabels() + ax.get_yticklabels():
            yield t
        leg = ax.get_legend()
        if leg is not None:
            for txt in leg.get_texts():
                yield txt
        for txt in ax.texts:
            yield txt


def check_figure_quality(fig, min_fontsize: float = 7.0) -> list[str]:
    """
    Return quality warnings (small fonts and obvious text overlaps).

    Overlap detection is intentionally simple to avoid heavyweight dependencies.
    """
    warnings: list[str] = []
    if plt is None:
        return ["matplotlib unavailable"]

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    bboxes = []
    for artist in _iter_text_artists(fig):
        try:
            fs = float(artist.get_fontsize())
            if fs < min_fontsize:
                warnings.append(f"font too small: {fs:.1f} pt")
            bb = artist.get_window_extent(renderer=renderer)
            if bb.width > 0 and bb.height > 0:
                bboxes.append(bb)
        except Exception:
            continue

    # Pairwise bbox overlap check.
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            if bboxes[i].overlaps(bboxes[j]):
                warnings.append("text overlap detected")
                return warnings

    return warnings
