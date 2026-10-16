"""嵌套语法增强的流式预览性能回归测试。

覆盖本轮新增的嵌套能力在流式路径上的性能约束：

  - 列表项内代码块预览增量高亮（不随行数二次方增长）
  - 列表项内块级容器预览按内容缓存（无变化的帧零解析）
  - 告示块级正文预览的子解析缓存命中
  - HTML 嵌套列表解析线性（不因嵌套深度退化）
"""
from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer


def _drain(r: AnsiStreamRenderer) -> None:
    r.take_lines()
    r.take_preview_lines()


# ═══════════════════════════════════════════════════════════
# 列表项内代码块预览：增量高亮
# ═══════════════════════════════════════════════════════════


def test_list_block_code_preview_incremental(monkeypatch):
    """列表项内代码块流式预览：累计高亮行数接近总行数（非每帧整段重渲）。"""
    from src.renderer.ansi import code as _code

    total = {"n": 0}
    orig = _code.highlight_code_lines

    def spy(lines, lang="", theme="monokai", highlight_lines=None,
            start_index=1, **kwargs):
        total["n"] += len(lines)
        return orig(lines, lang, theme, highlight_lines, start_index, **kwargs)

    monkeypatch.setattr(_code, "highlight_code_lines", spy)
    r = AnsiStreamRenderer(width=80)
    r.write("- 项\n")
    r.write("\n")
    r.write("  ```python\n")
    for i in range(120):
        r.write(f"  v{i} = {i}\n")
        _drain(r)
    r.write("  ```\n")
    _drain(r)
    # 全量重渲约 120 帧 × 行数（数千）；增量应接近「每行一次」
    assert total["n"] < 600


def test_list_block_preview_cached_when_unchanged(monkeypatch):
    """列表项内块级容器预览：内容未变化的重复刷新不再子解析。"""
    r = AnsiStreamRenderer(width=80)
    r.write("- 项\n\n  ```py\n  x=1\n")
    _drain(r)
    calls = {"n": 0}
    orig = AnsiStreamRenderer._parse_list_block_preview

    def spy(self, body):
        calls["n"] += 1
        return orig(self, body)

    monkeypatch.setattr(AnsiStreamRenderer, "_parse_list_block_preview", spy)
    r._refresh_preview()
    r._refresh_preview()
    # 内容未变（缓存键命中）→ 不重新子解析
    assert calls["n"] == 0
    r.write("  y=2\n")
    r._refresh_preview()
    assert calls["n"] >= 1


def test_list_block_preview_rows_indented():
    """列表项内块级容器预览行带列表内容缩进前缀（与提交一致）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("- 项\n\n  ```py\n  x=1\n")
    rows = [ln.plain for ln in r.take_preview_lines()]
    assert any(ln.startswith("  ```py") for ln in rows)
    assert any(ln == "  x=1" for ln in rows)


def test_list_block_preview_cache_invalidated_on_width_change():
    """终端宽度变化使列表项内容器预览缓存失效（表格/代码按新宽度重排）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("- 项\n\n  | a | b |\n  |---|---|\n  | 1 | 2 |\n")
    r.take_preview_lines()
    assert r._list_block_preview_key is not None
    r.set_width(40)
    assert r._list_block_preview_key is None
    rows = [ln.plain for ln in r.take_preview_lines()]
    assert any("\u250c" in ln for ln in rows)


# ═══════════════════════════════════════════════════════════
# 告示块级正文预览：子解析缓存
# ═══════════════════════════════════════════════════════════


def test_admonition_preview_sub_parse_cached(monkeypatch):
    """告示块级正文预览：同一内容重复帧不重复子解析（命中缓存）。"""
    from src.renderer._block_parser import RegexFreeBlockParser

    r = AnsiStreamRenderer(width=80)
    r.write("> [!NOTE]\n> - a\n> - b\n")
    r._refresh_preview()  # 先填充缓存
    calls = {"n": 0}
    orig = RegexFreeBlockParser._parse_sub_blocks

    def spy(self, lines):
        calls["n"] += 1
        return orig(self, lines)

    monkeypatch.setattr(RegexFreeBlockParser, "_parse_sub_blocks", spy)
    r._refresh_preview()
    r._refresh_preview()
    assert calls["n"] == 0


# ═══════════════════════════════════════════════════════════
# HTML 嵌套列表解析
# ═══════════════════════════════════════════════════════════


def test_html_nested_list_linear():
    """深/宽嵌套 HTML 列表解析在合理时间内完成（线性扫描，无回溯爆炸）。"""
    import time

    from src.renderer._block_parser import _parse_html_list_items

    parts = ["<ul>"]
    for i in range(200):
        parts.append(f"<li>项{i}<ul><li>子{i}</li></ul></li>")
    parts.append("</ul>")
    text = "\n".join(parts)
    start = time.perf_counter()
    items = _parse_html_list_items(text, "ul")
    elapsed = time.perf_counter() - start
    assert len(items) >= 200
    assert elapsed < 1.0


def test_table_preview_cache_multiline_cells():
    """表格预览缓存：多行单元格（``<br>``）下仍复用未变化行。"""
    r = AnsiStreamRenderer(width=80)
    r.write("| a | b |\n")
    r.write("|---|---|\n")
    for i in range(60):
        r.write(f"| 行{i}<br>续 | v{i} |\n")
        _drain(r)
    r.write("\n")
    lines = [ln.plain for ln in r.take_lines()]
    assert any("\u250c" in ln for ln in lines)
