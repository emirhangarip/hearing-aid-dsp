#!/usr/bin/env python3
"""
Fetch and lock a reproducible LibriSpeech test-clean subset for paper evaluation.

Behavior:
- Downloads test-clean archive if not already present.
- Extracts under verification/data/LibriSpeech/.
- Selects a 20-utterance subset using:
  1) existing lock manifest (preferred),
  2) explicit utterance_ids from config,
  3) deterministic sorted-first-N fallback.
- Computes SHA256 for each selected file and writes/validates lock manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import urllib.request


try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            blk = fh.read(chunk_size)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def _download(url: str, dst: Path, force: bool = False) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0 and not force:
        print(f"[fetch] Using cached archive: {dst}", flush=True)
        return
    if force and dst.exists():
        dst.unlink()
    tmp = dst.with_suffix(dst.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    print(f"[fetch] Downloading: {url}", flush=True)
    with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    tmp.replace(dst)
    print(f"[fetch] Saved: {dst}", flush=True)


def _extract(archive: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    split_dir = out_dir / "LibriSpeech" / "test-clean"
    if split_dir.exists():
        print(f"[fetch] Using extracted split: {split_dir}", flush=True)
        return
    print(f"[fetch] Extracting {archive} -> {out_dir}", flush=True)
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(out_dir)


def _scan_utterances(split_dir: Path) -> dict[str, Path]:
    files = sorted(split_dir.rglob("*.flac"))
    if not files:
        raise RuntimeError(f"No FLAC files found under {split_dir}")
    return {p.stem: p for p in files}


def _load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid YAML structure: {path}")
    return data


def _lock_entries_from_ids(id_list: list[str], utt_map: dict[str, Path], root: Path) -> list[dict]:
    entries: list[dict] = []
    missing: list[str] = []
    for uid in id_list:
        p = utt_map.get(uid)
        if p is None:
            missing.append(uid)
            continue
        entries.append(
            {
                "utterance_id": uid,
                "relative_path": str(p.relative_to(root)).replace(os.sep, "/"),
                "sha256": _sha256_file(p),
            }
        )
    if missing:
        raise RuntimeError(
            "Configured utterance_ids not found in extracted split: "
            + ", ".join(missing[:8])
            + (" ..." if len(missing) > 8 else "")
        )
    return entries


def _verify_lock(lock_entries: list[dict], root: Path) -> None:
    for row in lock_entries:
        rel = row["relative_path"]
        expected = row["sha256"]
        p = root / rel
        if not p.exists():
            raise RuntimeError(f"Missing locked file: {p}")
        got = _sha256_file(p)
        if got != expected:
            raise RuntimeError(f"SHA mismatch for {p}: got {got}, expected {expected}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="verification/config/paper_eval.yaml")
    ap.add_argument("--refresh-lock", action="store_true", help="Regenerate lock file")
    ap.add_argument("--manifest-out", default="verification/reports/paper/librispeech_subset_manifest.json")
    args = ap.parse_args()

    root = _repo_root()
    cfg = _load_yaml(root / args.config)

    data_cfg = cfg.get("data", {})
    ls_cfg = data_cfg.get("librispeech", {})
    data_root = root / data_cfg.get("root_dir", "verification/data")
    split = ls_cfg.get("split", "test-clean")
    url = ls_cfg.get("url", "https://www.openslr.org/resources/12/test-clean.tar.gz")
    lock_path = root / ls_cfg.get("lock_manifest", "verification/config/librispeech_subset.lock.json")

    archive = data_root / Path(url).name
    _download(url, archive)

    expected_archive_sha = str(ls_cfg.get("archive_sha256", "")).strip()
    if expected_archive_sha:
        got = _sha256_file(archive)
        if got != expected_archive_sha:
            raise RuntimeError(
                f"Archive SHA256 mismatch: got {got}, expected {expected_archive_sha}"
            )

    # Recover automatically from truncated/corrupt cached archives.
    for attempt in range(2):
        try:
            _extract(archive, data_root)
            break
        except (tarfile.ReadError, EOFError) as e:
            if attempt == 1:
                raise RuntimeError(f"Failed to extract archive after retry: {archive}") from e
            print(f"[fetch] Archive appears corrupt ({e}). Re-downloading...", flush=True)
            split_dir = data_root / "LibriSpeech" / split
            if split_dir.exists():
                shutil.rmtree(split_dir, ignore_errors=True)
            _download(url, archive, force=True)

    split_dir = data_root / "LibriSpeech" / split
    utt_map = _scan_utterances(split_dir)

    lock_entries: list[dict]
    use_existing_lock = lock_path.exists() and not args.refresh_lock
    if use_existing_lock:
        with lock_path.open("r", encoding="utf-8") as fh:
            lock_payload = json.load(fh)
        lock_entries = list(lock_payload.get("entries", []))
        selection_mode = str(lock_payload.get("selection_mode", "")).strip().lower()
        if not lock_entries or selection_mode == "uninitialized":
            print(
                f"[fetch] Existing lock is empty/uninitialized; regenerating: {lock_path}",
                flush=True,
            )
            use_existing_lock = False
        else:
            _verify_lock(lock_entries, root)
            print(f"[fetch] Verified existing lock: {lock_path}", flush=True)

    if not use_existing_lock:
        explicit_ids = list(ls_cfg.get("utterance_ids", []) or [])
        if explicit_ids:
            lock_entries = _lock_entries_from_ids(explicit_ids, utt_map, root)
        else:
            n = int(ls_cfg.get("auto_select_first_n", 20))
            selected = sorted(utt_map.keys())[:n]
            lock_entries = _lock_entries_from_ids(selected, utt_map, root)

        lock_payload = {
            "dataset": "LibriSpeech",
            "split": split,
            "selection_mode": "explicit" if explicit_ids else "sorted_first_n",
            "entries": lock_entries,
        }
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w", encoding="utf-8") as fh:
            json.dump(lock_payload, fh, indent=2)
        print(f"[fetch] Wrote lock manifest: {lock_path}", flush=True)

    manifest_out = root / args.manifest_out
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    with manifest_out.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "dataset": "LibriSpeech",
                "split": split,
                "count": len(lock_entries),
                "lock_manifest": str(lock_path.relative_to(root)).replace(os.sep, "/"),
                "entries": lock_entries,
            },
            fh,
            indent=2,
        )
    print(f"[fetch] Wrote runtime manifest: {manifest_out}", flush=True)


if __name__ == "__main__":
    main()
