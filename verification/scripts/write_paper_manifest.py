#!/usr/bin/env python3
"""Write paper artifact manifest with hashes and git metadata."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            blk = fh.read(1024 * 1024)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def _git_commit(root: Path) -> str:
    try:
        out = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True)
        return out.strip()
    except Exception:
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reports-dir", default="verification/reports")
    ap.add_argument("--out", default="verification/reports/paper/manifest.json")
    ap.add_argument("--glob", action="append", default=["*.json", "*.md", "*.png", "*.pdf"], help="Relative glob under reports dir")
    args = ap.parse_args()

    root = _repo_root()
    reports_dir = root / args.reports_dir

    files: list[Path] = []
    for pat in args.glob:
        files.extend(sorted(reports_dir.rglob(pat)))

    entries = []
    for p in sorted(set(files)):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        entries.append(
            {
                "path": rel,
                "bytes": p.stat().st_size,
                "sha256": _sha256(p),
            }
        )

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(root),
        "reports_dir": str(reports_dir.relative_to(root)).replace(os.sep, "/"),
        "count": len(entries),
        "entries": entries,
    }

    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print(f"[manifest] Wrote {len(entries)} entries -> {out}")


if __name__ == "__main__":
    main()
