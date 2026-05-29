#!/usr/bin/env python3
"""
Decompress Sam's workout drops losslessly, keeping every original.

Policy (per user): we ALWAYS keep the original file exactly as downloaded, and
produce a verified decompressed sibling next to it so downstream parsing reads
raw, uncompressed data with zero loss.

For each compressed workout in data/sam_workouts/ (*.fit.gz, *.tcx.gz,
*.pwx.gz, or *.gz), this script:
  1. Verifies the gzip container's stored CRC32 (`gzip -t` equivalent) so a
     corrupt download is caught before we trust it.
  2. Decompresses to the sibling without the .gz extension, KEEPING the
     original .gz intact.
  3. Verifies the decompressed byte length matches the gzip footer's ISIZE,
     confirming a complete, lossless extraction.

Idempotent: skips files whose decompressed sibling already exists and verifies
clean. Safe to re-run after every new download.

Usage:
    python3 scripts/decompress_workouts.py
"""
import gzip
import struct
import sys
from pathlib import Path

WORKOUT_DIR = Path(__file__).resolve().parent.parent / "data" / "sam_workouts"

# Compressed extensions we handle. (.fit/.tcx/.pwx that are already
# uncompressed are left alone — nothing to do.)
GZ_SUFFIX = ".gz"


def gzip_isize(path: Path) -> int:
    """Read the gzip footer's ISIZE field (uncompressed size mod 2^32)."""
    with open(path, "rb") as f:
        f.seek(-4, 2)
        return struct.unpack("<I", f.read(4))[0]


def decompress_one(gz_path: Path) -> tuple[str, str]:
    """Returns (status, message) for one .gz file. Never deletes the original."""
    out_path = gz_path.with_suffix("")  # strip trailing .gz

    # 1. CRC integrity check by streaming the whole thing through gzip.
    try:
        with gzip.open(gz_path, "rb") as f:
            n = 0
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        return ("CORRUPT", f"{gz_path.name}: failed CRC/decompress ({e})")

    # 2. Lossless-size assertion against the gzip footer.
    expected = gzip_isize(gz_path)
    if (n % (1 << 32)) != expected:
        return ("CORRUPT",
                f"{gz_path.name}: size mismatch (got {n}, footer says {expected})")

    # 3. Idempotency: if a decompressed sibling already exists and matches, skip.
    if out_path.exists() and out_path.stat().st_size == n:
        return ("OK-EXISTS", f"{out_path.name}: already decompressed ({n:,} bytes)")

    # 4. Write the decompressed sibling, keeping the original .gz untouched.
    with gzip.open(gz_path, "rb") as fin, open(out_path, "wb") as fout:
        while True:
            chunk = fin.read(1 << 20)
            if not chunk:
                break
            fout.write(chunk)

    written = out_path.stat().st_size
    if written != n:
        return ("ERROR",
                f"{out_path.name}: wrote {written} bytes, expected {n}")
    return ("DECOMPRESSED", f"{out_path.name}: {written:,} bytes (original kept)")


def main() -> int:
    if not WORKOUT_DIR.exists():
        print(f"No workout dir at {WORKOUT_DIR}", file=sys.stderr)
        return 1

    gz_files = sorted(p for p in WORKOUT_DIR.iterdir() if p.suffix == GZ_SUFFIX)
    if not gz_files:
        print("No compressed (.gz) workout files to process.")
        return 0

    bad = 0
    for gz in gz_files:
        status, msg = decompress_one(gz)
        print(f"[{status:12}] {msg}")
        if status in ("CORRUPT", "ERROR"):
            bad += 1

    print(f"\n{len(gz_files)} compressed file(s) checked, {bad} problem(s).")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
