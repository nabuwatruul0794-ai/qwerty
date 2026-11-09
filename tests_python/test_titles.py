"""Регрессионные тесты на названия параграфов.

Пользовательский баг: в содержании выводилось
    "§ 1. Современные компьютерные устройства 1.1. Различные виды ... — 1.2."
Нужно только "§ N. Название параграфа" - без подпунктов, выносок и номеров страниц.
"""
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ["BOTTG_SKIP_STARTUP"] = "1"
sys.path.insert(0, str(ROOT))

import bottg  # noqa: E402


BOOK_NAMES = [
    "algebra-10.pdf", "algebra-11.pdf", "algebra-7.pdf", "algebra-8.pdf", "algebra-9.pdf",
    "fizika-10.pdf", "fizika-11.pdf", "fizika-7.pdf", "fizika-8.pdf", "fizika-9.pdf",
    "geometriya-10.pdf", "geometriya-11.pdf", "geometriya-7.pdf", "geometriya-8.pdf",
    "geometriya-9.pdf", "informatika-10.pdf", "informatika-11.pdf", "informatika-6.pdf",
    "informatika-7.pdf", "informatika-8.pdf", "informatika-9.pdf", "matem-6.pdf",
]

# Подпункт вида "1.1." / "17.2." внутри названия - недопустим.
SUBSEC_RE = re.compile(r"\d{1,2}\s*\.\s*\d{1,2}\s*\.")
# Выноска содержания "..........." - недопустима.
DOT_LEADER_RE = re.compile(r"(?:\.\s*){3,}")
# Номер страницы в конце названия (2+ цифр) - недопустим.
# Одна цифра может быть частью формулы ("k != 0", "y = x3"), её не режем.
TRAILING_PAGE_RE = re.compile(r"\d{2,}\s*$")


@pytest.fixture(scope="module", autouse=True)
def loaded():
    bottg.warmup_books()
    return bottg


# ---------- Юнит-тесты резателя названия ----------
@pytest.mark.parametrize("raw,expected", [
    ("Современные компьютерные устройства 1.1. Различные виды компьютерных устройств",
     "Современные компьютерные устройства"),
    ("Операционная система 2.1. Основные виды операционных систем ..................... — 2.2. Элементы",
     "Операционная система"),
    ("Понятие векторной графики ................................ 14",
     "Понятие векторной графики"),
    ("Логическая операция НЕ 6.2. Логическая операция НЕ",
     "Логическая операция НЕ"),
    ("Алгоритмы и исполнители",
     "Алгоритмы и исполнители"),
])
def test_cut_title_strips_subsection_and_leaders(raw, expected):
    assert bottg._cut_title(raw) == expected


def test_cut_title_keeps_numbered_title_body():
    # Числа внутри названия ("Векторные 3D-модели") не должны резаться.
    assert "3D" in bottg._cut_title("Векторные 3D-модели 10.1. Виды моделей")


def test_clean_title_uses_cut_title():
    assert bottg._clean_title("Название 1.1. Подпункт .......... 12") == "Название"


# ---------- Интеграционные тесты по всем книгам ----------
@pytest.mark.parametrize("name", BOOK_NAMES)
def test_paragraph_titles_have_no_subsection(name):
    for p in bottg.book_paragraphs(name):
        title = str(p.get("title") or "")
        assert not SUBSEC_RE.search(title), f"{name}: подпункт в названии {title!r}"
        assert not DOT_LEADER_RE.search(title), f"{name}: выноска в названии {title!r}"


@pytest.mark.parametrize("name", BOOK_NAMES)
def test_paragraph_titles_have_no_trailing_page(name):
    for p in bottg.book_paragraphs(name):
        title = str(p.get("title") or "").strip()
        if not title:
            continue
        assert not TRAILING_PAGE_RE.search(title), f"{name}: номер страницы в конце {title!r}"


@pytest.mark.parametrize("name", BOOK_NAMES)
def test_toc_text_is_clean(name):
    text = bottg.book_toc_text(name)
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("§"):
            continue
        assert not SUBSEC_RE.search(s), f"{name}: {s!r}"
        assert not DOT_LEADER_RE.search(s), f"{name}: {s!r}"
        assert not TRAILING_PAGE_RE.search(s), f"{name}: {s!r}"




def test_split_words_are_glued_by_book_vocabulary():
    """Слова, разорванные пробелом в PDF, склеиваются по словарю книги."""
    counts = {"изменения": 200, "напряжение": 100, "суммы": 60}
    pairs = {("из", "менения"): 1, ("и", "меры"): 40}
    assert bottg._fix_split_by_dict("Способы из менения энергии", counts, pairs, set()) \
        == "Способы изменения энергии"
    assert bottg._fix_split_by_dict("Сила и меры тока", counts, pairs, set()) \
        == "Сила и меры тока"


def test_book_vocab_counts_words_and_pairs():
    lines = ["Способы изменения энергии", "Способы изменения тела"]
    counts, pairs, frag = bottg._book_vocab(lines)
    assert counts["изменения"] == 2
    assert counts["энергии"] == 1
    assert pairs[("способы", "изменения")] == 2


def test_glued_words_are_split_again():
    """Слипшиеся слова разделяются: "пересече" + "ниечисловых"."""
    counts = {"пересечение": 22, "числовых": 30, "пересече": 1}
    assert bottg._split_glued("пересече ниечисловых промежутков", counts) \
        == "пересечение числовых промежутков"

def test_informatika7_titles_are_exact():
    """Конкретный пример из бага пользователя."""
    paras = {int(p["num"]): str(p.get("title") or "") for p in bottg.book_paragraphs("informatika-7.pdf")}
    assert paras[1] == "Современные компьютерные устройства"
    assert paras[3] == "Локальная компьютерная сеть"
    assert paras[9] == "Множества. Операции над множествами"
    assert paras[10] == ("Использование логических операций для построения "
                         "поисковых запросов в Интернете")


def test_rare_continuation_is_glued():
    """Осколок в конце слова склеивается, даже если слово редкое.

    "то" + "нких" -> "тонких": вторая часть отдельно почти не встречается.
    """
    counts = {"тонких": 1, "то": 305, "нких": 0}
    pairs = {("то", "нких"): 0}
    assert bottg._fix_split_by_dict("в то нких ли нзах", counts, pairs, set())         == "в тонких ли нзах"


def test_three_pieces_are_glued_together():
    """Цепочка из трёх кусков: "Ди" + "на" + "мометр" -> "Динамометр"."""
    counts = {"динамометр": 11, "дина": 1, "мометр": 3, "на": 599}
    pairs = {("на", "мометр"): 0}
    assert bottg._fix_split_by_dict("Ди на мометр", counts, pairs, set())         == "Динамометр"


def test_glued_token_is_split_into_two_words():
    """Склеенные без пробела слова разделяются: "видчисла" -> "вид числа"."""
    counts = {"вид": 23, "числа": 214}
    assert bottg._split_glued_token("Стандартный видчисла", counts)         == "Стандартный вид числа"


def test_ambiguous_glued_token_is_kept():
    """Если вариантов разбиения несколько - слово не трогаем."""
    counts = {"вес": 54, "тела": 184, "ве": 3, "стела": 2}
    assert bottg._split_glued_token("Вестела", counts) == "Вестела"


def test_titles_have_no_broken_words():
    """В названиях всех книг нет разорванных или слипшихся слов."""
    for name in BOOK_NAMES:
        lines = bottg.get_book(name)
        counts, pairs, frag = bottg._book_vocab(lines)
        for p in bottg.book_paragraphs(name):
            title = str(p.get("title") or "")
            words = bottg._WORD_RE.findall(title.lower())
            for i in range(len(words) - 1):
                a, b = words[i], words[i + 1]
                merged = a + b
                assert merged not in counts or merged in frag or pairs.get((a, b), 0) * 3 > counts.get(merged, 0), \
                    f"{name} §{p['num']}: {title!r}"
            for w in words:
                if w not in counts:
                    ext = [x for x in counts if x.startswith(w) and len(x) > len(w)]
                    assert not ext or len(w) < 3, f"{name} §{p['num']}: {title!r}"
