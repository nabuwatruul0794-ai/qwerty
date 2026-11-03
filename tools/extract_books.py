#!/usr/bin/env python3
"""Extract text from books/*.pdf into books_index/*.txt.gz (one file per book).

Runs one PDF per worker process. Finished books are cached by mtime, so the
script can be restarted safely.
"""
import gzip
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

logging.disable(logging.CRITICAL)

BASE = Path(__file__).resolve().parent.parent
BOOKS = BASE / "books"
INDEX = BASE / "books_index"
INDEX.mkdir(exist_ok=True)


def subject_rank(name: str) -> int:
    n = name.lower()
    if "fizika" in n:
        return 0
    if "informatika" in n:
        return 1
    if "algebra" in n:
        return 2
    if "geometriya" in n:
        return 3
    if "matem" in n:
        return 4
    return 5


def extract_one(path_str: str) -> tuple:
    logging.disable(logging.CRITICAL)
    path = Path(path_str)
    name = path.stem
    out = INDEX / f"{name}.txt.gz"
    if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
        try:
            with gzip.open(out, "rt", encoding="utf-8") as fh:
                return (name, sum(1 for _ in fh), 0.0, "cached")
        except Exception:
            pass
    t0 = time.time()
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        lines = []
        for page in reader.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                t = line.strip()
                if t:
                    lines.append(t)
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return (name, len(lines), time.time() - t0, "ok")
    except Exception as exc:  # noqa: BLE001
        return (name, 0, time.time() - t0, f"FAIL {exc}")


def main() -> int:
    files = sorted(BOOKS.glob("*.pdf"), key=lambda f: (subject_rank(f.name), f.name.lower()))
    if not files:
        print(f"no PDFs in {BOOKS}", flush=True)
        return 1

    workers = min(6, max(1, (os.cpu_count() or 2) - 1), len(files))
    print(f"{len(files)} PDFs, {workers} workers", flush=True)

    manifest = {}
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(extract_one, str(f)): f for f in files}
        for fut in as_completed(futures):
            name, n_lines, secs, status = fut.result()
            done += 1
            print(f"[{done}/{len(files)}] {name}: {status} ({n_lines} lines, {secs:.1f}s)", flush=True)
            if status == "ok" or status == "cached":
                manifest[name] = {"source": name + ".pdf", "lines": n_lines}
            (INDEX / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
            )
    print(f"done: {len(manifest)}/{len(files)} books", flush=True)
    return 0 if len(manifest) == len(files) else 2


if __name__ == "__main__":
    sys.exit(main())
