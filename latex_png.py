"""Рисуем формулы LaTeX в PNG.

Зачем: родные формулы Word (OMML) умеет показывать только Word. Мобильные
просмотрщики (превью Telegram, Google Docs на телефоне) их не рисуют и просто
вырезают. Картинка с формулой отображается везде одинаково.

Используется matplotlib mathtext - установленный LaTeX не нужен.

mathtext не знает часть команд (\\le, \\ge, ...). Такие команды заменяем на
поддерживаемые, а если не помогло - чиним формулу автоматически по тексту
ошибки. Совсем нечитаемую формулу рисуем обычным текстом, но тоже картинкой.
"""
import re
from pathlib import Path

# Замены, которые mathtext не понимает.
_CMD_FIXES = {
    r"\le": r"\leq",
    r"\ge": r"\geq",
    r"\ne": r"\neq",
    r"\lt": "<",
    r"\gt": ">",
    r"\displaystyle": "",
    r"\limits": "",
    r"\nolimits": "",
    r"\operatorname": r"\mathrm",
    r"\leqslant": r"\leq",
    r"\geqslant": r"\geq",
}

# Команды, которые нужно просто убрать.
_DROP = {r"\displaystyle", r"\limits", r"\nolimits", r"\!", r"\,", r"\;", r"\:"}

# Замены для текстовой запасной версии.
_TEXT_FIXES = [
    (re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}"), r"(\1)/(\2)"),
    (re.compile(r"\\sqrt\s*\[\s*(\d+)\s*\]\s*\{([^{}]*)\}"), r"^[\1] \2"),
    (re.compile(r"\\sqrt\s*\{([^{}]*)\}"), r"√\1"),
    (re.compile(r"\\text\s*\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\\(cdot|times)\b"), "·"),
    (re.compile(r"\\(le|leq)\b"), "≤"),
    (re.compile(r"\\(ge|geq)\b"), "≥"),
    (re.compile(r"\\(ne|neq)\b"), "≠"),
    (re.compile(r"\\(approx|sim)\b"), "≈"),
    (re.compile(r"\\(pm|mp)\b"), "±"),
    (re.compile(r"\\mu\b"), "μ"),
    (re.compile(r"\\alpha\b"), "α"),
    (re.compile(r"\\beta\b"), "β"),
    (re.compile(r"\\gamma\b"), "γ"),
    (re.compile(r"\\theta\b"), "θ"),
    (re.compile(r"\\lambda\b"), "λ"),
    (re.compile(r"\\pi\b"), "π"),
    (re.compile(r"\\omega\b"), "ω"),
    (re.compile(r"\\Delta\b"), "Δ"),
    (re.compile(r"\\infty\b"), "∞"),
    (re.compile(r"\\circ\b"), "°"),
    (re.compile(r"\\[a-zA-Z]+"), " "),
    (re.compile(r"[{}]"), ""),
]


def _normalize(latex: str) -> str:
    text = latex.strip().replace("$$", "").replace("$", "")
    text = re.sub(r"\\left\s*", "", text)
    text = re.sub(r"\\right\s*", "", text)
    text = text.replace("{,}", ",")
    for cmd, repl in _CMD_FIXES.items():
        text = text.replace(cmd, repl)
    for cmd in _DROP:
        text = text.replace(cmd, " ")
    return text.strip()


def _unknown_command(error: str) -> str:
    """Достаёт команду, на которую пожаловался mathtext."""
    m = re.search(r"Unknown symbol:\s*(\\[a-zA-Z]+|\\[^a-zA-Z\s])", error)
    return m.group(1) if m else ""


def _de_latex(text: str) -> str:
    plain = text
    for pattern, repl in _TEXT_FIXES:
        plain = pattern.sub(repl, plain)
    return re.sub(r"\s{2,}", " ", plain).strip()


def _render_plain_png(text: str, path: Path, font_size: float = 13.0, dpi: int = 300):
    """Запасной вариант: рисуем формулу обычным текстом (но тоже картинкой)."""
    import matplotlib
    from PIL import Image, ImageDraw, ImageFont

    font_path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    font = ImageFont.truetype(str(font_path), max(8, int(font_size * dpi / 72)))
    plain = _de_latex(text)
    probe = Image.new("RGBA", (10, 10), (255, 255, 255, 0))
    bbox = ImageDraw.Draw(probe).textbbox((0, 0), plain, font=font)
    w = max(1, bbox[2] - bbox[0] + 10)
    h = max(1, bbox[3] - bbox[1] + 10)
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    ImageDraw.Draw(img).text((5 - bbox[0], 5 - bbox[1]), plain, font=font, fill="#111111")
    img.save(str(path), "PNG")
    return w / dpi, h / dpi


def render_formula(latex: str, path: Path, font_size: float = 13.0, dpi: int = 300):
    """Рисует формулу в PNG. Возвращает (ширина, высота) в дюймах."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import mathtext
    from matplotlib.font_manager import FontProperties
    from PIL import Image

    text = _normalize(latex)
    if not text:
        raise ValueError("пустая формула")

    for _attempt in range(8):
        try:
            mathtext.math_to_image(f"${text}$", str(path), prop=FontProperties(size=font_size),
                                   dpi=dpi, format="png", color="#111111")
            break
        except Exception as ex:
            cmd = _unknown_command(str(ex))
            if not cmd:
                # формулу не починить - рисуем обычным текстом, но картинкой
                return _render_plain_png(text, path, font_size, dpi)
            repl = _CMD_FIXES.get(cmd)
            if repl is None:
                repl = "" if cmd in _DROP else " "
            text = text.replace(cmd, repl)
    else:
        return _render_plain_png(text, path, font_size, dpi)

    with Image.open(str(path)) as im:
        w_px, h_px = im.size
    return w_px / dpi, h_px / dpi
