#!/usr/bin/env python3
"""Telegram bot: plan-koncepty urokov. Python port of bottg/Program.cs."""

import os
import sys
import re
import json
import gzip
import subprocess
import tempfile
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

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

BASE_DIR = Path(sys.argv[0]).resolve().parent
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
PUBLIC_URL = get_env("PUBLIC_URL").rstrip("/")

ALLOWED_USERS = {OWNER_ID, 1329641372, 1079330932, 1079918562, 5206288199}

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

SYSTEM_PROMPT = """
Ты помощник учителя физики, математики и информатики в Беларуси. Ты составляешь подробный план-конспект урока.

Пиши по-русски простым текстом, без символов Markdown (без **, #, таблиц с |).
Заголовки разделов пиши с новой строки, списки через тире (строка начинается с «- »).

Структура плана-конспекта:
1. Шапка: Учебный предмет, Класс, Глава, Тема урока, Учебное пособие (с указанием страниц),
   Тип урока, Продолжительность (45 минут).
2. Цель урока.
3. Задачи урока: образовательные, развивающие, воспитательные.
4. Планируемые результаты (к концу урока учащиеся смогут...).
5. Методы и формы работы.
6. Оборудование: демонстрационное, раздаточное, дополнительное.
7. Ход урока с указанием минут (в сумме 45):
   1) Организационный момент (2 мин)
   2) Актуализация знаний (5 мин). Вопросы для фронтальной беседы OBЯЗАТЕЛЬНО должны содержать краткие правильные ответы в скобках после каждого вопроса (для самопроверки учителя).
   3) Мотивация и постановка проблемы (3 мин): демонстрация или проблемный вопрос. ТЫ ОБЯЗАН привести не просто ответ, а подробное физическое/логическое объяснение этой проблемы, чтобы учитель мог озвучить его классу.
   4) Изложение нового материала (около 22 мин) по подпунктам 4.1, 4.2 и так далее с минутами,
      с записью в тетрадь, опытами, формулами и определениями.
   5) Закрепление (8 мин): утверждения «верно или неверно» с ответами, контрольные вопросы с ответами. Если приводится практическая задача, ТЫ ОБЯЗАН расписать ее ПОЛНОЕ И ПОДРОБНОЕ РЕШЕНИЕ: краткое условие (Дано / Найти), перевод величин в СИ, базовые формулы, пошаговые математические вычисления и финальный численный ответ.
   6) Рефлексия (2 мин)
   7) Информация о домашнем задании (3 мин): номера параграфов, вопросов или задач. Если учителю предлагается задать на дом задачи, ТЫ ОБЯЗАН прямо в конспекте расписать их полное пошаговое решение и ответы, чтобы учителю не пришлось считать самостоятельно при проверке.
8. Главные выводы (запись в тетрадь).
9. Литература.

Правила:
- К абсолютно любой sгенерированной задаче, проблемному вопросу или практическому заданию на любом этапе урока ТЫ ОБЯЗАН предоставить ПОЛНОЕ ХОД РЕШЕНИЯ с формулами, промежуточными расчетами и финальным ответом. Не ограничивайся только кратким ответом.
- Если в запросе приведён текст параграфа учебника, строй план строго на нём: используй его определения, примеры, опыты, контрольные вопросы и упражнения.
- Не выдумывай номера страниц, рисунков, контрольных вопросов и упражнений учебника. Если нужных данных нет, pиши «[уточнить по учебнику]».
- Актуализация знаний только по уже изученному материалу, не по темам, которые идут позже.
- Если ответ на задачу зависит от рисунка, добавь пометку «сверьте с рисунком в вашем экземпляре учебника».
- Физические факты должны быть точными. Если не уверен, пометь это для учителя.
- Не используй данные об учениках.
"""

# ---------- Книги ----------
BOOKS: Dict[str, List[str]] = {}
# Готовый текстовый индекс (books_index/*.txt.gz) - грузится мгновенно,
# потому что чтение PDF на free-плане Render занимает десятки минут.
BOOKS_INDEX: Dict[str, Path] = {}
_books_lock = threading.Lock()

def load_books_index(index_dir: Path) -> Dict[str, Path]:
    idx: Dict[str, Path] = {}
    manifest = index_dir / "manifest.json"
    names: List[str] = []
    if manifest.exists():
        try:
            names = list(json.loads(manifest.read_text(encoding="utf-8")).keys())
        except Exception as ex:
            log.warning(f"Не удалось прочитать manifest.json: {ex}")
    if not names:
        names = [p.name[:-len(".txt.gz")] for p in index_dir.glob("*.txt.gz")]
    for name in names:
        p = index_dir / f"{name}.txt.gz"
        if p.exists():
            idx[name] = p
    log.info(f"Индекс книг: {len(idx)} шт в {index_dir}")
    return idx

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

def warmup_books() -> None:
    global BOOKS_INDEX
    try:
        index_dir = find_dir_up("books_index", BASE_DIR)
        if index_dir:
            BOOKS_INDEX = load_books_index(index_dir)
        books_dir = find_dir_up("books", BASE_DIR)
        if books_dir and not BOOKS_INDEX:
            log.info("Индекс не найден - читаю PDF напрямую (на free-плане это очень медленно)")
            BOOKS.update(load_books(books_dir))
            log.info(f"Книг загружено из PDF: {len(BOOKS)}")
    except Exception as ex:
        log.error(f"warmup_books: {ex}")

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
def find_paragraph(request: str) -> Tuple[Optional[str], Optional[str]]:
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
            num = int(im.group(1))

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

    import math
    best_text: Optional[str] = None
    best_book: Optional[str] = None
    best_score = 0

    for book_name in candidate_names:
        lines = get_book(book_name)
        if not lines:
            continue
        starts = [idx for idx, line in enumerate(lines) if re.match(r"^§\s*\d+", line)]
        for k in range(len(starts)):
            end = starts[k + 1] if k + 1 < len(starts) else min(len(lines), starts[k] + 600)
            if num != -1:
                head = re.match(r"^§\s*(\d+)", lines[starts[k]])
                hit = bool(head and int(head.group(1)) == num)
            else:
                title = lines[starts[k]]
                if starts[k] + 1 < end:
                    title += " " + lines[starts[k] + 1]
                title = title.lower()
                score = sum(1 for s in stems if s in title)
                need = 1 if len(stems) == 1 else max(2, int(math.ceil(len(stems) * 0.6)))
                hit = score >= need
            if not hit:
                continue
            sb: List[str] = []
            cur_len = 0
            for j in range(starts[k], end):
                if cur_len + len(lines[j]) + 1 > MAX_CHARS:
                    break
                sb.append(lines[j])
                cur_len += len(lines[j]) + 1
            text_res = "\n".join(sb).strip()
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

def make_docx(plan: str, path: str):
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    for raw in plan.replace("\r", "").split("\n"):
        line = raw.rstrip()
        bullet = line.startswith("- ")
        if bullet:
            line = "• " + line[2:]
        heading = is_heading(line)
        p = doc.add_paragraph()
        run = p.add_run(line)
        run.font.name = "Times New Roman"
        run.font.size = Pt(12)
        run.bold = heading
        p.paragraph_format.space_after = Pt(6)
        if bullet:
            p.paragraph_format.left_indent = Pt(36)
    doc.save(path)

# ---------- Telegram ----------
def tg_post(method: str, data: dict, files: Optional[dict] = None) -> bool:
    url = API_BASE + method
    try:
        if files is None:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        else:
            boundary = "----WebKitFormBoundary" + os.urandom(8).hex()
            parts: List[bytes] = []
            for k, v in data.items():
                parts.append(f"--{boundary}".encode())
                parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
                parts.append(str(v).encode())
            for k, (fname, fbytes, ctype) in files.items():
                parts.append(f"--{boundary}".encode())
                parts.append(f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode())
                parts.append(fbytes)
            parts.append(f"--{boundary}--".encode())
            body = b"\r\n".join(parts)
            req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        log.debug(f"Telegram {method} OK")
        return True
    except Exception as e:
        log.warning(f"Telegram {method} error: {e}")
        return False

def send(chat_id: int, text: str):
    if not text or not text.strip():
        text = "(пустой ответ)"
    while text:
        length = min(4000, len(text))
        if length < len(text):
            cut = text.rfind("\n", 0, length - 1)
            if cut > 1000:
                length = cut + 1
        tg_post("sendMessage", {"chat_id": chat_id, "text": text[:length]})
        text = text[length:]

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

def ask_model(user_request: str) -> str:
    family = api_family(MODEL)
    if family == "google":
        return "Модели Gemini бот пока не поддерживает."

    if family == "anthropic":
        url = "https://opencode.ai/inference/anthropic/v1/messages"
        body = {"model": MODEL, "max_tokens": 32000, "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_request}]}
    elif family == "responses":
        url = "https://opencode.ai/inference/openai/v1/responses"
        body = {"model": MODEL, "instructions": SYSTEM_PROMPT, "input": user_request, "max_output_tokens": 16000}
    else:
        url = "https://opencode.ai/inference/openai/v1/chat/completions"
        body = {"model": MODEL, "max_tokens": 16000,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": user_request}]}

    log.info(f"Запрос к модели {MODEL} ({family}), длина запроса {len(user_request)} символов")
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Authorization": f"Bearer {ZEN_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            resp_text = resp.read().decode("utf-8")
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
def handle_update(update: dict):
    try:
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
            send(chat_id, "Привет. Бот делает план-конспекты по физике, математике и информатике.\nНапиши класс, предмет и номер параграфа, например: 10 класс, физика, § 25.\n/books - список книг\n/models - модели\n/model имя - сменить модель")
            return
        if text.startswith("/books"):
            if not BOOKS:
                send(chat_id, "Книг нет. Положи PDF в папку books.")
            else:
                send(chat_id, "\n".join(f"- {k} ({len(v)} строк)" for k, v in BOOKS.items()))
            return
        if text.startswith("/models"):
            send(chat_id, models_list())
            return
        if text.startswith("/model"):
            arg = text[6:].strip()
            if arg in MODELS:
                MODEL = MODELS[arg]
                send(chat_id, f"Модель переключена: {MODEL}")
            else:
                send(chat_id, "Такой модели нет.\n\n" + models_list())
            return

        user_request = text
        paragraph, book_name = find_paragraph(text)
        if paragraph:
            user_request += "\n\nТекст параграфа из учебника (строй план на нём):\n" + paragraph
            send(chat_id, f"Нашёл параграф в книге «{book_name}» ({len(paragraph)} символов).")
        elif not BOOKS:
            pass
        else:
            send(chat_id, "Параграф в книгах не нашёл. Составлю план без текста учебника.")

        send(chat_id, f"Готовлю план урока ({MODEL}), это займёт 1-2 минуты...")
        answer = ask_model(user_request)

        try:
            path = Path(tempfile.gettempdir()) / f"plan_{datetime.now():%Y%m%d_%H%M%S%f}.docx"
            make_docx(answer, str(path))
            send_doc(chat_id, str(path), "План-конспект урока")
            try:
                path.unlink()
            except Exception:
                pass
        except Exception as doc_ex:
            log.warning("DOCX error: " + str(doc_ex))
            send(chat_id, answer)
    except Exception as ex:
        log.warning("HandleUpdate error: " + str(ex))

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
    update = request.get_json(force=True) or {}
    threading.Thread(target=handle_update, args=(update,), daemon=True).start()
    return jsonify({"status": "ok"})

# ---------- Точка входа ----------
if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--read-book":
        r = read_book_one(sys.argv[2])
        if r is None:
            print("null")
        else:
            print(json.dumps(r, ensure_ascii=False))
        sys.exit(0)

    threading.Thread(target=warmup_books, daemon=True).start()
    log.info(f"Сервер стартует сразу, книги грузятся в фоне. Модель: {MODEL}")

    try:
        webhook_url = f"{PUBLIC_URL}/webhook"
        log.info(f"Устанавливаю вебхук: {webhook_url}")
        url = f"{API_BASE}setWebhook?url={urllib.parse.quote(webhook_url)}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            log.info(f"setWebhook ответ: {resp.read().decode()}")
    except Exception as e:
        log.warning(f"Ошибка установки вебхука: {e}")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))