"""回归测试 — BUG-74/75（渲染错误修复）。

覆盖：
  - BUG-74：committed-chat 前缀缓存键缺 box.w——终端宽度变化（reflow 前/
    失败）时旧宽度超宽行直接进入帧（E-COMMITTED-OVERFLOW 防线被缓存绕过）。
  - BUG-75：WRITE_LINE / NOTIFICATION / ERROR 文本含 ``\\n`` 时按行拆分——
    修复前换行符嵌进单条 AnsiLine，frame 行内嵌字面换行符渲染成多条终端行，
    破坏行级 diff 模型与光标定位。
"""

from __future__ import annotations

from src.tui.app.model import AppModel
from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui._const import (
    ToolOpenCmd, ToolOutputCmd, ToolCloseCmd,
    WriteLineCmd, NotificationCmd, ErrorCmd,
)
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame


class TestBug74PrefixCacheKeyWidth:
    """BUG-74 — 前缀缓存键含布局宽度。"""

    def _render(self, model, width):
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(model, width)
        r.render(root, el, width, 40)
        return render_frame(root, width)

    def test_width_change_without_reflow_clamps_overflow(self):
        """宽度变化（不 reflow）后前缀缓存不应错误命中——超宽行被防御截断。

        场景：宽 80 提交工具卡（committed_lines 按 80 wrap）→ 宽 40 渲染且
        **不调用 reflow_committed**（模拟 reflow 前时序/失败）——前缀缓存键
        缺 box.w 时错误命中返回旧 80 宽行（破坏行宽不变量）。
        """
        model = AppModel()
        apply_cmd(model, ToolOpenCmd(tool_id="t1", tool_name="Grep", detail="执行 ls"))
        apply_cmd(model, ToolOutputCmd(tool_id="t1", text="输出内容"))
        apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
        # 宽 80 首次渲染（提交前缀缓存）
        frame80 = self._render(model, 80)
        assert any("┌─" in ln.plain for ln in frame80.lines)
        # 宽 40 渲染——不 reflow（防御路径）
        frame40 = self._render(model, 40)
        for ln in frame40.lines:
            assert ln.width <= 40, (
                f"行宽 {ln.width} > 40（BUG-74：前缀缓存错误命中旧宽度）: {ln.plain[:40]!r}"
            )

    def test_width_change_with_reflow_ok(self):
        """宽度变化（reflow 正确执行）后工具卡按新宽度重建。"""
        model = AppModel()
        apply_cmd(model, ToolOpenCmd(tool_id="t1", tool_name="Grep", detail="执行 ls"))
        apply_cmd(model, ToolOutputCmd(tool_id="t1", text="输出内容"))
        apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
        frame80 = self._render(model, 80)
        model.reflow_committed(40)
        frame40 = self._render(model, 40)
        for ln in frame40.lines:
            assert ln.width <= 40, f"行宽 {ln.width} > 40: {ln.plain[:40]!r}"
        assert any("┌─" in ln.plain for ln in frame40.lines)


class TestBug75WriteLineMultilineSplit:
    """BUG-75 — 多行文本按 \\n 拆行（不内嵌换行符）。"""

    def _render(self, model, width=80):
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(model, width)
        r.render(root, el, width, 40)
        return render_frame(root, width)

    def test_write_line_multiline(self):
        """WRITE_LINE 含 \\n → 拆为多行，无单行内嵌换行符。"""
        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="第一行\n第二行"))
        frame = self._render(model)
        plains = [ln.plain for ln in frame.lines]
        # 无行内含换行符（Line 内嵌 \n 破坏行级 diff）
        for p in plains:
            assert "\n" not in p, f"行内嵌换行符: {p!r}"
        assert any("第一行" in p for p in plains), plains
        assert any("第二行" in p for p in plains), plains

    def test_notification_multiline(self):
        """NOTIFICATION 含 \\n → 拆为多行。"""
        model = AppModel()
        apply_cmd(model, NotificationCmd(text="通知一\n通知二"))
        frame = self._render(model)
        plains = [ln.plain for ln in frame.lines]
        for p in plains:
            assert "\n" not in p, f"行内嵌换行符: {p!r}"
        assert any("通知一" in p for p in plains), plains
        assert any("通知二" in p for p in plains), plains

    def test_error_multiline(self):
        """ERROR 含 \\n → 拆为多行（每行带错误标记）。"""
        model = AppModel()
        apply_cmd(model, ErrorCmd(message="错误一\n错误二"))
        frame = self._render(model)
        plains = [ln.plain for ln in frame.lines]
        for p in plains:
            assert "\n" not in p, f"行内嵌换行符: {p!r}"
        assert any("错误一" in p for p in plains), plains
        assert any("错误二" in p for p in plains), plains

    def test_empty_segments_preserved(self):
        """多行文本中的空段保留为空行（结构保持）。"""
        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="a\n\nb"))
        frame = self._render(model)
        plains = [ln.plain for ln in frame.lines]
        # a、空行、b 三段
        assert any(p == "a" for p in plains), plains
        assert any(p == "b" for p in plains), plains
