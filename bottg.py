#!/usr/bin/env python3
"""Telegram bot: plan-koncepty urokov. Python port of bottg/Program.cs."""

import os
import sys
import re
import json
import math
import gzip
import hmac
import tempfile
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import state_store

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("bottg")

from pypdf import PdfReader

# ---------- .env ----------
def load_env_dict(path: Path) -> Dict[str, str]:
    d: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            i = t.find("=")
            if i > 0:
                key = t[:i].strip()
                val = t[i + 1:].strip().strip('"').strip("'")
                d[key] = val
        log.info(f"Загружено {len(d)} переменных из .env: {path}")
    except Exception as e:
        log.warning(f"Не удалось прочитать .env: {e}")
    return d

def find_up(filename: str, start: Path) -> Optional[Path]:
    d = start
    while True:
        p = d / filename
        if p.exists():
            return p
        if d.parent == d:
            return None
        d = d.parent

def find_dir_up(dirname: str, start: Path) -> Optional[Path]:
    d = start
    while True:
        p = d / dirname
        if p.is_dir():
            return p
        if d.parent == d:
            return None
        d = d.parent

# Каталог проекта. Берём от __file__, а не от sys.argv[0]: под gunicorn
# argv[0] — путь к самому gunicorn, и books_index/.env искались бы не там.
BASE_DIR = Path(__file__).resolve().parent
env_path = find_up(".env", BASE_DIR)
env_dict = load_env_dict(env_path) if env_path else {}

def get_env(key: str, default: Optional[str] = None) -> str:
    if key in env_dict and env_dict[key]:
        return env_dict[key]
    v = os.environ.get(key, "")
    if v:
        return v
    if default is not None:
        return default
    raise Exception(f"Отсутствует переменная {key}")

TOKEN = get_env("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(get_env("OWNER_ID"))
ZEN_KEY = get_env("OPENCODE_API_KEY")
MODEL = get_env("OPENCODE_MODEL", "claude-sonnet-5-5")
PUBLIC_URL = get_env("PUBLIC_URL", "").rstrip("/")

ALLOWED_USERS = {OWNER_ID, 1329641372, 1079330932, 1079918562, 5206288199,
                 909156210, 1049364444}

MODELS: Dict[str, str] = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
    "qwen": "qwen3.8-flash",
    "deepseek": "deepseek-v4-pro",
    "deepseek-flash": "deepseek-v4.1-flash",
    "kimi": "kimi-k3",
    "glm": "glm-5.3-flash",
    "gpt-mini": "gpt-5.4-mini",
    "gpt-nano": "gpt-5.4-nano",
    "luna": "gpt-6-luna",
}

API_BASE = f"https://api.telegram.org/bot{TOKEN}/"

# Cloudflare перед inference-эндпоинтом отдаёт 403 (error code 1010) на
# дефолтный User-Agent urllib - поэтому подставляем обычный браузерный.
MODEL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Формулы в тексте плана: $...$ или $$...$$
MATH_SPAN_RE = re.compile(r"\$\$([^$]+)\$\$|\$([^$]+)\$")

# Секрет вебхука: без него любой может слать боту фальшивые обновления
# и тратить ваш ключ модели. Telegram присылает его в заголовке
# X-Telegram-Bot-Api-Secret-Token.
WEBHOOK_SECRET = get_env("BOT_WEBHOOK_SECRET", "").strip()

SYSTEM_PROMPT = r"""
Ты помощник учителя физики, математики и информатики в Беларуси. Ты составляешь подробный план-конспект урока.

Пиши по-русски простым текстом, без символов Markdown (без **, #, таблиц с |).
Заголовки разделов пиши с новой строки, списки через тире (строка начинается с «- »).

Структура плана-конспекта (пиши строго в таком порядке):

ШАПКА - иди сразу с этих строк, без заголовков и без нумерации, каждая строка с новой:
Учебный предмет: ...
Класс: ...
Тема раздела: ... (глава учебника, к которой относится параграф)
Тема урока: ...
Цели урока:
- Образовательные: ...
- Развивающие: ...
- Воспитывающие: ...
Тип урока: ... (например: урок усвоения новых знаний и умений)
Оборудование: ... (одной строкой через запятую: техника, проектор, раздаточный материал, карточки с заданиями)
Литература: ... (полное библиографическое описание учебника: автор, название, класс, издательство, год, объём.
   Если автора, года или объёма в тексте параграфа нет - не пиши «[уточнить по учебнику]», а укажи то,
   что точно известно: название, класс, издательство. Выдумывать нельзя.)
Структура урока:
1. Организационный момент (2 мин).
2. Актуализация знаний (5 мин).
3. Мотивация и постановка проблемы (3 мин).
4. Изучение нового материала (18 мин).
5. Формирование умений и навыков (12 мин).
6. Подведение итогов (2 мин).
7. Домашнее задание (3 мин).
Продолжительность: 45 минут.

Потом идёт ХОД УРОКА - тоже без заголовка, просто с первой пронумерованной строки.
Нумерованные этапы - те же и в том же порядке, что в «Структуре урока», в скобках минуты.
Время этапов в сумме должно давать 45 минут.
   1) Организационный момент (2 мин)
   2) Актуализация знаний (5 мин). Вопросы для фронтальной беседы OBЯЗАТЕЛЬНО должны содержать
      краткие правильные ответы в скобках после каждого вопроса (для самопроверки учителя).
   3) Мотивация и постановка проблемы (3 мин): демонстрация или проблемный вопрос. ТЫ ОБЯЗАН
      привести не просто ответ, а подробное физическое/логическое объяснение этой проблемы,
      чтобы учитель мог озвучить его классу.
   4) Изучение нового материала (18 мин) по подпунктам 4.1, 4.2 и так далее, с минутами,
      с записью в тетрадь, опытами, формулами и определениями.
   5) Формирование умений и навыков (12 мин): сначала утверждения «верно или неверно» с ответами
      (по 3-4 утверждения), потом практическая задача или упражнение. Если приводится практическая
      задача, ТЫ ОБЯЗАН расписать ее ПОЛНОЕ И ПОДРОБНОЕ РЕШЕНИЕ: краткое условие (Дано / Найти),
      перевод величин в СИ, базовые формулы, пошаговые математические вычисления и финальный
      численный ответ. Укажи критерии оценивания работы.
   6) Подведение итогов (2 мин): что изучили, что было непонятно, главные выводы для записи
      в тетрадь; вопросы с ответами в скобках.
   7) Домашнее задание (3 мин): номера параграфов, вопросов или задач. Если учителю предлагается
      задать на дом задачи, ТЫ ОБЯЗАН прямо в конспекте расписать их полное пошаговое решение
      и ответы, чтобы учителю не пришлось считать самостоятельно при проверке.

Как писать ход урока:
- Текст каждого этапа - от лица учителя, сначала его действие, затем действия учеников:
  «Учитель организует учащихся, приветствует их, объявляет тему и цели урока.»,
  «Тема нашего урока: «...». Цель урока: ...»,
  «Учащиеся записывают в тетрадях дату и тему урока.»,
  «Учитель демонстрирует через проектор...», «Учитель предлагает учащимся ознакомиться со справкой...»,
  «Учитель объясняет порядок выполнения заданий и озвучивает критерии их оценивания.»
- ПРОГНОЗНЫЙ ВОПРОС - главный приём живости урока: перед каждым опытом спрашивай заранее
  «Как вы думаете, что произойдёт, если...?», «Что будет, если...?». Учитель записывает
  предположения класса на доске, потом проверяет опытом и делает вывод. Прогноз обязателен
  хотя бы перед одним опытом урока.
- Чередуй виды работы: опыт - обсуждение - запись в тетрадь. Не держи один вид деятельности
  дольше 6 минут подряд.
- Вопросы пиши нумерованным перечнем 1., 2., 3. - и сразу после вопроса ответ в скобках.
- Изредка давай задание на доску: «Запишите на доске примеры ...».
- В конце хода урока - короткая похвала учителя классу («Молодцы, ребята! Вы сегодня очень хорошо поработали!»).
- Не пиши служебные надписи вроде «Начало», «Содержание», «Страница N из 80», «Назад», «На весь экран»,
  «Закрыть» - это мусор из электронного учебника, в конспект его тащить не надо.

Правила:
- Все формулы, величины и обозначения записывай в LaTeX внутри $...$:
  индексы через подчёркивание ($v_x$, $a_0$, $v_{1x}$), дроби через \frac с фигурными скобками
  ($\frac{3}{4}$, $\frac{\Delta x}{\Delta t}$), степени через ^ ($x^2$, $v^{2}$), корни через \sqrt
  ($\sqrt{2}$, $\sqrt[3]{5}$), греческие буквы как $\alpha$, $\beta$, $\pi$, десятичная запятая
  внутри фигурных скобок ($2{,}5$).
  Если в тексте учебника формула слиплась (например «x x− + − =43 12 3» или «v1x = v1 = 2,0 мс»),
  восстанови её по смыслу и запиши в LaTeX. Формулу в обычном виде не оставляй никогда.
- Класс, предмет, учебник и номер параграфа всегда указаны в запросе (строка "Данные об уроке"). Бери их оттуда точно и подставляй в шапку плана. НИКОГДА не пиши вместо класса или параграфа «[уточнить по учебнику]» - эти данные уже есть.
- К абсолютно любой sгенерированной задаче, проблемному вопросу или практическому заданию на любом этапе урока ТЫ ОБЯЗАН предоставить ПОЛНОЕ ХОД РЕШЕНИЯ с формулами, промежуточными расчетами и финальным ответом. Не ограничивайся только кратким ответом.
- Если в запросе приведён текст параграфа учебника, строй план строго на нём: используй его определения, примеры, опыты, контрольные вопросы и упражнения.
- Не выдумывай номера страниц, рисунков, контрольных вопросов и упражнений учебника. Если нужных данных нет, pиши «[уточнить по учебнику]».
- Актуализация знаний только по уже изученному материалу, не по темам, которые идут позже.
- Если ответ на задачу зависит от рисунка, добавь пометку «сверьте с рисунком в вашем экземпляре учебника».
- Физические факты должны быть точными. Если не уверен, пометь это для учителя.
- Не используй данные об учениках.
"""

PRES_PROMPT = r"""
Ты методист, который готовит промпты для ИИ-ассистента Кими: Кими по такому промту
сам создаёт готовую презентацию к уроку.

На входе тебе дают план-конспект урока. Составь из него ПОДРОБНЫЙ ПРОМТ ДЛЯ КИМИ,
готовый к копированию в чат Кими.

Что взять из плана:
- Только содержательную часть: тема урока, ключевые понятия и определения, законы и
  формулы, примеры из учебника, задачи для класса, демонстрации и опыты, что ученики
  должны записать в тетрадь.
- НЕ включай методическую часть: организационный момент, ход урока по минутам,
  рефлексию, критерии оценивания, литературу, инструкции по домашнему заданию,
  «Структуру урока» и «Оборудование» - это всё для учителя, Кими не нужно.
- Ничего не выдумывай: бери только термины, формулы и факты из плана. Формулы
  переноси в LaTeX внутри $...$ (индексы через _, дроби через \frac{}{}).

Структура промта (пиши ровно так):
1. Роль и задача Кими: «Ты - создатель учебных презентаций для урока <предмет>,
   <класс> класс...» с указанием, что нужно сделать.
2. Контекст урока: предмет, класс, тема, параграф, учебник.
3. Требования к презентации: 10-12 слайдов, аудитория - ученики <класс> класса,
   язык - русский, крупный шрифт, минимум текста, схемы вместо таблиц,
   каждый слайд - одна мысль.
4. Слайд за слайдом. Для каждого слайда укажи: заголовок; 2-4 коротких тезиса;
   формулы в LaTeX (если есть); что вывести на экран (схема, рисунок, пример,
   задача); заметки для учителя; вопрос классу или задание.
5. Оформление и стиль: светлый фон, тёмный текст, единая схема, минимум анимации.
6. Ограничения: не выдумывать факты и формулы, не добавлять слайды ради объёма,
   не перегружать слайд текстом.
7. Формат ответа Кими: markdown, слайды разделены заголовком «Слайд N».

Пиши по-русски, обычным текстом, без символов Markdown (без **, #), списки через тире.
Выведи только сам промт - без вступных фраз, без пояснений и без комментариев.
"""

# ---------- Книги ----------
BOOKS: Dict[str, List[str]] = {}
# Готовый текстовый индекс (books_index/*.txt.gz) - грузится мгновенно,
# потому что чтение PDF на free-плане Render занимает десятки минут.
BOOKS_INDEX: Dict[str, Path] = {}
# Метаданные из manifest.json: кол-во строк, исходный PDF.
BOOKS_META: Dict[str, Dict[str, object]] = {}
_books_lock = threading.Lock()

SUBJECT_LABELS: Dict[str, str] = {
    "fizika": "Физика",
    "informatika": "Информатика",
    "algebra": "Алгебра",
    "geometriya": "Геометрия",
    "matem": "Математика",
}
SUBJECT_SHORT: Dict[str, str] = {
    "fizika": "Физ",
    "informatika": "Инф",
    "algebra": "Алг",
    "geometriya": "Гео",
    "matem": "Мат",
}


def parse_book_name(name: str) -> Tuple[str, str]:
    """'fizika-10.pdf' -> ('fizika', '10')."""
    base = re.sub(r"\.(pdf|txt)(\.gz)?$", "", name, flags=re.IGNORECASE)
    m = re.match(r"^([a-z]+)-(\d+)", base, re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2)
    return base.lower(), ""


def book_label(name: str) -> str:
    """Человеческое название книги: 'Алгебра 10'."""
    subj, grade = parse_book_name(name)
    title = SUBJECT_LABELS.get(subj, subj)
    return f"{title} {grade}" if grade else title


def book_subject(name: str) -> str:
    """Предмет книги без класса: 'Алгебра'."""
    subj, _ = parse_book_name(name)
    return SUBJECT_LABELS.get(subj, subj)


def book_class(name: str) -> Optional[int]:
    """Класс из названия книги: 'algebra-10.pdf' -> 10."""
    _, grade = parse_book_name(name)
    return int(grade) if grade.isdigit() else None


def book_short_label(name: str) -> str:
    """Короткая подпись для кнопки: 'Алг 10'."""
    subj, grade = parse_book_name(name)
    short = SUBJECT_SHORT.get(subj, subj[:4])
    return f"{short} {grade}" if grade else short


def grade_of(name: str) -> int:
    _, grade = parse_book_name(name)
    return int(grade) if grade.isdigit() else 0


def load_books_index(index_dir: Path) -> Tuple[Dict[str, Path], Dict[str, Dict[str, object]]]:
    idx: Dict[str, Path] = {}
    meta: Dict[str, Dict[str, object]] = {}
    manifest = index_dir / "manifest.json"
    names: List[str] = []
    if manifest.exists():
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    names.append(key)
                    if isinstance(value, dict):
                        meta[key] = value
        except Exception as ex:
            log.warning(f"Не удалось прочитать manifest.json: {ex}")
    if not names:
        names = [p.name[:-len(".txt.gz")] for p in index_dir.glob("*.txt.gz")]
    for name in names:
        p = index_dir / f"{name}.txt.gz"
        if p.exists():
            idx[name] = p
    log.info(f"Индекс книг: {len(idx)} шт в {index_dir}")
    return idx, meta

def get_book(name: str) -> List[str]:
    """Текст книги из индекса, с кэшем в памяти. Читает только запрошенную книгу."""
    with _books_lock:
        if name in BOOKS:
            return BOOKS[name]
        path = BOOKS_INDEX.get(name)
        if not path:
            return []
        try:
            with gzip.open(str(path), "rt", encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().split("\n") if ln]
        except Exception as ex:
            log.error(f"Не удалось прочитать индекс {path.name}: {ex}")
            lines = []
        BOOKS[name] = lines
        log.info(f"Книга загружена из индекса: {name} ({len(lines)} строк)")
        return lines


# ---------- Разбор структуры книги: параграфы ----------
# Заголовок параграфа в учебниках выглядит по-разному:
#   "§ 12. Название"      - номер в начале
#   "Название§ 12."       - номер в конце (так вытягивает pypdf)
#   "12§ 12. Название"    - бегущий колонтитул с номером страницы
#   "§ 1 2."              - номер с разрывом внутри цифр
# Плюс блок "СОДЕРЖАНИЕ" с теми же номерами - его надо отличать от тела.
_SEC_RE = re.compile(r"§\s*(\d(?:\s*\d)*)\s*\.?", re.UNICODE)
_BAD_TITLE = re.compile(r"[()«»\"]|см\.")
_PREP_END = re.compile(
    r"(?:^|\s)(в|из|по|к|и|на|с|о|об|от|до|при|кроме|см|после|про|для|над|под|между)\s*$",
    re.IGNORECASE)
_LETTERS3 = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")
_DOT_LEADER = re.compile(r"(?:\.\s*){3,}")   # выноски в содержании: ". . ." или "...."
_PLAIN_RE = re.compile(r"^(\d{1,2})\s*\.?\s+(.*)$")
_PLAIN_EMPTY_RE = re.compile(r"^(\d{1,2})\s*\.\s*$")
_TOC_GAP = 20          # строк между соседними пунктами содержания
_MIN_TOC_RUN = 3       # столько плотных пунктов подряд - уже содержание
_MAX_HEADING_WORDS = 9


def _norm_ws(text: str) -> str:
    """Пробелы, в том числе неразрывные и narrow, приводим к обычным."""
    return re.sub(r"[\s\u00a0\u2009]+", " ", text).strip()


def _clean_title(text: str) -> str:
    text = re.sub(r"[\s.·]{2,}\s*\d{1,4}\s*$", "", text)
    return _norm_ws(text).strip(" .·")


def _number_ahead(lines: List[str], i: int) -> Tuple[Optional[int], int]:
    """Номер может быть разбит на две строки: '1' + '2.' -> 12."""
    parts: List[str] = []
    j = i
    while j < len(lines) and j < i + 3 and len("".join(parts)) < 3:
        m = re.match(r"^(\d{1,3})(\..*)?$", lines[j].strip())
        if not m:
            break
        parts.append(m.group(1))
        if m.group(2):
            break
        j += 1
    digits = "".join(parts)
    return (int(digits) if digits.isdigit() else None), j


def _scan_headings(lines: List[str]) -> List[List[object]]:
    """Строки, похожие на заголовок параграфа: [номер, индекс строки, название]."""
    out: List[List[object]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if "\u00a7" not in line:
            i += 1
            continue
        m = _SEC_RE.search(line)
        if m:
            digits = re.sub(r"\s+", "", m.group(1))
            if digits.isdigit() and len(digits) <= 3:
                before = line[:m.start()].strip()
                after = line[m.end():].strip()
                title = ""
                keep = True
                if before:
                    if before.isdigit():
                        title = after                      # колонтитул: страница перед §
                    elif _BAD_TITLE.search(before) or _PREP_END.search(before):
                        keep = False                        # перекрёстная ссылка
                    elif not _LETTERS3.search(before) or len(before.split()) > _MAX_HEADING_WORDS:
                        keep = False                        # это не заголовок
                    else:
                        title = before                       # название перед номером
                        if title and title[0].islower() and i > 0:
                            # название началось на предыдущей строке
                            prev = _norm_ws(lines[i - 1])
                            if prev and len(prev) < 80 and not prev[0].isdigit() \
                                    and prev[-1] not in ",.;:":
                                title = f"{prev} {title}"
                else:
                    title = after                           # "§ N. Название"
                    if after.startswith((",", ")", "]")) or "(см." in line:
                        keep = False
                if keep:
                    out.append([int(digits), i, _clean_title(title)])
                i += 1
                continue
        if re.search(r"§\s*$", line):
            title = line[:line.rfind("§")].strip()
            num, j = _number_ahead(lines, i + 1)
            if num is not None and not _BAD_TITLE.search(title) \
                    and not _PREP_END.search(title):
                rest = ""
                m2 = re.match(r"^\s*\d(?:\s*\d)*\s*\.?\s*(.*)$", lines[j])
                if m2:
                    rest = m2.group(1).strip()
                out.append([num, j, _clean_title((title + " " + rest).strip())])
                i = j + 1
                continue
        i += 1
    return out


def _dot_leader_regions(lines: List[str], gap: int = 40, min_lines: int = 3) -> List[Tuple[int, int]]:
    """Границы блока содержания по точечным выноскам '.... 85'."""
    hits = [i for i, l in enumerate(lines) if _DOT_LEADER.search(_norm_ws(l))]
    regions: List[Tuple[int, int]] = []
    if not hits:
        return regions
    start = prev = hits[0]
    count = 1
    for i in hits[1:]:
        if i - prev <= gap:
            prev = i
            count += 1
        else:
            if count >= min_lines:
                regions.append((start, prev))
            start = prev = i
            count = 1
    if count >= min_lines:
        regions.append((start, prev))
    return regions


_TOC_CHAPTER = re.compile(r"^(Глава|ГЛАВА|Раздел|РАЗДЕЛ)\b")
_FOOTER = re.compile(r"^(Правообладатель|Минск\s)")


_SHORT_WORDS = {
    "с", "и", "в", "о", "у", "к", "а", "я", "на", "из", "за", "по", "до", "не", "но",
    "же", "ли", "бы", "то", "их", "для", "от", "под", "над", "при", "через", "без",
    "между", "или", "так", "как", "что", "это", "все", "его", "ее", "её", "мы", "вы",
    "он", "она", "они", "оно", "им", "их", "ему", "ней", "нет", "да", "же", "бы", "то",
}


def _join_fragments(frags: List[str]) -> str:
    """Склеивает куски названия из содержания.

    Перенос строки в содержании - это либо перенос слова (дефис), либо
    просто разбивка длинного названия на несколько строк.
    """
    out = ""
    last_frag = ""
    pending_hyphen = False
    for frag in frags:
        if frag == "-":
            pending_hyphen = True        # дефис отдельной строкой
            continue
        if not out:
            out = frag
            last_frag = frag
            continue
        if pending_hyphen or out.endswith("-"):
            out = out.rstrip("-") + frag  # слово разорвано переносом
        elif (len(last_frag) <= 3 and last_frag.isalpha()
              and last_frag.lower() not in _SHORT_WORDS
              and frag[:1].isalpha() and frag[:1].islower()
              and frag.split(" ")[0].lower() not in _SHORT_WORDS):
            out = out + frag               # кусок слова: "Ди" + "на" -> "Дина"
        else:
            out = out + " " + frag
        last_frag = frag
        pending_hyphen = False
    return out


def _fix_split_word(text: str) -> str:
    """Склеивает разорванное слово: 'дви' + 'жение.' -> 'движение.'."""
    words = text.split(" ")
    out: List[str] = []
    for w in words:
        m = re.match(r"^([а-яё]+)([^а-яё]*)$", w)
        if not m:
            out.append(w)
            continue
        core, tail = m.group(1), m.group(2)
        if (out and len(out[-1]) <= 3 and out[-1].isalpha()
                and out[-1].lower() not in _SHORT_WORDS
                and core and core[0].islower() and core.lower() not in _SHORT_WORDS):
            out[-1] = out[-1] + core + tail
        else:
            out.append(w)
    return " ".join(out)


def _toc_title(lines: List[str], idx: int, first: str) -> str:
    """Полное название параграфа из содержания.

    В содержании название разбито на несколько строк, а иногда и слово
    разорвано переносом ("Ди" + "на" + "мометр" -> "Динамометр").
    """
    frags: List[str] = []
    if first.strip():
        frags.append(first.strip())
    j = idx + 1
    while j < len(lines) and j < idx + 14:
        s = _norm_ws(lines[j])
        if not s:
            break
        if s.startswith("§") or _TOC_CHAPTER.match(s) or _FOOTER.match(s):
            break
        if re.fullmatch(r"\d{1,4}", s):
            break                      # отдельная строка с номером страницы
        if _PLAIN_RE.match(s) and _LETTERS3.search(s):
            break                      # начало следующего пункта содержания
        if len(s) > 70 or s.lower().startswith("рис"):
            break                      # это уже текст тела, а не название
        frags.append(s)
        j += 1

    out = _join_fragments(frags)
    out = re.sub(r"[\s.·]*\d{1,4}\s*$", "", out)     # номер страницы
    out = re.sub(r"[\s.·.]+$", "", out)                 # хвостовые точки
    return _fix_split_word(re.sub(r"\s{2,}", " ", out).strip(" .·"))


def _toc_regions(lines: List[str], occ: List[List[object]]) -> Tuple[List[bool], List[Tuple[int, int]]]:
    """Какие заголовки относятся к содержанию и где оно расположено."""
    gaps = [int(occ[k + 1][1]) - int(occ[k][1]) for k in range(len(occ) - 1)]
    toc_flag = [False] * len(occ)
    regions: List[Tuple[int, int]] = []
    k = 0
    while k < len(occ):
        j = k
        while j + 1 < len(occ) and gaps[j] < _TOC_GAP:
            j += 1
        if j - k + 1 >= _MIN_TOC_RUN:
            for t in range(k, j + 1):
                toc_flag[t] = True
            regions.append((int(occ[k][1]), int(occ[j][1])))
        k = j + 1
    regions.extend(_dot_leader_regions(lines))
    for t, o in enumerate(occ):
        if not toc_flag[t]:
            idx = int(o[1])
            if any(a - 20 <= idx <= b for a, b in regions):
                toc_flag[t] = True
    return toc_flag, regions


def _parse_paragraphs(lines: List[str]) -> List[Dict[str, object]]:
    """Список параграфов книги с их границами в тексте."""
    occ = _scan_headings(lines)
    if not occ:
        return []
    toc_flag, regions = _toc_regions(lines, occ)

    def in_toc(idx: int) -> bool:
        return any(a <= idx <= b for a, b in regions)

    paras: Dict[int, Dict[str, object]] = {}
    for t, o in enumerate(occ):
        if toc_flag[t]:
            continue
        num, idx, title = int(o[0]), int(o[1]), str(o[2])
        if num not in paras:
            paras[num] = {"num": num, "start": idx, "title": title}

    # Названия из содержания - запасной источник (в теле заголовок не всегда есть).
    toc_titles: Dict[int, str] = {}
    for t, o in enumerate(occ):
        if not toc_flag[t]:
            continue
        num = int(o[0])
        if num in toc_titles:
            continue
        full = _toc_title(lines, int(o[1]), str(o[2]))
        if full:
            toc_titles[num] = full
    max_sec = max([int(o[0]) for o in occ] + [0])

    # Номера, записанные без знака § ("19. Почему") - ищем только внутри содержания.
    plain_in_toc: Dict[int, str] = {}
    for a, b in regions:
        for i in range(a, b + 1):
            s = _norm_ws(lines[i])
            m = _PLAIN_EMPTY_RE.match(s) or _PLAIN_RE.match(s)
            if m:
                num = int(m.group(1))
                if 1 <= num <= max_sec:
                    plain_in_toc.setdefault(num, s)
    first_body = min((int(p["start"]) for p in paras.values()), default=0)
    for i, l in enumerate(lines):
        if _norm_ws(l).lower() in ("содержание", "оглавление"):
            if i < first_body:
                for j in range(i, first_body):
                    s = _norm_ws(lines[j])
                    m = _PLAIN_EMPTY_RE.match(s) or _PLAIN_RE.match(s)
                    if m:
                        num = int(m.group(1))
                        if 1 <= num <= max_sec:
                            plain_in_toc.setdefault(num, s)
            break

    known = set(paras) | set(toc_titles) | set(plain_in_toc)
    known = {x for x in known if 1 <= x <= max_sec}
    for num in sorted(known - set(paras)):
        title = toc_titles.get(num, "")
        key = " ".join(_norm_ws(title).lower().split()[:3])[:24]
        if not key and num in plain_in_toc:
            key = " ".join(plain_in_toc[num].lower().split()[:3])[:24]
        for i in range(len(lines)):
            if in_toc(i):
                continue
            s = _norm_ws(lines[i])
            m = _PLAIN_EMPTY_RE.match(s)
            if m and int(m.group(1)) == num:
                paras[num] = {"num": num, "start": i, "title": title}
                break
            m = _PLAIN_RE.match(s)
            if m and int(m.group(1)) == num:
                rest = m.group(2)
                if key and key in s.lower():
                    paras[num] = {"num": num, "start": i, "title": title or rest}
                    break

    starts = sorted(paras.values(), key=lambda p: int(p["start"]))
    # Название параграфа: из тела оно точнее, из содержания - полнее.
    # Если название из тела - лишь часть названия из содержания, берём полное.
    for p in starts:
        body_title = _fix_split_word(str(p.get("title") or "").strip())
        toc_title = toc_titles.get(int(p["num"]), "")
        if len(toc_title) > 110:
            toc_title = toc_title[:110].rsplit(" ", 1)[0]
        if body_title and body_title.replace(" ", "") in toc_title.replace(" ", ""):
            p["title"] = toc_title
        else:
            p["title"] = body_title if body_title else toc_title
    for i, p in enumerate(starts):
        end = len(lines)
        for q in starts[i + 1:]:
            if int(q["num"]) != int(p["num"]):
                end = int(q["start"])
                break
        p["end"] = end
        p["text"] = "\n".join(lines[int(p["start"]):end])
    return starts


_BOOK_PARAS: Dict[str, List[Dict[str, object]]] = {}


def book_paragraphs(name: str) -> List[Dict[str, object]]:
    """Параграфы книги: [{num, title, start, end, text}, ...]."""
    with _books_lock:
        cached = _BOOK_PARAS.get(name)
    if cached is not None:
        return cached
    result = _parse_paragraphs(get_book(name))
    with _books_lock:
        _BOOK_PARAS[name] = result
    log.info(f"Параграфов в «{name}»: {len(result)}")
    return result


def paragraph_text(name: str, num: int) -> str:
    for p in book_paragraphs(name):
        if int(p["num"]) == num:
            return str(p["text"])
    return ""

def warmup_books() -> None:
    """Индекс книг читается сразу (это быстро), PDF - только если индекса нет."""
    global BOOKS_INDEX, BOOKS_META
    try:
        index_dir = find_dir_up("books_index", BASE_DIR)
        if index_dir:
            BOOKS_INDEX, BOOKS_META = load_books_index(index_dir)
    except Exception as ex:
        log.error(f"warmup_books: {ex}")
    if not BOOKS_INDEX:
        # Индекса нет - читать PDF надо долго, поэтому в фоне.
        threading.Thread(target=_load_pdfs_in_background, daemon=True).start()


def _load_pdfs_in_background() -> None:
    try:
        books_dir = find_dir_up("books", BASE_DIR)
        if not books_dir:
            return
        log.info("Индекс не найден - читаю PDF напрямую (на free-плане это очень медленно)")
        BOOKS.update(load_books(books_dir))
        log.info(f"Книг загружено из PDF: {len(BOOKS)}")
    except Exception as ex:
        log.error(f"Не удалось прочитать PDF: {ex}")

# ---------- Чтение одной книги ----------
def read_book_one(file: str) -> Optional[Dict]:
    log.info(f"Читаю книгу: {Path(file).name}")
    try:
        reader = PdfReader(file)
        lines: List[str] = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            for l in text.split("\n"):
                t = l.strip()
                if t:
                    lines.append(t)
            if (i + 1) % 50 == 0:
                log.info(f"  {Path(file).name}: обработано {i + 1}/{len(reader.pages)} страниц")
        name = Path(file).stem
        log.info(f"Прочитана книга: {Path(file).name} ({len(lines)} строк)")
        return {"name": name, "lines": lines}
    except Exception as ex:
        log.error(f"Не удалось прочитать {Path(file).name}: {ex}")
        return None

def subject_rank(name: str) -> int:
    n = name.lower()
    if "fizika" in n: return 0
    if "informatika" in n: return 1
    if "algebra" in n: return 2
    if "geometriya" in n: return 3
    if "matem" in n: return 4
    return 5

def load_books(books_dir: Path) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    files = sorted(books_dir.glob("*.pdf"), key=lambda f: f.name.lower())
    files.sort(key=lambda f: subject_rank(f.name))
    log.info(f"Найдено {len(files)} PDF-файлов в {books_dir}")
    for idx, f in enumerate(files):
        log.info(f"[{idx + 1}/{len(files)}] Читаю {f.name}...")
        r = read_book_one(str(f))
        if r is None:
            continue
        result[r["name"]] = [s for s in r["lines"] if s]
        log.info(f"  Успех: {f.name} ({len(result[r['name']])} строк)")
    log.info(f"Загружено книг: {len(result)}")
    return result

# ---------- Поиск параграфа ----------
def find_paragraph(request: str, only_book: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    I = re.IGNORECASE
    MAX_CHARS = 25000

    grade_rx = re.compile(r"(\d{1,2})\s*(?:-?\s*[а-яё]{1,2})?\s*(?:класс[а-яё]*|кл\b)", I)
    gm = grade_rx.search(request)
    grade = gm.group(1) if gm else None
    no_grade = grade_rx.sub(" ", request)

    subject: Optional[str] = None
    explicit_subject = True

    if re.search(r"(?<![а-яёa-zA-Z])физик", request, I): subject = "fizika"
    elif re.search(r"(?<![а-яёa-zA-Z])информатик", request, I): subject = "informatika"
    elif re.search(r"(?<![а-яёa-zA-Z])алгебр", request, I): subject = "algebra"
    elif re.search(r"(?<![а-яёa-zA-Z])геометри", request, I): subject = "geometriya"
    elif re.search(r"(?<![а-яёa-zA-Z])математик", request, I): subject = "matem"
    else:
        explicit_subject = False
        if re.search(r"(?<![а-яёa-zA-Z])(?:газ|механик|кинематик|термодинамик|электр|оптик|магнит|давлени)", request, I): subject = "fizika"
        elif re.search(r"(?<![а-яёa-zA-Z])(?:функци|уравнени|неравенств|тригонометр|логарифм|прогресси)", request, I): subject = "algebra"
        elif re.search(r"(?<![а-яёa-zA-Z])(?:планиметр|стереометр|вектор|треугольник|окружност)", request, I): subject = "geometriya"
        elif re.search(r"(?<![а-яёa-zA-Z])(?:программ|алгоритм|паскаль|питон|python|компьютер)", request, I): subject = "informatika"

    candidate_names = list(BOOKS_INDEX.keys()) if BOOKS_INDEX else list(BOOKS.keys())
    if only_book:
        # Книга выбрана кнопкой - ищем только в ней.
        candidate_names = [only_book]
    else:
        if subject:
            f = [k for k in candidate_names if subject in k.lower()]
            if explicit_subject or f:
                candidate_names = f
        if grade:
            gr = re.compile(rf"-{grade}(?!\d)")
            f = [k for k in candidate_names if gr.search(k)]
            if explicit_subject or f:
                candidate_names = f
    if not candidate_names:
        log.debug("find_paragraph: подходящих книг не найдено")
        return (None, None)

    num = -1
    em = re.search(r"(?:§|(?<![а-яёa-zA-Z])(?:параграф[а-яё]*|пункт[а-яё]*|пар|п)\.?)\s*(\d+)", no_grade, I)
    if em:
        num = int(em.group(1))
    else:
        im = re.search(r"(?<!\d)\d{1,3}(?!\d)", no_grade)
        if im:
            num = int(im.group(0))

    ignore_stems = {"физик", "инфор", "алгеб", "геоме", "матем", "параг", "урок", "конс", "класс", "соста", "сдела", "план", "тема", "теме", "пожал", "нужен", "нужна"}
    stems: List[str] = []
    seen = set()
    for m in re.finditer(r"[а-яёa-zA-Z]{4,}", no_grade.lower()):
        word = m.group(0)
        s = word[:5] if len(word) > 5 else word
        if s not in ignore_stems and s not in seen:
            seen.add(s)
            stems.append(s)

    if num == -1 and not stems:
        return (None, None)

    best_text: Optional[str] = None
    best_book: Optional[str] = None
    best_score = 0

    for book_name in candidate_names:
        for p in book_paragraphs(book_name):
            pnum = int(p["num"])
            if num != -1:
                hit = pnum == num
                score = 0
            else:
                title = (str(p["title"]) + " " + book_label(book_name)).lower()
                score = sum(1 for s in stems if s in title)
                need = 1 if len(stems) == 1 else max(2, int(math.ceil(len(stems) * 0.6)))
                hit = score >= need
            if not hit:
                continue
            text_res = str(p["text"])[:MAX_CHARS].strip()
            better = (best_text is None or
                      (num != -1 and len(text_res) > len(best_text)) or
                      (score > best_score or (score == best_score and len(text_res) > len(best_text))))
            if better:
                best_text = text_res
                best_book = book_name
                best_score = score

    return (best_text, best_book)

# ---------- Word ----------
def is_heading(line: str) -> bool:
    if not line or len(line) > 90:
        return False
    if re.match(r"^\d+(\.\d+)*[\.\)]\s", line):
        return True
    names = {"План-конспект", "Цель урока", "Задачи урока", "Планируемые результаты",
             "Методы и формы работы", "Оборудование", "Ход урока", "Главные выводы", "Литература"}
    return any(line.startswith(n) for n in names)

def _render_formula(latex: str, tmp_dir: Path, cache: Dict[str, Tuple[Path, float]]):
    """Рисует формулу в PNG. Возвращает (путь, ширина в дюймах) или None."""
    key = latex.strip()
    if not key:
        return None
    if key in cache:
        return cache[key]
    try:
        import latex_png
    except Exception as ex:
        log.warning(f"Не удалось загрузить latex_png: {ex}")
        return None
    safe = re.sub(r"[^A-Za-z0-9]+", "_", key)[:36] or "formula"
    path = tmp_dir / f"{safe}_{len(cache)}.png"
    try:
        width, _height = latex_png.render_formula(key, path)
    except Exception as ex:
        log.warning(f"Формулу не удалось отрисовать ({ex}): {key[:60]}")
        return None
    cache[key] = (path, width)
    return path, width


def _fill_paragraph(p, line: str, bold: bool = False,
                    tmp_dir: Optional[Path] = None,
                    cache: Optional[Dict[str, Tuple[Path, float]]] = None,
                    formulas_as_text: bool = False) -> None:
    """Заполняет абзац текстом и формулами.

    Формулы рисуются картинкой: родные формулы Word (OMML) показывает только
    Word, а мобильные просмотрщики их не рисуют и просто вырезают.
    formulas_as_text=True - оставить формулы текстом LaTeX (для промта,
    который потом пойдёт в другого ИИ).
    """
    from docx.shared import Inches, Pt

    def add_text(chunk: str):
        if not chunk:
            return
        run = p.add_run(chunk)
        run.font.name = "Times New Roman"
        run.font.size = Pt(12)
        run.bold = bold

    pos = 0
    for m in MATH_SPAN_RE.finditer(line):
        add_text(line[pos:m.start()])
        latex = m.group(1) if m.group(1) is not None else m.group(2)
        img = None
        if not formulas_as_text and tmp_dir is not None and cache is not None:
            img = _render_formula(latex, tmp_dir, cache)
        if img:
            path, width = img
            try:
                p.add_run().add_picture(str(path), width=Inches(width))
            except Exception as ex:
                log.warning(f"Не удалось вставить формулу в документ: {ex}")
                add_text(f"${latex}$")
        elif formulas_as_text:
            add_text(f"${latex}$")   # оставляем разделители - по ним ИИ понимает формулу
        else:
            add_text(latex)
        pos = m.end()
    add_text(line[pos:])


def make_docx(plan: str, path: str, formulas_as_text: bool = False):
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    tmp_dir = Path(tempfile.gettempdir()) / "bottg_formulas"
    tmp_dir.mkdir(exist_ok=True)
    cache: Dict[str, Tuple[Path, float]] = {}
    for raw in plan.replace("\r", "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue                      # пустые строки не нужны - пробел есть в абзацах
        bullet = line.startswith("- ")
        if bullet:
            line = "• " + line[2:]
        heading = is_heading(line)
        p = doc.add_paragraph()
        _fill_paragraph(p, line, heading, tmp_dir, cache, formulas_as_text)
        p.paragraph_format.space_after = Pt(6)
        if bullet:
            p.paragraph_format.left_indent = Pt(36)
    doc.save(path)

# ---------- Telegram ----------
def tg_post(method: str, data: dict, files: Optional[dict] = None) -> Optional[dict]:
    """Отправляет запрос в Telegram API. Возвращает разобранный JSON-ответ или None."""
    url = API_BASE + method
    try:
        if files is None:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        else:
            boundary = "----WebKitFormBoundary" + os.urandom(8).hex()
            # Каждый блок multipart должен заканчиваться CRLF, а между
            # заголовком поля и его значением лишнего перевода строки быть не должно -
            # иначе Telegram видит chat_id как "\r\n1177..." и отвечает "chat not found".
            chunks: List[bytes] = []
            for k, v in data.items():
                chunks.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
            for k, (fname, fbytes, ctype) in files.items():
                chunks.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\""
                    f"\r\nContent-Type: {ctype}\r\n\r\n".encode())
                chunks.append(fbytes)
                chunks.append(b"\r\n")
            chunks.append(f"--{boundary}--".encode())
            body = b"".join(chunks)
            req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        log.debug(f"Telegram {method} OK")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return {"ok": True}
        return parsed if isinstance(parsed, dict) else {"ok": True}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            body = ""
        log.warning(f"Telegram {method} error: HTTP {e.code} {body}")
        return None
    except Exception as e:
        log.warning(f"Telegram {method} error: {e}")
        return None


def split_text(text: str, limit: int = 4000) -> List[str]:
    """Режет текст на части не длиннее limit, не разрывая строки по возможности."""
    chunks: List[str] = []
    rest = text
    while rest:
        length = min(limit, len(rest))
        if length < len(rest):
            cut = rest.rfind("\n", 0, length - 1)
            if cut > 1000:
                length = cut + 1
        chunks.append(rest[:length])
        rest = rest[length:]
    return chunks


def send(chat_id: int, text: str, reply_markup: Optional[dict] = None) -> Optional[int]:
    """Отправляет сообщение, возвращает message_id последнего отправленного сообщения."""
    if not text or not text.strip():
        text = "(пустой ответ)"
    chunks = split_text(text)
    msg_id = None
    for i, chunk in enumerate(chunks):
        payload: Dict[str, object] = {"chat_id": chat_id, "text": chunk}
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        resp = tg_post("sendMessage", payload)
        if resp and resp.get("ok") and isinstance(resp.get("result"), dict):
            msg_id = resp["result"].get("message_id")
        else:
            log.warning(f"sendMessage не вернул message_id: {str(resp)[:200]}")
    return msg_id


def edit_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None) -> bool:
    """Редактирует существующее сообщение (для прогресс-бара)."""
    if message_id is None:
        return False
    if not text or not text.strip():
        text = "(пустой ответ)"
    payload: Dict[str, object] = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000]}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = tg_post("editMessageText", payload)
    return bool(resp and resp.get("ok"))


def answer_cb(callback_id: str, text: str = "") -> bool:
    """Снимает «часики» на кнопке и показывает короткий ответ."""
    if not callback_id:
        return False
    payload: Dict[str, object] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:200]
    resp = tg_post("answerCallbackQuery", payload)
    return bool(resp and resp.get("ok"))


_BOOK_ORDER: List[str] = []


def book_order() -> List[str]:
    """Книги в стабильном порядке - индекс книги нужен для callback_data."""
    global _BOOK_ORDER
    if not _BOOK_ORDER:
        names = list(BOOKS_INDEX.keys()) if BOOKS_INDEX else list(BOOKS.keys())
        names.sort(key=lambda n: (subject_rank(n), grade_of(n), n))
        _BOOK_ORDER = names
    return _BOOK_ORDER


def book_by_index(idx: int) -> Optional[str]:
    names = book_order()
    return names[idx] if 0 <= idx < len(names) else None


def books_keyboard() -> Optional[dict]:
    """Инлайн-кнопки выбора книги, сгруппированные по предмету."""
    names = book_order()
    if not names:
        return None
    buttons = [{"text": book_short_label(n), "callback_data": f"bk:{i}"} for i, n in enumerate(names)]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([{"text": "Отмена", "callback_data": "bkx"}])
    return {"inline_keyboard": rows}


PARAS_PER_PAGE = 12


def paragraph_keyboard(book_name: str, page: int = 0) -> Optional[dict]:
    """Кнопки выбора параграфа внутри книги, постранично."""
    paras = book_paragraphs(book_name)
    if not paras:
        return None
    pages = max(1, (len(paras) + PARAS_PER_PAGE - 1) // PARAS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = paras[page * PARAS_PER_PAGE:(page + 1) * PARAS_PER_PAGE]
    try:
        bidx = book_order().index(book_name)
    except ValueError:
        return None
    buttons = []
    for p in chunk:
        buttons.append({"text": f"§ {p['num']}", "callback_data": f"p:{bidx}:{p['num']}"})
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    nav: List[Dict[str, str]] = []
    if page > 0:
        nav.append({"text": "←", "callback_data": f"pg:{bidx}:{page - 1}"})
    if page < pages - 1:
        nav.append({"text": "→", "callback_data": f"pg:{bidx}:{page + 1}"})
    nav.append({"text": "Отмена", "callback_data": "bkx"})
    rows.append(nav)
    return {"inline_keyboard": rows}


def book_toc_text(book_name: str) -> str:
    """Содержание книги текстом - чтобы видеть названия параграфов."""
    paras = book_paragraphs(book_name)
    lines = [f"{book_label(book_name)} — параграфы:", ""]
    for p in paras:
        title = str(p.get("title") or "")
        lines.append(f"§ {p['num']}" + (f". {title}" if title else ""))
    lines.append("")
    lines.append("Выбери параграф кнопкой ниже.")
    return "\n".join(lines)


# ---------- Состояние чата ----------
# Живёт в SQLite, а не в памяти: под gunicorn работает несколько воркеров,
# и выбранная книга (или последний план) должна быть видна тому воркеру,
# который примет следующее сообщение. Через JSON-файл воркеры перетирали
# друг друга при записи - теперь пишем транзакцией.
def state_get(chat_id: int) -> Dict[str, object]:
    """Запись состояния чата: выбранная книга и последний план-конспект."""
    try:
        return state_store.get(int(chat_id))
    except Exception as ex:
        log.warning(f"Не удалось прочитать состояние: {ex}")
        return {}


def state_set(chat_id: int, **fields) -> None:
    """Обновляет поля записи состояния; пустое значение удаляет поле."""
    try:
        state_store.set_fields(int(chat_id), **fields)
    except Exception as ex:
        log.warning(f"Не удалось сохранить состояние: {ex}")


def send_doc(chat_id: int, path: str, caption: str):
    try:
        data = Path(path).read_bytes()
        tg_post("sendDocument", {"chat_id": chat_id, "caption": caption},
                {"document": (Path(path).name, data, "application/octet-stream")})
    except Exception as e:
        log.warning(f"sendDoc error: {e}")

# ---------- Модель ----------
def api_family(m: str) -> str:
    if m.startswith("claude-") or m.startswith("qwen3.8-flash") or m.startswith("qwen3.6") or m.startswith("qwen3.5"):
        return "anthropic"
    if m.startswith("gpt-") or m.startswith("grok-") or m.startswith("muse-"):
        return "responses"
    if m.startswith("gemini-"):
        return "google"
    return "chat"

def models_list() -> str:
    sb = ["Доступные модели (команда: /model имя):"]
    for k, v in MODELS.items():
        sb.append(f"- {k} ({v})")
    sb.append(f"\nСейчас выбрана: {MODEL}")
    return "\n".join(sb)

def ask_model(user_request: str, system: Optional[str] = None) -> str:
    family = api_family(MODEL)
    if family == "google":
        return "Модели Gemini бот пока не поддерживает."

    sys_prompt = system or SYSTEM_PROMPT
    if family == "anthropic":
        url = "https://opencode.ai/inference/anthropic/v1/messages"
        body = {"model": MODEL, "max_tokens": 32000, "system": sys_prompt,
                "messages": [{"role": "user", "content": user_request}]}
    elif family == "responses":
        url = "https://opencode.ai/inference/openai/v1/responses"
        body = {"model": MODEL, "instructions": sys_prompt, "input": user_request, "max_output_tokens": 16000}
    else:
        url = "https://opencode.ai/inference/openai/v1/chat/completions"
        body = {"model": MODEL, "max_tokens": 16000,
                "messages": [{"role": "system", "content": sys_prompt},
                             {"role": "user", "content": user_request}]}

    log.info(f"Запрос к модели {MODEL} ({family}), длина запроса {len(user_request)} символов")
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Authorization": f"Bearer {ZEN_KEY}",
                                          "Content-Type": "application/json",
                                          "User-Agent": MODEL_UA})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            resp_text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        log.warning(f"Модель {MODEL} ответила HTTP {e.code}: {detail}")
        return f"Ошибка модели {MODEL}: HTTP {e.code} {detail}"
    except Exception as e:
        return f"Ошибка модели {MODEL}: {e}"

    try:
        root = json.loads(resp_text)
    except Exception:
        return f"Ошибка парсинга ответа модели {MODEL}"

    result: List[str] = []
    if family == "anthropic":
        for block in root.get("content", []):
            if isinstance(block, dict) and "text" in block:
                result.append(block["text"])
    elif family == "responses":
        for item in root.get("output", []):
            parts = item.get("content")
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        result.append(part["text"])
    else:
        choices = root.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content")
            if isinstance(content, str):
                result.append(content)

    stop = None
    if family == "anthropic":
        stop = root.get("stop_reason")
    elif family == "responses":
        stop = root.get("status")
    else:
        choices = root.get("choices", [])
        if choices:
            stop = choices[0].get("finish_reason")

    if stop in ("max_tokens", "incomplete", "length"):
        log.warning(f"Ответ обрезан по лимиту токенов ({MODEL}). stop_reason={stop}")
        result.append("\n\n[Конспект обрезан по лимиту длины. Попробуйте запросить его снова или переключите модель.]")

    return "".join(result)

# ---------- Обработка обновления ----------
def run_plan(chat_id: int, request: str, only_book: Optional[str] = None,
             para_num: Optional[int] = None, status: Optional[int] = None) -> None:
    """Главный сценарий: найти параграф, собрать план, отправить документ."""
    if status is None:
        status = send(chat_id, "Ищу параграф в учебниках...")

    paragraph = ""
    book_name = only_book
    if para_num is not None and only_book:
        paragraph = paragraph_text(only_book, para_num)
    else:
        paragraph, book_name = find_paragraph(request, only_book=only_book)

    # Класс и учебник известны - передаём их модели явно, иначе в шапке
    # плана появляется «[уточнить по учебнику]».
    context: List[str] = []
    if book_name:
        grade = book_class(book_name)
        context.append(f"Предмет: {book_subject(book_name)}")
        if grade:
            context.append(f"Класс: {grade}")
        context.append(f"Учебник: {book_label(book_name)}")
    if para_num is not None:
        context.append(f"Параграф: {para_num}")

    user_request = request
    if context:
        user_request = "Данные об уроке:\n" + "\n".join(context) + "\n\n" + user_request

    if paragraph:
        user_request += "\n\nТекст параграфа из учебника (строй план на нём):\n" + paragraph
        where = book_label(book_name) if book_name else "учебнике"
        edit_message(chat_id, status, f"Нашёл параграф в «{where}» ({len(paragraph)} символов)")
    else:
        edit_message(chat_id, status, "Параграф в книгах не нашёл — составлю план без текста учебника.")

    edit_message(chat_id, status, f"Генерирую план через {MODEL}, это займёт 1-2 минуты...")
    answer = ask_model(user_request)
    if answer.startswith("Ошибка модели"):
        # План не получился - не надо сохранять текст ошибки в документ.
        log.warning(f"План не сгенерирован: {answer}")
        edit_message(chat_id, status, f"Ошибка: {answer}")
        return

    try:
        path = Path(tempfile.gettempdir()) / f"plan_{datetime.now():%Y%m%d_%H%M%S%f}.docx"
        make_docx(answer, str(path))
        send_doc(chat_id, str(path), "План-конспект урока")
        # Сохраняем текст плана - по нему потом собираем промт для презентации.
        state_set(chat_id, plan=answer, at=int(datetime.now().timestamp()))
        edit_message(chat_id, status, "Готово! План-конспект — в документе ниже.",
                     reply_markup=PRES_KEYBOARD)
        try:
            path.unlink()
        except Exception:
            pass
    except Exception as doc_ex:
        log.warning("DOCX error: " + str(doc_ex))
        send(chat_id, answer)
        state_set(chat_id, plan=answer, at=int(datetime.now().timestamp()))
        edit_message(chat_id, status, "Готово! Ниже — план текстом.",
                     reply_markup=PRES_KEYBOARD)


PRES_KEYBOARD = {"inline_keyboard": [[{"text": "Промт для презентации",
                                       "callback_data": "pr:"}]]}
PRES_TTL = 2 * 60 * 60      # план хранится 2 часа


def make_presentation_prompt(chat_id: int) -> None:
    """Собирает промт для Кими по последнему плану-конспекту."""
    rec = state_get(chat_id)
    plan = str(rec.get("plan") or "")
    at = int(rec.get("at") or 0)
    if not plan:
        send(chat_id, "Сначала сделайте план-конспект (кнопки с книгой и параграфом), "
                      "тогда я соберу промт для презентации.")
        return
    if at and datetime.now().timestamp() - at > PRES_TTL:
        send(chat_id, "План-конспект уже старый (больше двух часов). Сделайте новый — "
                      "и я соберу промт для презентации.")
        return

    status = send(chat_id, "Готовлю промт для презентации...")
    request = ("Составь промт для ИИ Кими, чтобы он сделал презентацию к этому уроку.\n\n"
               "План-конспект урока:\n" + plan)
    prompt = ask_model(request, system=PRES_PROMPT)
    if prompt.startswith("Ошибка модели"):
        log.warning(f"Промт для презентации не получен: {prompt}")
        edit_message(chat_id, status, f"Ошибка: {prompt}")
        return
    try:
        path = Path(tempfile.gettempdir()) / f"pres_{datetime.now():%Y%m%d_%H%M%S%f}.docx"
        make_docx(prompt, str(path), formulas_as_text=True)
        send_doc(chat_id, str(path), "Промт для Кими - презентация к уроку")
        edit_message(chat_id, status, "Промт для презентации готов — файл ниже. "
                                      "Вставьте его в Кими.")
        try:
            path.unlink()
        except Exception:
            pass
    except Exception as doc_ex:
        log.warning("DOCX error (промт): " + str(doc_ex))
        send(chat_id, prompt)
        edit_message(chat_id, status, "Готово! Ниже — промт текстом.")


def handle_callback_query(cb: dict) -> None:
    """Обработка нажатия на инлайн-кнопку (выбор книги или параграфа)."""
    try:
        from_id = cb["from"]["id"]
        if from_id not in ALLOWED_USERS:
            return
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        mid = msg.get("message_id")
        data = cb.get("data") or ""
        cb_id = cb.get("id") or ""
        if not chat_id:
            return

        if data.startswith("bk:"):
            try:
                idx = int(data[3:])
            except ValueError:
                idx = -1
            name = book_by_index(idx)
            if not name:
                answer_cb(cb_id, "Книга не найдена")
                return
            state_set(chat_id, book=name)
            answer_cb(cb_id, book_label(name))
            edit_message(chat_id, mid, book_toc_text(name),
                         reply_markup=paragraph_keyboard(name, 0))
        elif data.startswith("p:"):
            # p:<индекс книги>:<номер параграфа>
            parts = data.split(":")
            try:
                idx = int(parts[1])
                num = int(parts[2])
            except (IndexError, ValueError):
                answer_cb(cb_id)
                return
            name = book_by_index(idx)
            if not name:
                answer_cb(cb_id, "Книга не найдена")
                return
            answer_cb(cb_id, f"§ {num}")
            run_plan(chat_id, f"§ {num}", only_book=name, para_num=num, status=mid)
        elif data.startswith("pg:"):
            # pg:<индекс книги>:<страница>
            parts = data.split(":")
            try:
                idx = int(parts[1])
                page = int(parts[2])
            except (IndexError, ValueError):
                answer_cb(cb_id)
                return
            name = book_by_index(idx)
            if not name:
                answer_cb(cb_id, "Книга не найдена")
                return
            answer_cb(cb_id)
            edit_message(chat_id, mid, book_toc_text(name),
                         reply_markup=paragraph_keyboard(name, page))
        elif data.startswith("pr"):
            answer_cb(cb_id, "Готовлю промт")
            make_presentation_prompt(chat_id)
        elif data == "bkx":
            state_set(chat_id, book=None, plan=None, at=None)
            answer_cb(cb_id, "Отменено")
            edit_message(chat_id, mid, "Выбор отменён.", reply_markup={"inline_keyboard": []})
        else:
            answer_cb(cb_id)
    except Exception as ex:
        log.warning(f"HandleCallbackQuery error: {ex}")


def refresh_model() -> None:
    """Подхватывает модель, выбранную в другой воркер (MODEL - глобальная переменная процесса)."""
    global MODEL
    try:
        saved = state_store.get_setting("model")
    except Exception:
        return
    if saved and saved in MODELS.values() and saved != MODEL:
        MODEL = saved
        log.info(f"Модель из общих настроек: {MODEL}")


def handle_update(update: dict):
    try:
        refresh_model()
        cb = update.get("callback_query")
        if cb:
            handle_callback_query(cb)
            return
        msg = update.get("message")
        if not msg or "text" not in msg:
            return
        from_id = msg["from"]["id"]
        if from_id not in ALLOWED_USERS:
            return
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        if not text:
            return

        global MODEL

        if text.startswith("/start"):
            send(
                chat_id,
                "Привет. Бот делает план-конспекты по физике, математике и информатике.\n"
                "Выбери книгу кнопками ниже или напиши класс, предмет и номер параграфа, "
                "например: 10 класс, физика, § 25.\n"
                "/books - список книг\n/models - модели\n/model имя - сменить модель",
                reply_markup=books_keyboard(),
            )
            return
        if text.startswith("/през") or text.startswith("/presentation"):
            make_presentation_prompt(chat_id)
            return
        if text.startswith("/books"):
            names = book_order()
            if not names:
                send(chat_id, "Книг нет. Положи PDF в папку books.")
                return
            lines = []
            for name in names:
                meta = BOOKS_META.get(name) or {}
                count = meta.get("lines")
                suffix = f" ({count} строк)" if isinstance(count, int) else " (в индексе)"
                lines.append(f"- {book_label(name)}{suffix}")
            send(chat_id, "Доступные книги:\n" + "\n".join(lines), reply_markup=books_keyboard())
            return
        if text.startswith("/models"):
            send(chat_id, models_list())
            return
        if text.startswith("/model"):
            arg = text[6:].strip()
            if arg in MODELS:
                MODEL = MODELS[arg]
                try:
                    state_store.set_setting("model", MODEL)
                except Exception as ex:
                    log.warning(f"Не удалось сохранить выбор модели: {ex}")
                send(chat_id, f"Модель переключена: {MODEL}")
            else:
                send(chat_id, "Такой модели нет.\n\n" + models_list())
            return

        # Книга, выбранная кнопкой, действует на один запрос.
        selected = str(state_get(chat_id).get("book") or "")
        if selected:
            state_set(chat_id, book=None)
        run_plan(chat_id, text, only_book=selected or None)
    except Exception as ex:
        log.warning("HandleUpdate error: " + str(ex))
        try:
            chat_id = (update.get("message") or {}).get("chat", {}).get("id")
            if chat_id:
                send(chat_id, "Что-то пошло не так, разбираюсь. Попробуйте ещё раз или "
                              "напишите /books.")
        except Exception:
            pass

# ---------- Веб-сервер ----------
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(got, WEBHOOK_SECRET):
            log.warning("Вебхук с неверным секретом - запрос отклонён")
            return jsonify({"status": "forbidden"}), 403
    update = request.get_json(force=True) or {}
    threading.Thread(target=handle_update, args=(update,), daemon=True).start()
    return jsonify({"status": "ok"})

# ---------- Старт ----------
def _is_read_book_cli() -> bool:
    return len(sys.argv) >= 2 and sys.argv[1] == "--read-book"


def startup() -> None:
    """Стартовые действия: загрузка книг и установка вебхука.

    Вызывается при импорте модуля, потому что gunicorn не выполняет блок
    __main__, а запускает приложение как WSGI-объект.
    """
    warmup_books()          # индекс читается быстро - синхронно, чтобы книги были сразу
    log.info(f"Сервер стартует, книги на месте. Модель: {MODEL}")

    if not PUBLIC_URL:
        log.warning("PUBLIC_URL не задан — вебхук не устанавливаю")
        return
    try:
        webhook_url = f"{PUBLIC_URL}/webhook"
        log.info(f"Устанавливаю вебхук: {webhook_url}")
        url = f"{API_BASE}setWebhook?url={urllib.parse.quote(webhook_url)}"
        if WEBHOOK_SECRET:
            url += f"&secret_token={urllib.parse.quote(WEBHOOK_SECRET)}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            log.info(f"setWebhook ответ: {resp.read().decode()}")
    except Exception as e:
        log.warning(f"Ошибка установки вебхука: {e}")


def _startup_enabled() -> bool:
    if os.environ.get("BOTTG_SKIP_STARTUP") == "1":
        return False
    return not _is_read_book_cli()


if _startup_enabled():
    startup()


# ---------- Точка входа ----------
if __name__ == "__main__":
    if _is_read_book_cli():
        r = read_book_one(sys.argv[2])
        if r is None:
            print("null")
        else:
            print(json.dumps(r, ensure_ascii=False))
        sys.exit(0)

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))