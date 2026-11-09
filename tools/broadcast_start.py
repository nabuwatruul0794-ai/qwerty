"""Рассылает стартовое сообщение всем, кто пользовался ботом.

От кого получатели:
  * ALLOWED_USERS из bottg.py - бот отвечает только этим людям,
    поэтому это и есть весь список тех, кто им пользовался;
  * текст сообщения - tools/start_message.json (тот же, что шлёт /start);
  * кнопки выбора книги - из books_index/manifest.json, в том же порядке,
    что и в боте (предмет -> класс).

Запуск:
    python tools/broadcast_start.py            # реальная рассылка
    python tools/broadcast_start.py --dry-run  # только показать, что будет отправлено

Токен берётся из переменной окружения TELEGRAM_BOT_TOKEN или из .env в корне проекта.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.telegram.org"

SUBJECT_RANK = {"fizika": 0, "informatika": 1, "algebra": 2, "geometriya": 3, "matem": 4}
SUBJECT_SHORT_DEFAULT = {"fizika": "Физ", "informatika": "Инф", "algebra": "Алг",
                         "geometriya": "Гео", "matem": "Мат"}


def load_env(path: Path) -> dict:
    data = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


ENV = {**load_env(ROOT / ".env"), **os.environ}
TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID = int(ENV.get("OWNER_ID", "0") or 0)


def tg(method: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{API}/bot{TOKEN}/{method}", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        return json.loads(ex.read().decode("utf-8", "replace") or "{}")


def parse_book_name(name: str):
    base = name
    for suffix in (".pdf.pdf", ".pdf", ".txt.gz", ".txt"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    if "-" in base:
        subject, _, grade = base.partition("-")
        if grade.isdigit():
            return subject.lower(), grade
    return base.lower(), ""


def book_order() -> list:
    """Книги в том же порядке, что и в боте: предмет -> класс."""
    manifest = ROOT / "books_index" / "manifest.json"
    names = list(json.loads(manifest.read_text(encoding="utf-8")).keys()) if manifest.exists() else []
    return sorted(names, key=lambda n: (SUBJECT_RANK.get(parse_book_name(n)[0], 5),
                                        int(parse_book_name(n)[1] or 0), n))


def books_keyboard() -> dict:
    cfg = json.loads((ROOT / "tools" / "start_message.json").read_text(encoding="utf-8"))
    subject_short = {**SUBJECT_SHORT_DEFAULT, **(cfg.get("subject_short") or {})}
    buttons = []
    for i, name in enumerate(book_order()):
        subject, grade = parse_book_name(name)
        label = subject_short.get(subject, subject[:4])
        buttons.append({"text": f"{label} {grade}" if grade else label,
                        "callback_data": f"bk:{i}"})
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([{"text": cfg.get("cancel_label", "Cancel"), "callback_data": "bkx"}])
    return {"inline_keyboard": rows}


def allowed_users() -> list:
    """Список пользователей берётся из самого бота (ALLOWED_USERS в bottg.py)."""
    source = (ROOT / "bottg.py").read_text(encoding="utf-8")
    match = re.search(r"ALLOWED_USERS\s*=\s*\{([^}]*)\}", source, re.S)
    ids = set()
    if match:
        ids.update(int(x) for x in re.findall(r"\d{4,}", match.group(1)))
    if OWNER_ID:
        ids.add(OWNER_ID)
    return sorted(ids)


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    if not TOKEN:
        print("Нет TELEGRAM_BOT_TOKEN (проверь .env)")
        return 1

    text = json.loads((ROOT / "tools" / "start_message.json").read_text(encoding="utf-8"))["text"]
    markup = books_keyboard()
    targets = allowed_users()

    me = tg("getMe", {})
    if not me.get("ok"):
        print("Не удалось авторизоваться в Telegram:", str(me)[:200])
        return 1
    print(f"Бот: @{me['result'].get('username')}  |  получателей: {len(targets)}")
    if dry_run:
        print("DRY RUN, ничего не отправлено")
        return 0

    for chat_id in targets:
        chat = tg("getChat", {"chat_id": chat_id})
        if not chat.get("ok"):
            print(f"  {chat_id}: чат недоступен - {chat.get('description', '?')}")
            continue
        if chat["result"].get("type") != "private":
            print(f"  {chat_id}: не личка ({chat['result'].get('type')}), пропуск")
            continue
        resp = tg("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": markup})
        if resp.get("ok"):
            print(f"  {chat_id}: отправлено (message_id={resp['result'].get('message_id')})")
        else:
            print(f"  {chat_id}: ошибка - {resp.get('description', '?')}")
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
