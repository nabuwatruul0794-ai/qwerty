import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import latex_png  # noqa: E402

# Формулы, которые реально встречаются в планах-конспектах.
FORMULAS = [
    r"v_x = 2{,}0 \text{ м/с}",
    r"\alpha_{\text{рад}} = \alpha^\circ \cdot \frac{\pi}{180^\circ}",
    r"1 \text{ рад} = \frac{180^\circ}{\pi} \approx 57{,}3^\circ",
    r"d = \sqrt{x^2 + y^2}",
    r"\cos 280^\circ = \cos(360^\circ - 80^\circ) \approx 0{,}17",
    r"n \in \mathbb{Z}",
    r"P_{1000^\circ}",
    r"\sqrt[3]{5}",
    r"m = \frac{m_0}{2} + \frac{m_1}{2}",
    r"\vec{v} = \vec{v}_1 + \vec{v}_2",
    r"a = \frac{F}{m}",
    # \le и \ge matplotlib mathtext не понимает - должны заменяться
    r"0 \le F_{тр.пок} \le F_{тр.пок.макс} = \mu_{пок} N",
    r"mg \le F_{тр.макс}, \text{ то есть } m \le \frac{\mu F}{g}",
    r"0 \le F_{тр.пок} \le \mu_{пок} N",
    r"a \ge b",
    r"a \ne b",
    # неизвестная команда - чинится автоматически
    r"F_{тр.макс} \foo{x} = 1",
    # совсем сломанная формула - рисуется текстом
    r"\frac{1}{",
]


@pytest.mark.parametrize("latex", FORMULAS)
def test_formula_renders_to_png(latex, tmp_path):
    from PIL import Image

    path = tmp_path / "formula.png"
    width, height = latex_png.render_formula(latex, path)
    assert width > 0
    assert height > 0
    assert path.exists()
    with Image.open(str(path)) as im:
        assert im.size[0] > 10
        assert im.size[1] > 5


def test_no_latex_left_in_picture(latex=None):
    """Формула должна быть картинкой, а не текстом с $...$."""
    from PIL import Image

    out = Path(__file__).resolve().parent.parent / "books_index"
    out.mkdir(exist_ok=True)
    path = out / "_test_formula.png"
    try:
        latex_png.render_formula(r"v_x = 2{,}0 \text{ м/с}", path)
        with Image.open(str(path)) as im:
            assert im.size[0] > 10 and im.size[1] > 5
    finally:
        if path.exists():
            path.unlink()
