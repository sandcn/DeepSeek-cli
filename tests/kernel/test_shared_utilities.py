"""跨模块共享工具单一真源守卫。

历史问题：``strip_ansi`` 与 TOC 树形连接符在多层各有一份实现，覆盖范围
互不相同（``clawbot`` 仅剥 CSI、``renderer`` 剥 CSI+OSC+双字符转义），同一
文本经不同路径清洗结果可能不一致；TOC 连接符逻辑则在 Rich 路径与自绘 ANSI
路径各有一份逐字符相同的拷贝。

本测试固化收敛结果：

1. ``src._ansi_text`` 为 ANSI 处理的唯一实现，三层均 re-export 同一对象；
2. 三层对同一输入产出完全一致（含 OSC 8 超链接、ECMA-48 中间字节、双字符
   转义、``None`` 输入）；
3. ``src.renderer._toc_logic.build_toc_connectors`` 为 TOC 连接符唯一真源，
   两条渲染路径引用同一对象。
"""

from __future__ import annotations

import pytest

from src import _ansi_text


# ── ANSI：单一真源 ────────────────────────────────────────


def test_strip_ansi_is_same_object_across_layers():
    from src.clawbot.render import strip_ansi as clawbot_strip
    from src.renderer.ansi.helpers import strip_ansi as renderer_strip
    from src.tui.ink._ansi_utils import strip_ansi as tui_strip

    assert clawbot_strip is _ansi_text.strip_ansi
    assert renderer_strip is _ansi_text.strip_ansi
    assert tui_strip is _ansi_text.strip_ansi


def test_ansi_re_is_same_object_across_layers():
    from src.renderer.ansi.helpers import _ANSI_RE as renderer_re
    from src.tui.ink._ansi_utils import _ANSI_RE as tui_re

    assert renderer_re is _ansi_text.ANSI_RE
    assert tui_re is _ansi_text.ANSI_RE


def test_has_ansi_is_same_object():
    from src.tui.ink._ansi_utils import has_ansi

    assert has_ansi is _ansi_text.has_ansi


def test_all_layers_produce_identical_output():
    from src.clawbot.render import strip_ansi as clawbot_strip
    from src.renderer.ansi.helpers import strip_ansi as renderer_strip
    from src.tui.ink._ansi_utils import strip_ansi as tui_strip

    cases = [
        "\x1b[31mred\x1b[0m",
        "\x1b]8;;http://example.com\x07link\x1b]8;;\x07",
        "\x1b[38:2::255:0:0mtruecolor\x1b[0m",
        "\x1b[!pDECSTR\x1b[>cDA",
        "\x1b[3~keyseq",
        "plain text",
        "",
    ]
    for text in cases:
        results = {clawbot_strip(text), renderer_strip(text), tui_strip(text)}
        assert len(results) == 1, f"三层输出不一致: {text!r} -> {results}"


@pytest.mark.parametrize("text,expected", [
    ("\x1b[31mred\x1b[0m", "red"),
    ("\x1b[38:2::255:0:0mX\x1b[0m", "X"),
    ("\x1b[!pY", "Y"),
    ("\x1b[>cZ", "Z"),
    ("\x1b[3~K", "K"),
    ("\x1b]8;;http://x\x07txt\x1b]8;;\x07", "txt"),
    ("\x1b\\W", "W"),
    ("plain", "plain"),
    ("", ""),
    (None, ""),
])
def test_strip_ansi_coverage(text, expected):
    assert _ansi_text.strip_ansi(text) == expected


def test_strip_ansi_non_string_input():
    assert _ansi_text.strip_ansi(123) == "123"


def test_has_ansi_fast_path():
    assert _ansi_text.has_ansi("\x1b[0m") is True
    assert _ansi_text.has_ansi("plain") is False
    assert _ansi_text.has_ansi("") is False
    assert _ansi_text.has_ansi(None) is False


def test_ansi_module_has_no_third_party_dependency():
    """``src._ansi_text`` 必须零第三方依赖（TUI ANSI 路径禁止 Rich）。"""
    import ast
    from pathlib import Path

    src = Path(_ansi_text.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module != "__future__":
                imported.add(node.module.split(".")[0])
    assert imported <= {"re"}, f"出现非标准库依赖: {imported}"

# ── TOC 连接符：单一真源 ──────────────────────────────────


def test_toc_connectors_shared_between_render_paths():
    from src.renderer._rendering._special import _build_toc_connectors as rich_impl
    from src.renderer._toc_logic import build_toc_connectors
    from src.renderer.ansi.toc import _build_toc_connectors as ansi_impl

    assert rich_impl is build_toc_connectors
    assert ansi_impl is build_toc_connectors


def test_build_toc_connectors_empty():
    from src.renderer._toc_logic import build_toc_connectors

    assert build_toc_connectors([]) == []


def test_build_toc_connectors_flat_levels():
    from src.renderer._toc_logic import CONN_LAST, build_toc_connectors

    toc = [{"level": 1, "text": "a"}, {"level": 1, "text": "b"}]
    out = build_toc_connectors(toc)
    assert out[0]["prefix"] == "┣━ "
    assert out[1]["prefix"] == "┗━ "
    assert CONN_LAST == "┗━ "


def test_build_toc_connectors_nested_levels():
    from src.renderer._toc_logic import build_toc_connectors

    toc = [
        {"level": 1, "text": "a"},
        {"level": 2, "text": "a.1"},
        {"level": 2, "text": "a.2"},
        {"level": 1, "text": "b"},
    ]
    out = build_toc_connectors(toc)
    assert out[0]["prefix"] == "┣━ "
    assert out[1]["prefix"] == "┃  ┣━ "
    assert out[2]["prefix"] == "┃  ┗━ "
    assert out[3]["prefix"] == "┗━ "


def test_build_toc_connectors_preserves_extra_keys():
    from src.renderer._toc_logic import build_toc_connectors

    toc = [{"level": 1, "text": "a", "anchor": "#a"}]
    out = build_toc_connectors(toc)
    assert out[0]["anchor"] == "#a"
    assert out[0]["text"] == "a"


def test_toc_logic_has_no_third_party_dependency():
    import ast
    from pathlib import Path

    from src.renderer import _toc_logic

    src = Path(_toc_logic.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        assert not isinstance(node, (ast.Import, ast.ImportFrom)), "共享纯逻辑不应有导入"
