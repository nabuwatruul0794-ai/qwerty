import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["BOTTG_SKIP_STARTUP"] = "1"      # не ставить настоящий вебхук при импорте
sys.path.insert(0, str(ROOT))

import bottg  # noqa: E402


def _client(monkeypatch):
    monkeypatch.setattr(bottg, "handle_update", lambda update: None)
    return bottg.app.test_client()


def test_webhook_without_secret_is_rejected(monkeypatch):
    monkeypatch.setattr(bottg, "WEBHOOK_SECRET", "test-secret")
    resp = _client(monkeypatch).post("/webhook", json={"message": {"text": "/books"}})
    assert resp.status_code == 403


def test_webhook_with_wrong_secret_is_rejected(monkeypatch):
    monkeypatch.setattr(bottg, "WEBHOOK_SECRET", "test-secret")
    resp = _client(monkeypatch).post(
        "/webhook", json={"message": {"text": "/books"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert resp.status_code == 403


def test_webhook_with_correct_secret_is_accepted(monkeypatch):
    monkeypatch.setattr(bottg, "WEBHOOK_SECRET", "test-secret")
    resp = _client(monkeypatch).post(
        "/webhook", json={"message": {"text": "/books"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_webhook_open_when_no_secret_configured(monkeypatch):
    monkeypatch.setattr(bottg, "WEBHOOK_SECRET", "")
    resp = _client(monkeypatch).post("/webhook", json={"message": {"text": "/books"}})
    assert resp.status_code == 200


def test_health_and_root_are_open(monkeypatch):
    monkeypatch.setattr(bottg, "WEBHOOK_SECRET", "test-secret")
    client = _client(monkeypatch)
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_model_choice_is_shared_between_workers(monkeypatch, tmp_path):
    """Выбор модели сохраняется в базе, а не в памяти процесса."""
    import state_store

    monkeypatch.setattr(state_store, "DB_PATH", tmp_path / "bottg.db")
    monkeypatch.setattr(state_store, "OLD_JSON", tmp_path / "bottg_state.json")
    monkeypatch.setattr(state_store, "_initialized", False)
    state_store.init()
    monkeypatch.setattr(bottg, "state_store", state_store)

    state_store.set_setting("model", "claude-sonnet-5-5")
    monkeypatch.setattr(bottg, "MODEL", "claude-haiku-5-5")
    bottg.refresh_model()
    assert bottg.MODEL == "claude-sonnet-5-5"
