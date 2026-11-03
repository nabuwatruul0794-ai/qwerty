"""Конфиг gunicorn для Render (бесплатный план).

Кол-во воркеров берём из BOTTG_WORKERS, а не из WEB_CONCURRENCY:
Render сам выставляет WEB_CONCURRENCY=1 по числу доступных CPU.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = int(os.environ.get("BOTTG_WORKERS", "2"))
threads = int(os.environ.get("BOTTG_THREADS", "4"))
timeout = int(os.environ.get("BOTTG_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
preload_app = False
