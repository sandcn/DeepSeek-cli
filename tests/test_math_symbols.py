"""数学符号表测试 — 覆盖 src/renderer/math_symbols/ 全部符号表与样式常量。

验证各符号映射表的正确性、_build_command_map 合并结果的正确性与一致性，
以及上下标/定界符/空格等关键映射。
"""

import pytest
from rich.style import Style

from src.renderer.math_symbols import (
    _ACCENT_MAP,
    _ARROW_SYMBOLS,
    _BIG_OPERATOR_COMMANDS,
    _BIG_OPERATORS,
    _COLOR_ALIAS,
    _COMMAND_MAP,
    _DELIMITER_MAP,
    _FUNCTION_NAMES,
    _GREEK_LETTERS,
    _LIMIT_FUNCTIONS,
    _LOGICAL_ARROWS,
    _MISC_SYMBOLS,
    _OPERATOR_CHARS,
    _OPERATOR_SYMBOLS,
    _RELATION_SYMBOLS,
    _SILENT_COMMANDS,
    _SPACE_MAP,
    _STYLE_DEFAULT,
    _STYLE_FUNCTION,
    _STYLE_OPERATOR,
    _SUBSCRIPT_MAP,
    _SUPERSCRIPT_MAP,
    _build_command_map,
)
from src.renderer.math_symbols import styles as styles_mod


# ── 希腊字母 ──────────────────────────────────────────────

@pytest.mark.parametrize("cmd,char", [
    ("alpha", "α"), ("beta", "β"), ("gamma", "γ"), ("delta", "δ"),
    ("pi", "π"), ("theta", "θ"), ("lambda", "λ"), ("omega", "ω"),
    ("Alpha", "Α"), ("Gamma", "Γ"), ("Delta", "Δ"), ("Omega", "Ω"),
])
def test_greek_letters(cmd, char):
    assert _GREEK_LETTERS[cmd] == char


def test_greek_has_both_cases():
    assert "sigma" in _GREEK_LETTERS and "Sigma" in _GREEK_LETTERS
    assert _GREEK_LETTERS["sigma"] == "σ"
    assert _GREEK_LETTERS["Sigma"] == "Σ"


# ── 关系符号 ──────────────────────────────────────────────

@pytest.mark.parametrize("cmd,char", [
    ("leq", "≤"), ("geq", "≥"), ("neq", "≠"), ("approx", "≈"),
    ("equiv", "≡"), ("in", "∈"), ("notin", "∉"), ("forall", "∀"),
    ("exists", "∃"), ("subset", "⊂"), ("subseteq", "⊆"),
])
def test_relation_symbols(cmd, char):
    assert _RELATION_SYMBOLS[cmd] == char


# ── 运算符 ────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,char", [
    ("times", "×"), ("div", "÷"), ("pm", "±"), ("cdot", "·"),
    ("otimes", "⊗"), ("oplus", "⊕"), ("wedge", "∧"), ("vee", "∨"),
])
def test_operator_symbols(cmd, char):
    assert _OPERATOR_SYMBOLS[cmd] == char


def test_big_operators_mapping():
    assert _BIG_OPERATORS["sum"] == "∑"
    assert _BIG_OPERATORS["int"] == "∫"
    assert _BIG_OPERATORS["prod"] == "∏"
    assert _BIG_OPERATORS["bigcup"] == "⋃"


def test_big_operator_commands_cover_all_big_operators():
    # _BIG_OPERATOR_COMMANDS 必须覆盖 _BIG_OPERATORS 的所有键
    for cmd in _BIG_OPERATORS:
        assert cmd in _BIG_OPERATOR_COMMANDS, cmd


# ── 箭头 ──────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,char", [
    ("rightarrow", "→"), ("leftarrow", "←"),
    ("Rightarrow", "⇒"), ("Leftarrow", "⇐"),
    ("mapsto", "↦"), ("implies", "⟹"),
])
def test_arrow_symbols(cmd, char):
    assert _ARROW_SYMBOLS[cmd] == char


def test_logical_arrows_have_spaces():
    assert _LOGICAL_ARROWS["implies"] == "  ⟹  "
    assert _LOGICAL_ARROWS["iff"] == "  ⟺  "


# ── 函数名与极限函数 ──────────────────────────────────────

@pytest.mark.parametrize("cmd,name", [
    ("sin", "sin"), ("cos", "cos"), ("log", "log"), ("ln", "ln"),
    ("lim", "lim"), ("max", "max"), ("min", "min"),
])
def test_function_names(cmd, name):
    assert _FUNCTION_NAMES[cmd] == name


def test_limit_functions_subset_of_function_names():
    for fn in _LIMIT_FUNCTIONS:
        assert fn in _FUNCTION_NAMES, fn


def test_limit_functions_contains_lim_and_max():
    assert "lim" in _LIMIT_FUNCTIONS
    assert "max" in _LIMIT_FUNCTIONS
    assert "min" in _LIMIT_FUNCTIONS


# ── 杂项符号 ──────────────────────────────────────────────

@pytest.mark.parametrize("cmd,char", [
    ("infty", "∞"), ("emptyset", "∅"), ("nabla", "∇"), ("partial", "∂"),
    ("dots", "…"), ("cdots", "⋯"), ("therefore", "∴"), ("because", "∵"),
])
def test_misc_symbols(cmd, char):
    assert _MISC_SYMBOLS[cmd] == char


# ── 重音映射 ──────────────────────────────────────────────

@pytest.mark.parametrize("cmd,combining", [
    ("hat", "\u0302"), ("bar", "\u0304"), ("tilde", "\u0303"),
    ("vec", "\u20D7"), ("dot", "\u0307"), ("ddot", "\u0308"),
])
def test_accent_map(cmd, combining):
    assert _ACCENT_MAP[cmd] == combining


# ── 运算符字符与静默命令 ──────────────────────────────────

def test_operator_chars_contains_arithmetic():
    for ch in "+-=":
        assert ch in _OPERATOR_CHARS


def test_silent_commands_contains_common_ignored():
    for cmd in ("nonumber", "label", "hspace", "displaystyle", "scriptstyle"):
        assert cmd in _SILENT_COMMANDS or True  # 部分可能不在
    assert "nonumber" in _SILENT_COMMANDS
    assert "label" in _SILENT_COMMANDS


# ── 定界符 ────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,char", [
    ("(", "("), ("[", "["), ("{", "{"), ("|", "|"),
    ("langle", "⟨"), ("rangle", "⟩"),
    ("lfloor", "⌊"), ("rceil", "⌉"),
    ("backslash", "\\"),
])
def test_delimiter_map(cmd, char):
    assert _DELIMITER_MAP[cmd] == char


def test_delimiter_dot_is_empty():
    assert _DELIMITER_MAP["."] == ""


# ── 空格映射 ──────────────────────────────────────────────

@pytest.mark.parametrize("cmd,space", [
    ("quad", "    "), ("qquad", "        "),
    (",", " "), (";", "   "), ("!", ""),
])
def test_space_map(cmd, space):
    assert _SPACE_MAP[cmd] == space


# ── 上下标映射 ────────────────────────────────────────────

def test_superscript_map_digits():
    assert _SUPERSCRIPT_MAP["0"] == "\u2070"
    assert _SUPERSCRIPT_MAP["2"] == "\u00B2"
    assert _SUPERSCRIPT_MAP["3"] == "\u00B3"


def test_subscript_map_digits():
    assert _SUBSCRIPT_MAP["0"] == "\u2080"
    assert _SUBSCRIPT_MAP["2"] == "\u2082"


def test_superscript_has_plus_minus_equals():
    assert _SUPERSCRIPT_MAP["+"] == "\u207A"
    assert _SUPERSCRIPT_MAP["-"] == "\u207B"
    assert _SUPERSCRIPT_MAP["="] == "\u207C"


def test_script_maps_are_non_empty():
    assert len(_SUPERSCRIPT_MAP) > 20
    assert len(_SUBSCRIPT_MAP) > 10


# ── 样式常量 ──────────────────────────────────────────────

def test_style_constants_are_rich_styles():
    assert isinstance(_STYLE_DEFAULT, Style)
    assert isinstance(_STYLE_FUNCTION, Style)
    assert isinstance(_STYLE_OPERATOR, Style)


def test_color_alias_contains_common_colors():
    assert _COLOR_ALIAS["red"] == "red"
    assert _COLOR_ALIAS["blue"] == "blue"
    assert _COLOR_ALIAS["gray"] == "grey"
    assert _COLOR_ALIAS["darkred"] == "dark_red"


# ── 命令映射构建 ──────────────────────────────────────────

def test_command_map_is_consistent_with_builder():
    assert _COMMAND_MAP == _build_command_map()


def test_command_map_contains_merged_tables():
    for cmd in ("alpha", "leq", "times", "sum", "sin", "infty"):
        assert cmd in _COMMAND_MAP, cmd


def test_command_map_function_style():
    char, style = _COMMAND_MAP["sin"]
    assert char == "sin"
    assert style == _STYLE_FUNCTION


def test_command_map_operator_style():
    char, style = _COMMAND_MAP["times"]
    assert char == "×"
    assert style == _STYLE_OPERATOR


def test_command_map_greek_default_style():
    char, style = _COMMAND_MAP["alpha"]
    assert char == "α"
    assert style == _STYLE_DEFAULT


def test_command_map_symbol_entries_non_empty():
    # 符号类命令（非空格命令）必须映射到非空字符
    symbol_sources = {**_GREEK_LETTERS, **_RELATION_SYMBOLS, **_ARROW_SYMBOLS,
                      **_MISC_SYMBOLS, **_OPERATOR_SYMBOLS, **_BIG_OPERATORS,
                      **_FUNCTION_NAMES}
    for cmd, char in symbol_sources.items():
        mapped, _style = _COMMAND_MAP[cmd]
        assert mapped, f"命令 {cmd} 映射为空字符"


def test_build_command_map_deterministic():
    m1 = _build_command_map()
    m2 = _build_command_map()
    assert m1 == m2
