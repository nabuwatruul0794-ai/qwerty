#!/usr/bin/env python3
"""Python tests for bottg.py — mirror of tests_csharp/BookLoadTests.cs."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = ROOT / "books"
BOT_SCRIPT = ROOT / "bottg.py"


# ---------- helpers ----------
# Разбор PDF - самая медленная часть набора: без кэша один и тот же файл
# читается несколько раз (содержимое, прямое/подпроцессное сравнение,
# замер памяти). Кэш ускоряет прогон, ничего не меняя по смыслу.
from functools import lru_cache


@lru_cache(maxsize=None)
def read_book_direct(file: str) -> tuple[str, ...]:
    reader = PdfReader(file)
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for l in text.split("\n"):
            t = l.strip()
            if t:
                lines.append(t)
    return tuple(lines)


@lru_cache(maxsize=None)
def read_book_subprocess(file: str) -> tuple[str, ...]:
    # Кодировка UTF-8 задаётся явно с обеих сторон: в тексте учебников есть
    # символы (U+2212 «−», U+2032 «′»), которые локальная кодовая страница
    # cp1251 не умеет кодировать, и subprocess падает на print().
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    psi = subprocess.run(
        [sys.executable, str(BOT_SCRIPT), "--read-book", file],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=env,
    )
    if psi.stderr.strip():
        print(psi.stderr.strip())
    data = json.loads(psi.stdout.strip())
    if data is None:
        return ()
    return tuple(s for s in data["lines"] if s)


def get_book_files() -> list[str]:
    if not BOOKS_DIR.is_dir():
        return []
    return sorted(str(p) for p in BOOKS_DIR.glob("*.pdf"))


def sorted_book_files() -> list[str]:
    files = get_book_files()
    files.sort(key=lambda f: subject_rank(Path(f).name))
    return files


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


def name_to_subject(name: str) -> str:
    n = name.lower()
    if "fizika" in n:
        return "fizika"
    if "informatika" in n:
        return "informatika"
    if "algebra" in n:
        return "algebra"
    if "geometriya" in n:
        return "geometriya"
    if "matem" in n:
        return "matem"
    return "other"


# ---------- tests ----------
def test_books_directory_exists():
    assert BOOKS_DIR.is_dir(), f"Books directory not found at {BOOKS_DIR}"


def test_book_count_is_22():
    files = get_book_files()
    assert len(files) == 22


def test_all_files_are_pdf():
    files = get_book_files()
    for f in files:
        assert Path(f).suffix.lower() == ".pdf"


def test_each_book_has_content():
    files = get_book_files()
    for f in files:
        lines = read_book_direct(f)
        assert lines, f"{Path(f).name} has no lines"
        assert len(lines) > 100, f"{Path(f).name} has only {len(lines)} lines"


def test_subprocess_and_direct_read_match():
    files = get_book_files()[:3]
    for f in files:
        direct = read_book_direct(f)
        sub = read_book_subprocess(f)
        assert len(direct) == len(sub), f"{Path(f).name}: direct={len(direct)} sub={len(sub)}"
        assert direct == sub


def test_sorting_groups_by_subject():
    sorted_names = [Path(f).name for f in sorted_book_files()]
    ranks = [subject_rank(n) for n in sorted_names]
    for i in range(1, len(ranks)):
        assert ranks[i - 1] <= ranks[i], (
            f"Sort order violated at index {i}: rank {ranks[i - 1]} before rank {ranks[i]}"
        )


def test_all_subjects_present():
    names = [Path(f).name for f in get_book_files()]
    assert any("fizika" in n for n in names)
    assert any("informatika" in n for n in names)
    assert any("algebra" in n for n in names)
    assert any("geometriya" in n for n in names)
    assert any("matem" in n for n in names)


def test_no_duplicate_books():
    names = [Path(f).stem for f in get_book_files()]
    unique = {n.lower() for n in names}
    assert len(names) == len(unique)


def test_memory_usage_under_control():
    import gc
    files = get_book_files()
    import tracemalloc
    read_book_direct.cache_clear()      # меряем реальное чтение, а не кэш
    tracemalloc.start()
    for f in files:
        read_book_direct(f)
        gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 500 * 1024 * 1024, f"Peak memory {peak / 1024 / 1024:.1f} MB exceeds 500 MB"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))