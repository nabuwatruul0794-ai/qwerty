"""Рисуем формулы LaTeX в PNG.

Зачем: родные формулы Word (OMML) умеет показывать только Word. Мобильные
просмотрщики (превью Telegram, Google Docs на телефоне) их не рисуют и просто
вырезают. Картинка с формулой отображается везде одинаково.

Используется matplotlib mathtext - установленный LaTeX не нужен.
"""
import re
from pathlib import Path

# matplotlib mathtext не понимает эти конструкции - приводим к тому, что понимает.
_PREP = [
    (re.compile(r"\\left\s*"), ""),
    (re.compile(r"\\right\s*"), ""),
    (re.compile(r"\\[,!;:]|\\ "), " "),
    (re.compile(r"\{,\}"), ","),
]


def _normalize(latex: str) -> str:
    text = latex.strip().replace("$$", "").replace("$", "")
    for pattern, repl in _PREP:
        text = pattern.sub(repl, text)
    return text.strip()


def render_formula(latex: str, path: Path, font_size: float = 13.0, dpi: int = 300):
    """Рисует формулу в PNG. Возвращает (ширина, высота) в дюймах.

    Бросает исключение, если формулу отрисовать не удалось.
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import mathtext
    from matplotlib.font_manager import FontProperties
    from PIL import Image

    text = _normalize(latex)
    if not text:
        raise ValueError("пустая формула")

    mathtext.math_to_image(f"${text}$", str(path), prop=FontProperties(size=font_size),
                           dpi=dpi, format="png", color="#111111")

    with Image.open(str(path)) as im:
        w_px, h_px = im.size
    return w_px / dpi, h_px / dpi
