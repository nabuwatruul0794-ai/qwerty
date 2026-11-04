"""Хранилище состояния бота (SQLite).

Состояние должно переживать несколько процессов gunicorn, поэтому обычный
JSON-файл не годится: два воркера перетирают друг друга при записи
(прочитал весь файл - поправил - записал весь файл).

В SQLite запись идёт одной транзакцией, поэтому гонки нет, а ещё появляется
место под историю планов и статистику.

Таблица:
    state(chat_id INTEGER PRIMARY KEY, book TEXT, plan TEXT, at INTEGER, updated INTEGER)
      book   - выбранная книга (действует на один запрос)
      plan   - текст последнего плана-конспекта (для промта под презентацию)
      at     - когда сохранён план
      updated - когда запись менялась последний раз (по этому чистим старое)
"""
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("bottg.state")

DB_PATH = Path(tempfile.gettempdir()) / "bottg.db"
OLD_JSON = Path(tempfile.gettempdir()) / "bottg_state.json"
TTL = 2 * 60 * 60          # запись считается протухшей через 2 часа
CLEAN_EVERY = 60          # секунд между чистками

_init_lock = threading.Lock()
_initialized = False
_last_clean = 0.0


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def _migrate_from_json() -> None:
    """Переносит старое состояние из JSON-файла в базу (один раз)."""
    if not OLD_JSON.exists():
        return
    try:
        data = json.loads(OLD_JSON.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        conn = _connect()
        try:
            for key, value in data.items():
                try:
                    chat_id = int(key)
                except (TypeError, ValueError):
                    continue
                book = value.get("book") if isinstance(value, dict) else str(value)
                plan = value.get("plan") if isinstance(value, dict) else None
                at = value.get("at") if isinstance(value, dict) else None
                if not (book or plan):
                    continue
                conn.execute(
                    "INSERT INTO state (chat_id, book, plan, at, updated) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET book=excluded.book, plan=excluded.plan, "
                    "at=excluded.at, updated=excluded.updated",
                    (chat_id, book, plan, at, int(time.time())))
            log.info("Состояние перенесено из %s", OLD_JSON.name)
        finally:
            conn.close()
        try:
            OLD_JSON.rename(OLD_JSON.with_suffix(".json.migrated"))
        except Exception:
            pass
    except Exception as ex:
        log.warning("Не удалось перенести состояние из JSON: %s", ex)


def init() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        try:
            conn = _connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS state ("
                    "chat_id INTEGER PRIMARY KEY, book TEXT, plan TEXT, "
                    "at INTEGER, updated INTEGER)")
            finally:
                conn.close()
            _migrate_from_json()
            _initialized = True
        except Exception as ex:
            log.warning("Не удалось открыть базу состояния: %s", ex)


def cleanup(ttl: int = TTL, force: bool = False) -> None:
    """Удаляет записи старше ttl секунд.

    force=True - чистить всегда (иначе действует троттлинг CLEAN_EVERY).
    """
    global _last_clean
    now = time.time()
    if not force and now - _last_clean < CLEAN_EVERY:
        return
    _last_clean = now
    try:
        conn = _connect()
        try:
            conn.execute("DELETE FROM state WHERE updated IS NOT NULL AND updated < ?", (int(now - ttl),))
        finally:
            conn.close()
    except Exception as ex:
        log.warning("Не удалось почистить состояние: %s", ex)


def get(chat_id: int) -> Dict[str, object]:
    """Запись состояния чата: book, plan, at. Пусто, если ничего нет."""
    init()
    cleanup()
    try:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT book, plan, at FROM state WHERE chat_id = ?", (int(chat_id),)).fetchone()
        finally:
            conn.close()
        if not row:
            return {}
        return {key: row[key] for key in ("book", "plan", "at") if row[key] is not None}
    except Exception as ex:
        log.warning("Не удалось прочитать состояние: %s", ex)
        return {}


def set_fields(chat_id: int, **fields) -> None:
    """Обновляет поля записи состояния; пустое значение удаляет поле."""
    init()
    cleanup()
    allowed = ("book", "plan", "at")
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT book, plan, at FROM state WHERE chat_id = ?", (int(chat_id),)).fetchone()
        rec: Dict[str, Optional[object]] = {"book": None, "plan": None, "at": None}
        if row:
            for key in allowed:
                rec[key] = row[key]
        for key, value in fields.items():
            if key not in allowed:
                continue
            rec[key] = None if (value is None or value == "") else value
        if any(v is not None for v in rec.values()):
            conn.execute(
                "INSERT INTO state (chat_id, book, plan, at, updated) VALUES (?,?,?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET book=excluded.book, plan=excluded.plan, "
                "at=excluded.at, updated=excluded.updated",
                (int(chat_id), rec["book"], rec["plan"], rec["at"], int(time.time())))
        else:
            conn.execute("DELETE FROM state WHERE chat_id = ?", (int(chat_id),))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def stats() -> Dict[str, int]:
    """Сколько записей в базе (для диагностики)."""
    init()
    conn = _connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n, COUNT(plan) AS p FROM state").fetchone()
        return {"всего": int(row["n"]), "с планом": int(row["p"])}
    finally:
        conn.close()
