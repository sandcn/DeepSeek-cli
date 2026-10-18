"""ANSI 流式 Markdown 渲染修复与语法扩展（第二十二批）测试。

覆盖本轮三类改动：

  1. **渲染错误**
     - 超长格式活动行（> ``_FORMAT_ACTIVE_MAX_CHARS``）预览冻结：窗口化后
       按窗口长度节流永远命中，最新内容要等块闭合才出现——现窗口化路径
       逐帧渲染当前窗口；
     - Mermaid 长图（≥ ``_PREVIEW_THROTTLE_FULL_LIMIT`` 行）活动行滞留：
       行数不变时直接复用上一帧图形，正在输入的节点/边要等换行才显示——
       现按活动行字符增长节流刷新；
     - Front Matter 预览省略行数滞后：正文截断为固定前缀后新增行只改变
       ``preview_dropped``，节流命中时提示行数字停留旧值——现仅替换提示行。
  2. **性能**
     - 容器块（告示 / ``<details>`` / fenced div）预览头行按参数缓存行对象
       （跨帧身份稳定 → UI styled 缓存命中 + ``_sanitize_lines`` 保持
       「已检查行构成前缀」不变式）；
     - ``_preview_src_lines`` 的最后换行位置增量缓存 + 超长内容跳过全文
       前缀比较（修复前超长内容每帧仍做一次全文 ``startswith``）。
  3. **语法扩展**
     - Mermaid 新增 6 类图渲染：packet / block / quadrantChart /
       requirementDiagram / xychart / sankey；
     - 延迟围栏关键词表补齐 ``packet-beta`` / ``block-beta`` /
       ``xychart`` / ``xychart-beta`` / ``sankey`` / ``sankey-beta``。
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi._mermaid_render import render_mermaid
from src.renderer._block_parser_state import _MERMAID_KEYWORDS


# ══════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════


def _preview_plain(r: AnsiStreamRenderer) -> str:
    return "\n".join(ln.plain for ln in r.take_preview_lines())


def _render_batch(md: str, width: int = 100) -> str:
    r = AnsiStreamRenderer(width=width)
    r.write(md)
    r.close()
    return "\n".join(ln.plain for ln in r.take_lines())


def _mermaid_block(src: str) -> str:
    return "```mermaid\n" + src.strip("\n") + "\n```"


# ══════════════════════════════════════════════════════════
# 1. 超长格式活动行预览不冻结
# ══════════════════════════════════════════════════════════


class TestLongFormatActiveLinePreview:
    def test_windowed_active_line_keeps_refreshing(self):
        """超过格式活动行窗口后，最新输入内容仍能持续刷新（修复前永久冻结）。

        构造 4200 个 ``A`` + ``**B**``：活动行窗口（尾部 4096 字符）内确实
        含行内标记 → 走 ``_render_format_active_line`` 的窗口化路径。窗口化
        路径按全文长度节流（步长 ≤ 256 字符）——每次追加 ≥ 256 字符必刷新。
        """
        r = AnsiStreamRenderer(width=120)
        r.write("A" * 4200 + " **B** ")
        assert "B" in _preview_plain(r)
        chunks = ["第一段最新内容", "第二段最新内容", "第三段最新内容"]
        for idx, chunk in enumerate(chunks):
            r.write(chunk * 40)  # 每段 ~320 字符 ≥ 窗口节流步长
            preview = _preview_plain(r)
            assert chunk in preview, (idx, preview[-200:])
        r.close()
        # 提交路径仍完整渲染（窗口只影响流式中间态）
        committed = "\n".join(ln.plain for ln in r.take_lines())
        assert chunks[-1] in committed

    def test_windowed_active_line_plain_text_live(self):
        """纯文本超长活动行同样逐帧可见（纯文本快路径回归）。"""
        r = AnsiStreamRenderer(width=120)
        r.write("B" * 5000)
        r.write("tail-1")
        assert "tail-1" in _preview_plain(r)
        r.write("-tail2")
        assert "tail-1-tail2" in _preview_plain(r)
        r.close()

    def test_short_format_line_unchanged(self):
        """短格式行仍按整行实时渲染，预览与提交一致。"""
        r = AnsiStreamRenderer(width=100)
        r.write("前置 **加粗** 尾")
        preview = _preview_plain(r)
        assert "加粗" in preview
        assert "**" not in preview
        r.close()
        assert "前置 加粗 尾" in "\n".join(ln.plain for ln in r.take_lines())


# ══════════════════════════════════════════════════════════
# 2. Mermaid 长图活动行不滞留
# ══════════════════════════════════════════════════════════


class TestMermaidLongDiagramActiveLine:
    @staticmethod
    def _long_src(n: int = 60) -> str:
        rows = ["graph TD"]
        rows.extend(f"A{i} --> B{i}" for i in range(n))
        return "\n".join(rows)

    def test_active_line_refreshes_for_long_diagram(self):
        """长图（≥ 48 行）活动行继续增长时预览必须刷新（修复前直接复用）。"""
        src = self._long_src(60)
        r = AnsiStreamRenderer(width=100)
        r.write("```mermaid\n" + src)
        first = _preview_plain(r)
        assert "B59" in first
        # 活动行（未换行的最后一条边）继续输入 ≥ 节流步长的内容
        r.write("xyz" * 8)
        second = _preview_plain(r)
        assert "B59xyz" in second, second[-300:]
        r.close()

    def test_short_diagram_active_line_live(self):
        """短图（< 48 行）活动行逐帧实时（既有契约不回归）。"""
        r = AnsiStreamRenderer(width=100)
        r.write("```mermaid\ngraph TD\nA --> B")
        assert "B" in _preview_plain(r)
        r.write("CD")
        assert "BCD" in _preview_plain(r)
        r.close()


# ══════════════════════════════════════════════════════════
# 3. Front Matter 省略行数实时更新
# ══════════════════════════════════════════════════════════


class TestFrontMatterOmittedRow:
    def test_omitted_count_updates_after_prefix_truncation(self):
        """正文前缀固定后，新增行只改变 dropped——提示数字必须同步。"""
        body = "\n".join(f"k{i}: v{i}" for i in range(205))
        r = AnsiStreamRenderer(width=100)
        r.write("---\n" + body)
        first = _preview_plain(r)
        assert "前 5 行省略" in first, first[:300]
        # 新增一个完整行：正文前缀（前 200 行）不变，dropped 5 → 6
        r.write("\nk206: v206")
        second = _preview_plain(r)
        assert "前 6 行省略" in second, second[:300]
        r.close()

    def test_omitted_count_zero_when_not_truncated(self):
        body = "\n".join(f"k{i}: v{i}" for i in range(5))
        r = AnsiStreamRenderer(width=100)
        r.write("---\n" + body)
        preview = _preview_plain(r)
        assert "省略" not in preview
        r.close()


# ══════════════════════════════════════════════════════════
# 4. 容器头行对象跨帧稳定（性能）
# ══════════════════════════════════════════════════════════


class TestContainerHeadStability:
    def test_admonition_head_identity_stable(self):
        r = AnsiStreamRenderer(width=80)
        r.write("> [!NOTE]\n> first")
        p1 = r.take_preview_lines()
        r.write("\n> second")
        p2 = r.take_preview_lines()
        assert p1 and p2
        assert p1[0] is p2[0]
        r.close()

    def test_fenced_div_head_identity_stable(self):
        r = AnsiStreamRenderer(width=80)
        r.write("::: tip 提示\n正文一")
        p1 = r.take_preview_lines()
        r.write("\n正文二")
        p2 = r.take_preview_lines()
        assert p1 and p2
        assert p1[0] is p2[0]
        r.close()

    def test_details_head_identity_stable(self):
        r = AnsiStreamRenderer(width=80)
        r.write("<details open>\n<summary>摘要</summary>\n正文一")
        p1 = r.take_preview_lines()
        r.write("\n正文二")
        p2 = r.take_preview_lines()
        assert p1 and p2
        assert p1[0] is p2[0]
        r.close()


# ══════════════════════════════════════════════════════════
# 5. 预览源行切分：最后换行位置增量缓存
# ══════════════════════════════════════════════════════════


class TestPreviewSrcLastNewline:
    def test_incremental_last_newline(self):
        r = AnsiStreamRenderer(width=100)
        assert r._preview_src_lines("a\nb") == ["a", "b"]
        assert r._preview_src_auto_nl == 1
        assert r._preview_src_lines("a\nb\nc") == ["a", "b", "c"]
        assert r._preview_src_auto_nl == 3
        assert r._preview_src_lines("a\nb\nc\nd") == ["a", "b", "c", "d"]
        assert r._preview_src_auto_nl == 5

    def test_long_single_line_windowed(self):
        r = AnsiStreamRenderer(width=100)
        big = "x" * 5000
        assert r._preview_src_lines(big) == [big[-4096:]]
        lines = r._preview_src_lines(big + "\n" + "y" * 10)
        assert lines[-1] == "y" * 10
        assert lines[-2] == "x" * 5000

    def test_content_switch_resets(self):
        r = AnsiStreamRenderer(width=100)
        r._preview_src_lines("a\nb\nc")
        assert r._preview_src_lines("完全不同") == ["完全不同"]


# ══════════════════════════════════════════════════════════
# 6. Mermaid 新增图类型
# ══════════════════════════════════════════════════════════


class TestMermaidNewDiagramTypes:
    def test_packet(self):
        src = (
            "packet-beta\n"
            "title TCP Packet\n"
            "0-15: \"Source Port\"\n"
            "16-31: \"Destination Port\"\n"
            "32-63: \"Sequence Number\""
        )
        out = "\n".join(ln.plain for ln in render_mermaid(src))
        assert "TCP Packet" in out
        assert "Source Port" in out
        assert "0-15" in out
        assert "16-31" in out

    def test_packet_relative_size(self):
        out = "\n".join(ln.plain for ln in render_mermaid(
            "packet-beta\n+8: \"A\"\n+8: \"B\""))
        assert "0-7" in out and "8-15" in out

    def test_block(self):
        src = (
            "block-beta\n"
            "columns 2\n"
            "a[\"Alpha\"] b[\"Beta\"]\n"
            "c[\"Gamma\"]"
        )
        out = "\n".join(ln.plain for ln in render_mermaid(src))
        assert "Alpha" in out
        assert "Beta" in out
        assert "Gamma" in out
        assert "┌" in out and "└" in out

    def test_quadrant(self):
        src = (
            "quadrantChart\n"
            "    title Reach and engagement\n"
            "    x-axis Low Reach --> High Reach\n"
            "    y-axis Low Engagement --> High Engagement\n"
            "    quadrant-1 We should expand\n"
            "    quadrant-2 Need to promote\n"
            "    quadrant-3 Re-evaluate\n"
            "    quadrant-4 May be improved\n"
            "    Campaign A: [0.3, 0.6]\n"
            "    Campaign B: [0.45, 0.23]"
        )
        out = "\n".join(ln.plain for ln in render_mermaid(src))
        assert "Reach and engagement" in out
        assert "Low Reach → High Reach" in out
        assert "Need to promote" in out
        assert "May be improved" in out
        assert "Campaign A" in out and "0.3" in out

    def test_requirement(self):
        src = (
            "requirementDiagram\n"
            "    requirement test_req {\n"
            "    id: 1\n"
            "    text: the test text.\n"
            "    risk: high\n"
            "    verifymethod: test\n"
            "    }\n"
            "    element test_entity {\n"
            "    type: simulation\n"
            "    }\n"
            "    test_entity - satisfies -> test_req"
        )
        out = "\n".join(ln.plain for ln in render_mermaid(src))
        assert "test_req" in out
        assert "the test text." in out
        assert "satisfies" in out

    def test_xychart(self):
        src = (
            "xychart-beta\n"
            "    title \"Sales\"\n"
            "    x-axis [jan, feb, mar]\n"
            "    y-axis \"Revenue\" 0 --> 100\n"
            "    bar [10, 20, 30]\n"
            "    line [5, 15, 25]"
        )
        out = "\n".join(ln.plain for ln in render_mermaid(src))
        assert "Sales" in out
        assert "bar:" in out and "line:" in out
        assert "jan" in out and "mar" in out
        assert "30" in out

    def test_sankey(self):
        src = "sankey-beta\nA,B,10\nB,C,5"
        out = "\n".join(ln.plain for ln in render_mermaid(src))
        assert "Sankey" in out
        assert "A" in out and "B" in out and "C" in out
        assert "10" in out and "5" in out
        assert "▶" in out

    def test_unknown_diagram_still_fallback(self):
        out = "\n".join(ln.plain for ln in render_mermaid("unknownDiagram\nx"))
        assert "Mermaid 源码" in out or "unknownDiagram" in out


# ══════════════════════════════════════════════════════════
# 7. 新图类型经完整 Markdown 渲染路径
# ══════════════════════════════════════════════════════════


class TestMermaidThroughMarkdown:
    def test_fenced_xychart(self):
        out = _render_batch(_mermaid_block(
            "xychart-beta\n"
            "    title \"Monthly\"\n"
            "    x-axis [a, b]\n"
            "    bar [3, 7]"
        ))
        assert "Monthly" in out
        assert "bar:" in out
        assert "7" in out

    def test_fenced_sankey(self):
        out = _render_batch(_mermaid_block("sankey-beta\nsrc,dst,42"))
        assert "Sankey" in out
        assert "42" in out

    def test_deferred_fence_keyword_recognition(self):
        """无语言围栏首行为图类型关键词 → 仍识别为 Mermaid 块。"""
        md = "```\nxychart-beta\nx-axis [a, b]\nbar [1, 2]\n```"
        out = _render_batch(md)
        assert "bar:" in out

    def test_keywords_include_new_types(self):
        for kw in ("packet-beta", "block-beta", "xychart", "xychart-beta",
                   "sankey", "sankey-beta"):
            assert kw in _MERMAID_KEYWORDS, kw
