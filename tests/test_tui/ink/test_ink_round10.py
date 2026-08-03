"""第十轮 React Ink 完善测试（完善 react ink / 动效美化 / 布局增强）。

覆盖：
  - useId（React 18 useId 等价物：稳定 + 唯一）
  - TEXT ``wrap`` prop 别名（react-ink ``<Text wrap>``，本框架 ``textWrap``）
  - TEXT ``dimColor`` prop（react-ink 特有：dim 文本更暗）
  - BOX ``flexBasis``（flexbox 主轴初始尺寸：column 高度 / row 宽度）
  - borderStyle 变体扩展（classic / dashed）
  - 输入区下分隔线呼吸（BEAUTY-13：活跃期青色呼吸）
"""

from __future__ import annotations

from src.tui.ink.element import h, BOX, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink import strip_ansi


def _render(el, width: int = 30, height: int = 24):
    root = Reconciler.create_root()
    recon = Reconciler()
    recon.render(root, el, width, height)
    frame = render_frame(root, width)
    plains = [strip_ansi(line.render()) for line in frame.lines]
    return plains, root


class TestUseId:
    """React 18 useId 等价物。"""

    def test_returns_stable_unique_id(self):
        from src.tui.ink.hooks import useId

        seen = {}

        def Comp(props):
            fid = useId()
            seen[fid] = seen.get(fid, 0) + 1
            return h(TEXT, {"children": fid})

        # 挂载两个组件 + 跨帧复用
        root = Reconciler.create_root()
        recon = Reconciler()
        el = h(BOX, None, [h(Comp, {"key": "a"}), h(Comp, {"key": "b"})])
        recon.render(root, el, 30, 24)
        recon.render(root, el, 30, 24)  # 第二帧复用 fiber
        recon.render(root, el, 30, 24)
        # 两个组件各渲染 3 次 → 每个 ID 恰好出现 3 次（跨帧稳定）
        assert len(seen) == 2, f"应恰好 2 个唯一 ID: {seen!r}"
        assert all(v == 3 for v in seen.values()), f"跨帧应稳定复用: {seen!r}"
        ids = list(seen.keys())
        assert ids[0] != ids[1], "不同组件 ID 必须不同"
        assert all(i.startswith(":r") and i.endswith(":") for i in ids), f"ID 格式: {ids!r}"

    def test_remounted_gets_new_id(self):
        """同一位置重新挂载（key 变化）应分配新 ID（React 语义：ID 绑定挂载实例）。"""
        from src.tui.ink.hooks import useId

        ids = []

        def Comp(props):
            ids.append(useId())
            return h(TEXT, {"children": "x"})

        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, h(Comp, {"key": "k1"}), 30, 24)
        first = ids[-1]
        recon.render(root, h(Comp, {"key": "k2"}), 30, 24)  # key 变化 → 重新挂载
        second = ids[-1]
        assert first != second, "重新挂载应分配新 ID"


class TestTextWrapAlias:
    """TEXT ``wrap`` prop 别名（react-ink 语义）。"""

    def test_wrap_truncate(self):
        plains, _ = _render(h(TEXT, {"children": "hello world", "wrap": "truncate", "width": 5}))
        assert plains[0] == "hell…", f"wrap='truncate' 应截断加省略号: {plains!r}"

    def test_wrap_truncate_start(self):
        plains, _ = _render(h(TEXT, {"children": "hello world", "wrap": "truncate-start", "width": 5}))
        assert plains[0] == "…orld", f"wrap='truncate-start' 应开头省略号: {plains!r}"

    def test_wrap_default_wraps(self):
        plains, _ = _render(h(TEXT, {"children": "hello world", "wrap": "wrap", "width": 5}))
        # 词边界换行（方向8）：空格优先断行，单词完整（react-ink textWrap="wrap" 语义）
        assert plains == ["hello", "world"], f"wrap='wrap' 默认换行: {plains!r}"

    def test_text_wrap_takes_precedence(self):
        plains, _ = _render(h(TEXT, {
            "children": "hello world", "wrap": "wrap", "textWrap": "truncate", "width": 5,
        }))
        assert plains[0] == "hell…", "textWrap 应优先于 wrap"


class TestTextDimColor:
    """TEXT ``dimColor`` prop（react-ink 特有）。"""

    def test_dim_color_style(self):
        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, h(TEXT, {"children": "x", "dimColor": True}), 30, 24)
        frame = render_frame(root, 30)
        run = frame.lines[0].runs[0]
        assert run.style is not None
        assert run.style.dim is True
        assert run.style.fg == 238, f"dimColor 应指定暗色 fg: {run.style.fg!r}"

    def test_dim_color_with_explicit_color(self):
        """显式 color 优先于 dimColor 的默认暗色。"""
        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, h(TEXT, {"children": "x", "dimColor": True, "color": 45}), 30, 24)
        frame = render_frame(root, 30)
        run = frame.lines[0].runs[0]
        assert run.style.fg == 45, "显式 color 应覆盖 dimColor 默认色"

    def test_dim_color_false_no_effect(self):
        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, h(TEXT, {"children": "x", "dimColor": False}), 30, 24)
        frame = render_frame(root, 30)
        assert frame.lines[0].runs[0].style is None


class TestFlexBasis:
    """BOX ``flexBasis``（flexbox 主轴初始尺寸）。"""

    def test_column_flex_basis_height(self):
        """column 容器：flexBasis 子节点按指定高度布局。"""
        plains, root = _render(h(BOX, {"height": 8}, [
            h(BOX, {"flexBasis": 3}, [h(TEXT, {"children": "a"})]),
            h(BOX, {"flexBasis": 1}, [h(TEXT, {"children": "b"})]),
        ]), width=30, height=24)
        first = root.child.child
        second = first.sibling
        assert first.layout_box.h == 3, f"column flexBasis 应作初始高度: {first.layout_box.h}"
        assert second.layout_box.h == 1, f"column flexBasis 应作初始高度: {second.layout_box.h}"
        # 第二子节点 y 应基于第一子高度（flexBasis 参与堆叠）
        assert second.layout_box.y == first.layout_box.y + first.layout_box.h, (
            f"flexBasis 高度应参与堆叠: first.y={first.layout_box.y} "
            f"first.h={first.layout_box.h} second.y={second.layout_box.y}"
        )

    def test_row_flex_basis_width(self):
        """row 容器：flexBasis 子节点按指定宽度布局。"""
        plains, root = _render(h(BOX, {"flexDirection": "row", "width": 15}, [
            h(TEXT, {"children": "abcdef", "flexBasis": 3}),
            h(TEXT, {"children": "ghijkl", "flexBasis": 3}),
        ]), width=30, height=24)
        first = root.child.child
        second = first.sibling
        assert first.layout_box.w == 3, f"row flexBasis 应作初始宽度: {first.layout_box.w}"
        assert second.layout_box.w == 3
        assert second.layout_box.x == first.layout_box.x + first.layout_box.w, (
            f"flexBasis 宽度应参与横向排布: first.x={first.layout_box.x} "
            f"first.w={first.layout_box.w} second.x={second.layout_box.x}"
        )

    def test_invalid_flex_basis_ignored(self):
        """畸形 flexBasis（非数字）忽略，保持内容尺寸。"""
        plains, root = _render(h(BOX, {"height": 8}, [
            h(BOX, {"flexBasis": "abc"}, [h(TEXT, {"children": "a"})]),
        ]), width=30, height=24)
        first = root.child.child
        assert first.layout_box.h == 1, f"畸形 flexBasis 应忽略: {first.layout_box.h}"


class TestToolCardNarrowWidthInvariant:
    """BUG-29 回归 — 极端窄屏工具卡主体行宽度不变量（≤ width）。"""

    def _render_tool_card(self, width: int) -> list[str]:
        from src.tui.app.model import AppModel
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.components import render_frame
        from src.tui.ink import strip_ansi
        from src.tui._screen import wcswidth_simple
        from src.renderer.ansi.helpers import AnsiLine as AL

        model = AppModel()
        model.width = width
        tb = model.open_tool_box("t1", "bash", "长命令参数" * 5)
        l = AL.of("  ", None)
        l.append("输出" * 15, None)
        tb.lines.append(l)
        model.close_tool_box("t1", True)

        root = Reconciler.create_root()
        recon = Reconciler()
        el = h(BOX, None, [h(TEXT, {"styled": []})])  # 占位（直接测 committed）
        recon.render(root, el, width, 24)
        # 直接渲染工具卡行（避开 App 组件树）
        from src.tui.app.model import _tool_card_styled_lines
        from src.tui.ink.output import Line
        lines = [Line(runs) for runs in _tool_card_styled_lines(tb, width, 0, None)]
        return [strip_ansi(l.render()) for l in lines]

    def test_width5_no_overflow(self):
        """width=5 时工具卡主体行不超宽（wrap_line 拆 CJK 后仍须截断）。"""
        from src.tui._screen import wcswidth_simple
        plains = self._render_tool_card(5)
        for p in plains:
            assert wcswidth_simple(p) <= 5, f"width=5 行超宽: {p!r}"

    def test_width6_no_overflow(self):
        from src.tui._screen import wcswidth_simple
        plains = self._render_tool_card(6)
        for p in plains:
            assert wcswidth_simple(p) <= 6, f"width=6 行超宽: {p!r}"


class TestHeadAnimationDoesNotRewriteCommitted:
    """方向4 渲染优化回归 — 头部动画（标题栏呼吸）不引发 committed 可见区重写。

    修复前：delta!=0（流式增长）+ 标题栏呼吸色变化（i=0）时，rewrite_start
    = max(0, screen_offset) 从可见区顶部连续重写到末尾——committed 历史
    可见区每帧全量重写（大文档 + 流式 = 高 CPU）。修复后：位移锚点 +
    头部差异区间，committed 可见区零重写。
    """

    def _render_two_frames(self, width=80, height=30, commit_growth=2):
        import io
        from src.tui.app.model import AppModel, StatusState
        from src.tui.app.app import App
        from src.tui.ink.renderer import InkRenderer
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.components import render_frame
        from src.tui.ink import h
        from src.tui.ink.output import Line
        from src.tui.core.style import Style
        from src.tui.app._theme import _glow_bucket
        import src.tui.app.header as H

        model = AppModel()
        model.width = width
        model.status = StatusState(model_name="m", status_active=False, cpu=10, mem=20)
        model.committed_lines = [Line.of(f"history line {i}", Style(fg=244)) for i in range(50)]
        r = Reconciler()
        root = r.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=height)

        # 帧1：标题栏桶 t1
        H.time_glow = lambda lo, hi, period: _glow_bucket(lo, hi, period, 1000)
        el = h(App, {"model": model, "width": width})
        r.render(root, el, width, height)
        renderer.render(render_frame(root, width))
        renderer._stream.seek(0)
        renderer._stream.truncate()

        # 帧2：标题栏桶 t2（颜色变化）+ committed 增长
        H.time_glow = lambda lo, hi, period: _glow_bucket(lo, hi, period, 1005)
        for k in range(commit_growth):
            model.committed_lines.append(Line.of(f"new history {k}", Style(fg=244)))
        el = h(App, {"model": model, "width": width})
        r.render(root, el, width, height)
        renderer.render(render_frame(root, width))
        val = renderer._stream.getvalue()
        H.time_glow = lambda lo, hi, period: _glow_bucket(
            lo, hi, period, int(__import__("time").monotonic() / 0.1),
        )
        return val

    def test_committed_middle_not_rewritten(self):
        """标题栏变化 + committed 增长：committed 中部行零重写。"""
        val = self._render_two_frames()
        assert "history line 20" not in val, (
            f"committed 中部行不应被头部动画引发重写: {val!r}"
        )
        assert "new history 0" in val, f"新增 committed 行应写入: {val!r}"


class TestBorderStyleVariants:
    """borderStyle 变体扩展（classic / dashed）。"""

    def test_classic(self):
        plains, _ = _render(h(BOX, {"border": 1, "borderStyle": "classic", "width": 5, "height": 3},
                              [h(TEXT, {"children": "x"})]))
        assert plains[0] == "+---+", f"classic 顶边: {plains[0]!r}"
        # 内容从内区起点绘制（border=1 → 内区 x=1..3），无额外 padding
        assert plains[1] == "|x  |", f"classic 主体: {plains[1]!r}"
        assert plains[2] == "+---+", f"classic 底边: {plains[2]!r}"

    def test_dashed(self):
        plains, _ = _render(h(BOX, {"border": 1, "borderStyle": "dashed", "width": 6, "height": 3},
                              [h(TEXT, {"children": "x"})]))
        assert plains[0].startswith("┌"), f"dashed 顶边: {plains[0]!r}"
        assert "┄" in plains[0], f"dashed 横线字符: {plains[0]!r}"
        assert "┆" in plains[1], f"dashed 竖线字符: {plains[1]!r}"


class TestInputAreaBottomSepBreath:
    """BEAUTY-13 — 输入区下分隔线（时间戳行）活跃期呼吸。"""

    @staticmethod
    def _make_fiber(status_active: bool):
        from src.tui.app import input_area as ia
        from src.tui.ink.fiber import Fiber
        from src.tui.ink.layout import LayoutBox
        fiber = Fiber("host", "input-area")
        fiber.props = {
            "text": "", "status_active": status_active, "cpu": 1, "mem": 1,
        }
        fiber.layout_box = LayoutBox(0, 0, 60, 3)
        return ia, fiber

    def test_bottom_sep_breath_when_active(self):
        ia, fiber = self._make_fiber(True)
        lines = ia._build_lines(fiber)
        bottom = lines[-1]
        assert bottom.runs[0].style is not None
        assert bottom.runs[0].style.fg != 237, (
            f"活跃期下分隔线应呼吸（非静态深灰）: {bottom.runs[0].style.fg!r}"
        )

    def test_bottom_sep_static_when_idle(self):
        ia, fiber = self._make_fiber(False)
        lines = ia._build_lines(fiber)
        bottom = lines[-1]
        assert bottom.runs[0].style.fg == 237, (
            f"空闲期下分隔线应静态深灰: {bottom.runs[0].style.fg!r}"
        )
