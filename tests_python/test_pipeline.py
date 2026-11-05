"""Юнит-тесты по шагам конвейера бота.

Шаги: индекс -> разбор книги -> поиск параграфа -> клавиатуры -> формулы ->
документ -> telegram-слой -> обработчики.
"""
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ["BOTTG_SKIP_STARTUP"] = "1"
sys.path.insert(0, str(ROOT))

import bottg  # noqa: E402
import latex_png  # noqa: E402


# Список книг - статический: параметры pytest собираются при импорте,
# когда индекс ещё не загружен.
BOOK_NAMES = [
    "algebra-10.pdf", "algebra-11.pdf", "algebra-7.pdf", "algebra-8.pdf", "algebra-9.pdf",
    "fizika-10.pdf", "fizika-11.pdf", "fizika-7.pdf", "fizika-8.pdf", "fizika-9.pdf",
    "geometriya-10.pdf", "geometriya-11.pdf", "geometriya-7.pdf", "geometriya-8.pdf",
    "geometriya-9.pdf", "informatika-10.pdf", "informatika-11.pdf", "informatika-6.pdf",
    "informatika-7.pdf", "informatika-8.pdf", "informatika-9.pdf", "matem-6.pdf",
]


@pytest.fixture(scope="module", autouse=True)
def loaded():
    bottg.warmup_books()
    return bottg


# ---------- Шаг 1. Загрузка индекса ----------
def test_index_loaded():
    assert len(bottg.BOOKS_INDEX) == 22
    assert len(bottg.BOOKS_META) == 22
    assert all(p.exists() for p in bottg.BOOKS_INDEX.values())
    assert set(bottg.BOOKS_INDEX) == set(BOOK_NAMES)


def test_index_manifest_line_counts():
    for name, meta in bottg.BOOKS_META.items():
        assert isinstance(meta.get("lines"), int), name
        assert meta["lines"] > 0


def test_book_order_is_stable_and_complete():
    order = bottg.book_order()
    assert len(order) == 22
    assert order == bottg.book_order()
    assert bottg.book_by_index(0) == order[0]
    assert bottg.book_by_index(-1) is None
    assert bottg.book_by_index(999) is None


# ---------- Шаг 2. Разбор структуры книги ----------
@pytest.mark.parametrize("name", BOOK_NAMES)
def test_paragraphs_are_numbered_without_gaps(name):
    nums = [int(p["num"]) for p in bottg.book_paragraphs(name)]
    assert nums == list(range(1, len(nums) + 1)), name
    assert len(nums) >= 10, name


@pytest.mark.parametrize("name", BOOK_NAMES)
def test_paragraph_text_is_not_empty(name):
    for p in bottg.book_paragraphs(name):
        assert str(p["text"]).strip(), (name, p["num"])
        assert int(p["end"]) > int(p["start"])


def test_paragraph_ranges_do_not_overlap():
    for name in bottg.BOOKS_INDEX:
        paras = sorted(bottg.book_paragraphs(name), key=lambda p: int(p["start"]))
        for a, b in zip(paras, paras[1:]):
            assert int(a["end"]) <= int(b["start"]) + 1, (name, a["num"], b["num"])


def test_titles_are_recovered():
    paras = {int(p["num"]): str(p["title"]) for p in bottg.book_paragraphs("fizika-9.pdf")}
    assert len(paras) == 36
    assert "движение" in paras[1].lower()
    assert paras[7]  # § 7 тоже с названием


def test_paragraph_text_is_cached():
    first = bottg.book_paragraphs("fizika-9.pdf")
    second = bottg.book_paragraphs("fizika-9.pdf")
    assert first is second


# ---------- Шаг 3. Поиск параграфа ----------
def test_find_by_number_with_subject_and_grade():
    text, book = bottg.find_paragraph("10 класс, физика, § 25")
    assert book == "fizika-10.pdf"
    assert text and len(text) > 500


def test_find_by_number_only():
    text, book = bottg.find_paragraph("§ 7", only_book="fizika-9.pdf")
    assert book == "fizika-9.pdf"
    assert "§ 7" in text[:40]


def test_find_by_topic():
    text, book = bottg.find_paragraph("сила трения покоя")
    assert book is not None
    assert text and len(text) > 200


def test_find_returns_nothing_for_garbage():
    text, book = bottg.find_paragraph("ыыыы")
    assert text is None and book is None


def test_find_only_book_restricts_search():
    text, book = bottg.find_paragraph("§ 5", only_book="algebra-7.pdf")
    assert book == "algebra-7.pdf"


def test_paragraph_text_helper():
    txt = bottg.paragraph_text("fizika-9.pdf", 7)
    assert txt.startswith("§ 7")
    assert bottg.paragraph_text("fizika-9.pdf", 999) == ""


# ---------- Шаг 4. Клавиатуры ----------
def test_books_keyboard_structure():
    kb = bottg.books_keyboard()
    rows = kb["inline_keyboard"]
    assert len(rows) == 9                       # 22 кнопки по 3 (8 рядов) + ряд отмены
    assert sum(len(r) for r in rows) == 23
    for row in rows[:-1]:
        for b in row:
            assert b["callback_data"].startswith("bk:")
            assert b["callback_data"][3:].isdigit()
    assert rows[-1][0]["callback_data"] == "bkx"


def test_paragraph_keyboard_structure():
    kb = bottg.paragraph_keyboard("fizika-9.pdf", 0)
    rows = kb["inline_keyboard"]
    buttons = [b for row in rows[:-1] for b in row]
    assert len(buttons) == 12                    # PARAS_PER_PAGE
    for b in buttons:
        assert b["callback_data"].startswith("p:")
        assert b["text"].startswith("§ ")
    assert "Отмена" in rows[-1][-1]["text"]


def test_paragraph_keyboard_pagination():
    kb = bottg.paragraph_keyboard("fizika-9.pdf", 1)
    nums = [b["callback_data"].split(":")[2] for row in kb["inline_keyboard"][:-1] for b in row]
    assert nums[0] == "13"                      # вторая страница продолжает список
    assert any(b["callback_data"].startswith("pg:") for b in kb["inline_keyboard"][-1])


def test_paragraph_keyboard_out_of_range_page():
    kb = bottg.paragraph_keyboard("fizika-9.pdf", 999)
    assert kb is not None


# ---------- Шаг 5. Формулы ----------
def test_formula_renders_png(tmp_path):
    p = tmp_path / "f.png"
    w, h = latex_png.render_formula(r"v_x = \frac{s}{t}", p)
    assert w > 0 and h > 0
    assert p.stat().st_size > 500


def test_formula_with_cyrillic_subscript(tmp_path):
    p = tmp_path / "f.png"
    latex_png.render_formula(r"F_{тр.пок} \le \mu_{пок} N", p)
    assert p.stat().st_size > 500


def test_unknown_command_is_repaired(tmp_path):
    p = tmp_path / "f.png"
    latex_png.render_formula(r"x^2 \foo{y} = 1", p)
    assert p.stat().st_size > 500


def test_broken_formula_falls_back_to_text(tmp_path):
    p = tmp_path / "f.png"
    latex_png.render_formula(r"\frac{1}{", p)
    assert p.exists()


# ---------- Шаг 6. Документ ----------
def test_docx_contains_formula_images(tmp_path):
    plan = ("Тема: $v = \\frac{s}{t}$\n- ещё $x^2$\n")
    out = tmp_path / "d.docx"
    bottg.make_docx(plan, str(out))
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    assert len(media) == 2
    assert "$" not in xml


def test_docx_keeps_latex_as_text_for_kimi(tmp_path):
    plan = "Тема: $v = \\frac{s}{t}$\n"
    out = tmp_path / "d.docx"
    bottg.make_docx(plan, str(out), formulas_as_text=True)
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    assert not media
    assert "\\frac" in xml


def test_docx_skips_empty_lines(tmp_path):
    out = tmp_path / "d.docx"
    bottg.make_docx("Первая\n\n\n\nВторая\n", str(out))
    doc = __import__("docx").Document(str(out))
    assert [p.text for p in doc.paragraphs] == ["Первая", "Вторая"]


def test_docx_bullets_and_headings(tmp_path):
    out = tmp_path / "d.docx"
    bottg.make_docx("Тема урока: трение\n- пункт один\n- пункт два\n", str(out))
    doc = __import__("docx").Document(str(out))
    texts = [p.text for p in doc.paragraphs]
    assert "• пункт один" in texts
    assert doc.paragraphs[0].runs[0].bold is True


# ---------- Шаг 7. Telegram-слой ----------
def test_send_returns_message_id(monkeypatch):
    monkeypatch.setattr(bottg, "tg_post",
                        lambda m, d, f=None: {"ok": True, "result": {"message_id": 42}})
    assert bottg.send(1, "привет") == 42


def test_send_splits_long_text(monkeypatch):
    calls = []
    monkeypatch.setattr(bottg, "tg_post",
                        lambda m, d, f=None: calls.append(d) or {"ok": True, "result": {"message_id": 1}})
    bottg.send(1, "x" * 9000)
    assert len(calls) == 3
    assert all(len(c["text"]) <= 4000 for c in calls)


def test_send_keeps_keyboard_on_last_chunk(monkeypatch):
    calls = []
    monkeypatch.setattr(bottg, "tg_post",
                        lambda m, d, f=None: calls.append(d) or {"ok": True, "result": {"message_id": 1}})
    bottg.send(1, "x" * 9000, reply_markup={"inline_keyboard": [[{"text": "x", "callback_data": "y"}]]})
    assert "reply_markup" not in calls[0]
    assert "reply_markup" in calls[-1]


def test_edit_message(monkeypatch):
    seen = {}
    monkeypatch.setattr(bottg, "tg_post",
                        lambda m, d, f=None: seen.update(d) or {"ok": True})
    assert bottg.edit_message(1, 5, "новый текст") is True
    assert seen["message_id"] == 5
    assert seen["text"] == "новый текст"


def test_edit_message_without_id():
    assert bottg.edit_message(1, None, "текст") is False


def test_answer_callback(monkeypatch):
    seen = {}
    monkeypatch.setattr(bottg, "tg_post",
                        lambda m, d, f=None: seen.update(d) or {"ok": True})
    assert bottg.answer_cb("cb-1", "готово") is True
    assert seen["callback_query_id"] == "cb-1"
    assert seen["text"] == "готово"


def test_tg_post_returns_parsed_json(monkeypatch):
    import urllib.request

    class FakeResp:
        def read(self):
            return b'{"ok": true, "result": {"message_id": 7}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    out = bottg.tg_post("getMe", {})
    assert out["ok"] is True
    assert out["result"]["message_id"] == 7


def test_tg_post_http_error_returns_none(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert bottg.tg_post("getMe", {}) is None


# ---------- Шаг 8. Обработчики ----------
def test_foreign_user_is_ignored(monkeypatch):
    sent = []
    monkeypatch.setattr(bottg, "send", lambda c, t, r=None: sent.append((c, t)) or 1)
    monkeypatch.setattr(bottg, "run_plan", lambda *a, **k: sent.append("PLAN"))
    bottg.handle_update({"message": {"from": {"id": 1}, "chat": {"id": 1}, "text": "/books"}})
    assert not sent


def test_command_books_sends_list(monkeypatch):
    sent = []
    monkeypatch.setattr(bottg, "send",
                        lambda c, t, reply_markup=None: sent.append(t) or 1)
    bottg.handle_update({"message": {"from": {"id": 1177127744}, "chat": {"id": 1}, "text": "/books"}})
    assert sent and "Доступные книги" in sent[0]


def test_command_presentation_without_plan(monkeypatch, tmp_path):
    import state_store
    monkeypatch.setattr(state_store, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(state_store, "OLD_JSON", tmp_path / "s.json")
    monkeypatch.setattr(state_store, "_initialized", False)
    state_store.init()
    monkeypatch.setattr(bottg, "state_store", state_store)
    sent = []
    monkeypatch.setattr(bottg, "send", lambda c, t, r=None: sent.append(t) or 1)
    bottg.handle_update({"message": {"from": {"id": 1177127744}, "chat": {"id": 9}, "text": "/през"}})
    assert sent and "Сначала сделайте план" in sent[0]


def test_callback_selects_book(monkeypatch):
    edits = []
    monkeypatch.setattr(bottg, "answer_cb", lambda i, t="": True)
    monkeypatch.setattr(bottg, "edit_message",
                        lambda c, m, t, reply_markup=None: edits.append((t, reply_markup)) or True)
    idx = bottg.book_order().index("fizika-9.pdf")
    bottg.handle_callback_query({
        "id": "c1", "from": {"id": 1177127744}, "data": f"bk:{idx}",
        "message": {"message_id": 5, "chat": {"id": 1}}})
    assert edits and "Физика 9" in edits[0][0]
    assert edits[0][1] is not None


def test_callback_unknown_book_is_handled(monkeypatch):
    monkeypatch.setattr(bottg, "answer_cb", lambda i, t="": True)
    bottg.handle_callback_query({
        "id": "c2", "from": {"id": 1177127744}, "data": "bk:999",
        "message": {"message_id": 5, "chat": {"id": 1}}})
