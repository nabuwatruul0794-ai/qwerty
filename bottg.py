#!/usr/bin/env python3
"""Telegram bot: plan-koncepty urokov. Python port of bottg/Program.cs."""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pypdf
from pypdf import PdfReader


# ---------- Режим: прочитать одну книгу в отдельном процессе ----------
def read_book_one(file: str) -> Optional[Dict]:
    try:
        reader = PdfReader(file)
        lines: List[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            for l in text.split("\n"):
                t = l.strip()
                if t:
                    lines.append(t)
        name = Path(file).stem
        print(f"Прочитана книга: {Path(file).name} ({len(lines)} строк)", file=sys.stderr)
        return {"name": name, "lines": lines}
    except Exception as ex:
        print(f"Не удалось прочитать {Path(file).name}: {ex}", file=sys.stderr)
        return None


# ---------- Вспомогательные ----------
def find_env() -> str:
    d = Path(sys.argv[0]).resolve().parent
    while d:
        p = d / ".env"
        if p.exists():
            return str(p)
        d = d.parent
    raise FileNotFoundError("Файл .env не найден")


def find_books_dir() -> Optional[str]:
    d = Path(sys.argv[0]).resolve().parent
    while d:
        p = d / "books"
        if p.is_dir():
            return str(p)
        d = d.parent
    return None


# ---------- Загрузка книг ----------
def load_books() -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    d = find_books_dir()
    if not d:
        print("Папка books не найдена.")
        return result

    files = sorted(Path(d).glob("*.pdf"), key=lambda f: f.name.lower())
    files.sort(key=lambda f: subject_rank(f.name))

    for f in files:
        try:
            psi = subprocess.Popen(
                [sys.executable, __file__, "--read-book", str(f)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(Path(__file__).resolve().parent),
            )
            out, err = psi.communicate()
            if err.strip():
                print(err.strip(), file=sys.stderr)
            data = json.loads(out.strip())
            if data is None:
                continue
            name = data["name"]
            lines = [s for s in data["lines"] if s]
            result[name] = lines
            print(f"Загружена книга: {f.name} ({len(lines)} строк)")
        except Exception as ex:
            print(f"Не удалось прочитать {f.name}: {ex}")
    return result


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


# ---------- Точка входа ----------
if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--read-book":
        r = read_book_one(sys.argv[2])
        if r is None:
            print("null")
        else:
            print(json.dumps(r, ensure_ascii=False))
        sys.exit(0)

    books = load_books()
    print(f"Книг загружено: {len(books)}")