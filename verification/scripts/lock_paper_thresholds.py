#!/usr/bin/env python3
"""Lock paper thresholds from collect-mode report summaries."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


REPORT_FILES = [
    "verification/reports/HA_7_output_snr.json",
    "verification/reports/HA_8_haspi_hasqi.json",
    "verification/reports/HA_9_reverb_eval.json",
    "verification/reports/HA_10_modulation_metrics.json",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid thresholds config: {path}")
    return data


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid report payload: {path}")
    return data


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            blk = fh.read(1024 * 1024)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def _clamp_to_floors(
    merged: dict[str, float],
    direction: dict[str, str],
    floors: dict[str, float],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    out: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}
    for metric, value in merged.items():
        v = float(value)
        if metric not in floors:
            out[metric] = v
            meta[metric] = {
                "guard_role": None,
                "guard_value": None,
                "clamp_applied": False,
            }
            continue
        f = float(floors[metric])
        if direction.get(metric, "higher_is_better") == "higher_is_better":
            out[metric] = max(v, f)
            meta[metric] = {
                "guard_role": "floor",
                "guard_value": f,
                "clamp_applied": bool(out[metric] != v),
            }
        else:
            out[metric] = min(v, f)
            meta[metric] = {
                "guard_role": "ceiling",
                "guard_value": f,
                "clamp_applied": bool(out[metric] != v),
            }
    return out, meta


def _fmt_float(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return "-"
    if not math.isfinite(x):
        return "nan"
    return f"{x:.6f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--thresholds", default="verification/config/paper_thresholds.yaml")
    ap.add_argument(
        "--provenance-json",
        default="verification/reports/paper/threshold_lock_provenance.json",
        help="Output JSON provenance artifact path",
    )
    ap.add_argument(
        "--provenance-md",
        default="verification/reports/paper/threshold_lock_provenance.md",
        help="Output Markdown provenance artifact path",
    )
    args = ap.parse_args()

    root = _repo_root()
    th_path = root / args.thresholds
    th_cfg = _load_yaml(th_path)
    direction = {str(k): str(v) for k, v in dict(th_cfg.get("direction", {})).items()}
    abs_floors = {
        str(k): float(v)
        for k, v in dict(th_cfg.get("absolute_floors", {})).items()
        if isinstance(v, (int, float))
    }

    merged: dict[str, float] = {}
    trace: dict[str, list[dict[str, Any]]] = {}
    used_reports: list[str] = []
    for rel in REPORT_FILES:
        p = root / rel
        if not p.exists():
            continue
        used_reports.append(rel)
        data = _load_json(p)
        summary = dict(data.get("summary", {}))
        proposed = dict(data.get("proposed_thresholds", {}))

        # Prefer explicit proposed thresholds emitted by each test.
        for k, v in proposed.items():
            try:
                metric = str(k)
                val = float(v)
                merged[metric] = val
                stat = summary.get(metric, {})
                mean = stat.get("mean")
                std = stat.get("std")
                trace.setdefault(metric, []).append(
                    {
                        "report": rel,
                        "source_kind": "proposed_thresholds",
                        "proposed_value": val,
                        "summary_mean": float(mean) if isinstance(mean, (int, float)) else None,
                        "summary_std": float(std) if isinstance(std, (int, float)) else None,
                    }
                )
            except Exception:
                pass

        # Fallback: derive from summary if proposed missing.
        for metric_key, stats in summary.items():
            metric = str(metric_key)
            if metric in merged:
                continue
            try:
                mean = float(stats["mean"])
                std = float(stats["std"])
            except Exception:
                continue
            if not (math.isfinite(mean) and math.isfinite(std)):
                continue
            dir_mode = str(th_cfg.get("direction", {}).get(metric, "higher_is_better"))
            if dir_mode == "higher_is_better":
                val = mean - 2.0 * std
                source_kind = "summary_mean_minus_2std"
            else:
                val = mean + 2.0 * std
                source_kind = "summary_mean_plus_2std"
            merged[metric] = val
            trace.setdefault(metric, []).append(
                {
                    "report": rel,
                    "source_kind": source_kind,
                    "proposed_value": val,
                    "summary_mean": mean,
                    "summary_std": std,
                }
            )

    merged_pre_clamp = dict(merged)
    merged, clamp_meta = _clamp_to_floors(merged, direction=direction, floors=abs_floors)

    th_cfg["locked"] = True
    th_cfg["thresholds"] = merged

    with th_path.open("w", encoding="utf-8") as fh:
        json.dump(th_cfg, fh, indent=2)

    th_rel = str(th_path.relative_to(root)).replace(os.sep, "/")
    th_sha = _sha256(th_path)

    metrics_rows: list[dict[str, Any]] = []
    for metric in sorted(merged.keys()):
        src_rows = list(trace.get(metric, []))
        selected = src_rows[-1] if src_rows else {}
        cmeta = clamp_meta.get(metric, {})
        metrics_rows.append(
            {
                "metric": metric,
                "direction": direction.get(metric, "higher_is_better"),
                "source_files": sorted({str(r.get("report")) for r in src_rows if r.get("report")}),
                "source_trace": src_rows,
                "selected_source": {
                    "report": selected.get("report"),
                    "source_kind": selected.get("source_kind"),
                    "summary_mean": selected.get("summary_mean"),
                    "summary_std": selected.get("summary_std"),
                },
                "proposed_before_clamp": float(merged_pre_clamp[metric]),
                "guard_role": cmeta.get("guard_role"),
                "guard_value": cmeta.get("guard_value"),
                "clamp_applied": bool(cmeta.get("clamp_applied", False)),
                "final_locked_threshold": float(merged[metric]),
            }
        )

    prov_json_path = root / args.provenance_json
    prov_md_path = root / args.provenance_md
    prov_json_path.parent.mkdir(parents=True, exist_ok=True)
    prov_md_path.parent.mkdir(parents=True, exist_ok=True)

    provenance = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "collect_calibration_refresh",
        "threshold_config": {
            "path": th_rel,
            "sha256": th_sha,
        },
        "report_inputs": {
            "required": REPORT_FILES,
            "used": used_reports,
            "missing": [r for r in REPORT_FILES if r not in used_reports],
        },
        "counts": {
            "locked_metrics": len(merged),
            "reports_used": len(used_reports),
        },
        "metrics": metrics_rows,
    }
    prov_json_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    md_lines = [
        "# Threshold Lock Provenance",
        "",
        f"- Generated UTC: `{provenance['generated_utc']}`",
        f"- Mode: `{provenance['mode']}`",
        f"- Threshold config: `{th_rel}`",
        f"- Threshold config SHA256: `{th_sha}`",
        f"- Reports used: `{len(used_reports)}` / `{len(REPORT_FILES)}`",
        "",
        "| Metric | Direction | Source file(s) | Selected source | Summary mean/std | Proposed (pre-clamp) | Guard | Guard value | Clamp applied | Final locked |",
        "|---|---|---|---|---:|---:|---|---:|---|---:|",
    ]
    for row in metrics_rows:
        sel = row["selected_source"]
        mean = sel.get("summary_mean")
        std = sel.get("summary_std")
        if isinstance(mean, (int, float)) and isinstance(std, (int, float)):
            summary_str = f"{float(mean):.6f} / {float(std):.6f}"
        else:
            summary_str = "-"
        src_files = ", ".join(row["source_files"]) if row["source_files"] else "-"
        sel_src = f"{sel.get('report', '-')}: {sel.get('source_kind', '-')}"
        md_lines.append(
            "| "
            + f"{row['metric']} | {row['direction']} | {src_files} | {sel_src} | "
            + f"{summary_str} | {_fmt_float(row['proposed_before_clamp'])} | "
            + f"{row.get('guard_role') or '-'} | {_fmt_float(row.get('guard_value'))} | "
            + f"{'yes' if row.get('clamp_applied') else 'no'} | {_fmt_float(row['final_locked_threshold'])} |"
        )
    prov_md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"[thresholds] Locked {len(merged)} metric thresholds -> {th_path}")
    print(f"[thresholds] Wrote provenance JSON -> {prov_json_path}")
    print(f"[thresholds] Wrote provenance Markdown -> {prov_md_path}")


if __name__ == "__main__":
    main()
