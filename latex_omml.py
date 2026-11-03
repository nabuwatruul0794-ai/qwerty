"""Перевод LaTeX-формул в родные формулы Word (OMML).

Поддерживается набор, который использует модель в плане-конспекте:
  \\frac{a}{b}, x_{i}, x^{2}, x_{i}^{2}, \\sqrt{x}, \\sqrt[3]{x}, \\text{...},
  греческие буквы, операторы (\\cdot, \\approx, \\le, \\ge, \\ne, \\in, ...),
  группы {...}, десятичная запятая {,}, \\mathbb{Z}.

Схема OMML: m:oMath внутри w:p, элементы m:f (дробь), m:sSub/m:sSup/m:sSubSup
(индексы и степени), m:rad (корень), m:r (символ).
"""
import re

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

SYMBOLS = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "approx": "≈", "neq": "≠", "ne": "≠", "leq": "≤", "le": "≤",
    "geq": "≥", "ge": "≥", "ll": "≪", "gg": "≫", "in": "∈", "notin": "∉",
    "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩", "to": "→",
    "rightarrow": "→", "Rightarrow": "⇒", "leftarrow": "←", "infty": "∞",
    "ldots": "…", "dots": "…", "circ": "°", "degree": "°", "perp": "⊥",
    "parallel": "∥", "angle": "∠", "star": "★", "prime": "′", "percent": "%",
    "quad": " ", "qquad": "  ", ",": " ", ";": " ", "!": "",
    "left": "", "right": "", "displaystyle": "", "limits": "", "cdot ": "·",
}

DOUBLE_STRUCK = {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ",
                 "R": "ℝ", "Z": "ℤ"}


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


class _Parser:
    """Разбор LaTeX в дерево узлов."""

    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def items(self, stop_brace: bool = False):
        out = []
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == "}" and stop_brace:
                break
            if ch in "^_":
                self.i += 1
                kind = "sup" if ch == "^" else "sub"
                base = out.pop() if out else ("t", "")
                out.append(("script", kind, base, self.atom()))
                continue
            if ch == "\\":
                out.append(self.command())
                continue
            if ch == "{":
                self.i += 1
                inner = self.items(stop_brace=True)
                if self.i < len(self.s) and self.s[self.i] == "}":
                    self.i += 1
                out.append(("g", inner))
                continue
            if ch == "$":
                self.i += 1
                continue
            self.i += 1
            out.append(("t", ch))
        return out

    def atom(self):
        """Один аргумент: {...}, команда или символ."""
        if self.i < len(self.s) and self.s[self.i] == "{":
            self.i += 1
            inner = self.items(stop_brace=True)
            if self.i < len(self.s) and self.s[self.i] == "}":
                self.i += 1
            return ("g", inner)
        if self.i < len(self.s) and self.s[self.i] == "\\":
            return self.command()
        if self.i < len(self.s):
            ch = self.s[self.i]
            self.i += 1
            return ("t", ch)
        return ("t", "")

    def raw_group(self) -> str:
        """Содержимое {...} без разбора команд (для \\text{...})."""
        if self.i >= len(self.s) or self.s[self.i] != "{":
            return ""
        self.i += 1
        depth = 1
        buf = []
        while self.i < len(self.s) and depth:
            ch = self.s[self.i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if not depth:
                    self.i += 1
                    break
            buf.append(ch)
            self.i += 1
        return "".join(buf)

    def command(self):
        self.i += 1
        start = self.i
        while self.i < len(self.s) and self.s[self.i].isalpha():
            self.i += 1
        name = self.s[start:self.i]
        if name == "frac":
            return ("frac", self.atom(), self.atom())
        if name == "sqrt":
            deg = None
            if self.i < len(self.s) and self.s[self.i] == "[":
                self.i += 1
                deg = []
                while self.i < len(self.s) and self.s[self.i] != "]":
                    deg.append(self.atom())
                    if self.i < len(self.s) and self.s[self.i] == "]":
                        break
                if self.i < len(self.s) and self.s[self.i] == "]":
                    self.i += 1
            return ("sqrt", deg, self.atom())
        if name == "text":
            return ("t", self.raw_group(), True)
        if name == "mathbb":
            raw = self.raw_group()
            return ("t", "".join(DOUBLE_STRUCK.get(c, c) for c in raw))
        if name in GREEK:
            return ("t", GREEK[name])
        if name in SYMBOLS:
            return ("t", SYMBOLS[name])
        if name == "vec" or name == "bar" or name == "hat" or name == "overline":
            return self.atom()
        return ("t", "")


def _run(text: str, normal: bool = False) -> str:
    if not text:
        return ""
    if normal:
        rpr = ('<m:rPr><m:nor/></m:rPr>'
               '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/></w:rPr>')
    else:
        rpr = ('<w:rPr><w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/></w:rPr>')
    return f'<m:r>{rpr}<m:t xml:space="preserve">{_esc(text)}</m:t></m:r>'


def _nodes(node):
    """Список узлов для рендера: у группы - её содержимое, иначе сам узел."""
    if node[0] == "g":
        return node[1]
    return [node]


def _render(items) -> str:
    out = []
    for node in items:
        kind = node[0]
        if kind == "t":
            out.append(_run(node[1], bool(len(node) > 2 and node[2])))
        elif kind == "g":
            out.append(_render(node[1]))
        elif kind == "frac":
            out.append("<m:f><m:fPr><m:type m:val=\"bar\"/></m:fPr>"
                       f"<m:num>{_render(_nodes(node[1]))}</m:num>"
                       f"<m:den>{_render(_nodes(node[2]))}</m:den></m:f>")
        elif kind == "sqrt":
            deg = node[1]
            if deg:
                deg_xml = f"<m:deg>{_render(deg)}</m:deg>"
                hide = '<m:degHide m:val="0"/>'
            else:
                deg_xml = "<m:deg/>"
                hide = '<m:degHide m:val="1"/>'
            out.append(f"<m:rad><m:radPr>{hide}</m:radPr>{deg_xml}"
                       f"<m:e>{_render(_nodes(node[2]))}</m:e></m:rad>")
        elif kind == "script":
            _, which, base, arg = node
            base_xml = _render(_nodes(base))
            arg_xml = _render(_nodes(arg))
            if which == "sup":
                out.append(f"<m:sSup><m:e>{base_xml}</m:e><m:sup>{arg_xml}</m:sup></m:sSup>")
            else:
                out.append(f"<m:sSub><m:e>{base_xml}</m:e><m:sub>{arg_xml}</m:sub></m:sSub>")
    return "".join(out)


def latex_to_omml(latex: str) -> str:
    """LaTeX -> XML формулы OMML (без внешней обёртки m:oMath)."""
    text = latex.strip()
    if text.startswith("$$") and text.endswith("$$") and len(text) > 4:
        text = text[2:-2]
    parser = _Parser(text)
    return _render(parser.items())


def omml_element(latex: str):
    """Готовый XML-элемент m:oMath для вставки в абзац docx."""
    from docx.oxml import parse_xml
    body = latex_to_omml(latex)
    if not body:
        raise ValueError("пустая формула")
    xml = (f'<m:oMath xmlns:m="{M_NS}" xmlns:w="{W_NS}">'
           f"{body}</m:oMath>")
    return parse_xml(xml)


MATH_SPAN_RE = re.compile(r"\$\$([^$]+)\$\$|\$([^$]+)\$")
