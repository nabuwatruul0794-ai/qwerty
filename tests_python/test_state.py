import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import state_store  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "DB_PATH", tmp_path / "bottg.db")
    monkeypatch.setattr(state_store, "OLD_JSON", tmp_path / "bottg_state.json")
    monkeypatch.setattr(state_store, "_initialized", False)
    monkeypatch.setattr(state_store, "_last_clean", 0.0)
    state_store.init()
    return state_store


def test_set_and_get(store):
    store.set_fields(555, book="fizika-9.pdf")
    assert store.get(555) == {"book": "fizika-9.pdf"}


def test_plan_and_book_together(store):
    store.set_fields(555, book="algebra-10.pdf", plan="текст плана", at=123)
    rec = store.get(555)
    assert rec["book"] == "algebra-10.pdf"
    assert rec["plan"] == "текст плана"
    assert rec["at"] == 123


def test_clearing_one_field_keeps_others(store):
    store.set_fields(555, book="fizika-9.pdf", plan="текст плана", at=123)
    store.set_fields(555, book=None)
    rec = store.get(555)
    assert "book" not in rec
    assert rec["plan"] == "текст плана"


def test_clearing_all_removes_record(store):
    store.set_fields(555, book="fizika-9.pdf", plan="текст", at=1)
    store.set_fields(555, book=None, plan=None, at=None)
    assert store.get(555) == {}


def test_chats_do_not_mix(store):
    store.set_fields(1, book="a.pdf")
    store.set_fields(2, book="b.pdf")
    assert store.get(1)["book"] == "a.pdf"
    assert store.get(2)["book"] == "b.pdf"


def test_expired_records_are_cleaned(store, monkeypatch):
    store.set_fields(555, book="fizika-9.pdf")
    conn = store._connect()
    try:
        conn.execute("UPDATE state SET updated = ? WHERE chat_id = ?",
                     (int(time.time()) - 10 * 3600, 555))
    finally:
        conn.close()
    store.cleanup(ttl=2 * 3600, force=True)
    assert store.get(555) == {}


def test_migration_from_json(store):
    store.OLD_JSON.write_text(json.dumps({
        "777": {"book": "fizika-7.pdf", "plan": "старый план", "at": 42},
        "888": {"book": "algebra-9.pdf"},
    }, ensure_ascii=False), encoding="utf-8")
    store._migrate_from_json()
    assert store.get(777)["book"] == "fizika-7.pdf"
    assert store.get(777)["plan"] == "старый план"
    assert store.get(888)["book"] == "algebra-9.pdf"


def test_no_lost_updates_between_processes(tmp_path):
    """Два процесса пишут разные чаты одновременно - записи не должны теряться."""
    db = tmp_path / "multi.db"
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "import state_store, time\n"
        "state_store.DB_PATH = __import__('pathlib').Path(%r)\n"
        "state_store._initialized = False\n"
        "state_store.init()\n"
        "for i in range(25):\n"
        "    state_store.set_fields(int(sys.argv[1]), book='book-%%d.pdf' %% i)\n"
    ) % (str(ROOT), str(db))

    procs = [subprocess.Popen([sys.executable, "-c", script, str(chat)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for chat in range(1, 9)]
    for p in procs:
        p.wait()

    conn = state_store.sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT chat_id, book FROM state").fetchall()
    finally:
        conn.close()
    assert len(rows) == 8, f"часть записей потерялась: {rows}"
    for chat_id, book in rows:
        assert book is not None
