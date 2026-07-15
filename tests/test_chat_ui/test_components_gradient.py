"""ChatUI 组件渐变色增强测试。

测试范围：
1. ErrorBlock.render() 输出含增强亮红样式属性
2. UserMsgBlock.render() 输出含青色样式属性
3. NotificationBlock.render() 输出含亮绿样式属性
4. 各组件 render() 不影响已有消息文本内容
"""

from __future__ import annotations

import pytest
from rich.style import Style
from rich.text import Text

from src.tui.components._error import ErrorBlock
from src.tui.components._user_msg import UserMsgBlock
from src.tui.components._notification import NotificationBlock
from src.tui.components._write_line import WriteLineBlock
from src.tui.consumer.const import (
    _STYLE_ERROR_GRADIENT,
    _STYLE_USER_GRADIENT,
    _STYLE_NOTIFICATION_GRADIENT,
)


class TestErrorBlockGradient:
    """ErrorBlock 渐变色/动效增强测试"""

    def test_render_narrow_uses_gradient_style(self, monkeypatch):
        """窄屏时 ErrorBlock.render() 输出使用静态 _STYLE_ERROR_GRADIENT"""
        monkeypatch.setattr("src.tui.components._error.is_narrow", lambda: True)
        block = ErrorBlock("test error")
        result = block.render()
        assert isinstance(result, Text)
        # 检查两个 span（! 前缀和消息体）都使用增强样式
        for span in result.spans:
            assert span.style == _STYLE_ERROR_GRADIENT, (
                f"预期 Style(bright_red, bold=True)，实际 {span.style}"
            )

    def test_render_wide_uses_ansi_effect(self):
        """宽屏时 ErrorBlock.render() 输出使用 ANSI 脉动+呼吸效果"""
        block = ErrorBlock("test error")
        result = block.render()
        assert isinstance(result, Text)
        assert "! " in result.plain
        assert "test error" in result.plain

    def test_render_message_content_preserved(self):
        """消息文本内容不受样式变更影响（宽屏/窄屏均适用）"""
        msg = "磁盘空间不足"
        block = ErrorBlock(msg)
        result = block.render()
        assert "! " in result.plain
        assert msg in result.plain

    def test_render_long_message_truncated(self):
        """超长消息仍正常截断"""
        from src.tui.consumer.const import _MAX_ERROR_LENGTH
        long_msg = "x" * (_MAX_ERROR_LENGTH + 100)
        block = ErrorBlock(long_msg)
        result = block.render()
        plain = result.plain
        # 宽屏："\n  │ ! <msg>"；窄屏："\n  ! <msg>"
        for prefix in ("\n  │ ! ", "\n  ! "):
            if plain.startswith(prefix):
                body = plain[len(prefix):]
                break
        else:
            body = plain
        assert len(body) <= _MAX_ERROR_LENGTH + 3  # +3 为 "..."
        assert "..." in body

    def test_render_wide_border_present(self):
        """宽屏时 ErrorBlock.render() 输出含呼吸边框字符 │"""
        block = ErrorBlock("border test")
        result = block.render()
        ansi_str = str(result)
        assert "\u2502" in ansi_str, "宽屏 ErrorBlock 应含边框字符 │"

    def test_render_narrow_no_border(self, monkeypatch):
        """窄屏时 ErrorBlock.render() 输出不含边框字符"""
        monkeypatch.setattr("src.tui.components._error.is_narrow", lambda: True)
        block = ErrorBlock("no border")
        result = block.render()
        ansi_str = str(result)
        assert "\u2502" not in ansi_str, "窄屏 ErrorBlock 不应含边框字符"

    # ── FadeIn 入场过渡测试 ──
    # FadeIn 融入色号计算（不增加 span），颜色随帧渐亮。

    def test_render_wide_fadein_frame_gt_0(self, monkeypatch):
        """frame>0 + 宽屏 → FadeIn 使边框/辉光色号渐亮"""
        from rich.color import ColorType
        from src.tui.core.animator import AnimatorContext
        from src.tui.framework import Framework

        AnimatorContext.reset_default()
        Framework.reset_default()
        Framework.get_default().get_animator().tick()  # frame=1

        monkeypatch.setattr("src.tui.components._error.is_narrow", lambda: False)
        monkeypatch.setattr("src.tui.terminal.narrow.is_narrow", lambda: False)

        block = ErrorBlock("fadein test")
        result = block.render()

        assert "! " in result.plain
        assert "fadein test" in result.plain

        eight_bit = [s for s in result.spans if s.style.color and s.style.color.type == ColorType.EIGHT_BIT]
        assert len(eight_bit) == 3, f"FadeIn 融入色号，span 数应保持 3，实际 {len(eight_bit)}: {result.spans}"

    def test_render_wide_fadein_frame_0(self, monkeypatch):
        """frame=0 → FadeIn 因子为 0"""
        from rich.color import ColorType
        from src.tui.core.animator import AnimatorContext
        from src.tui.framework import Framework

        AnimatorContext.reset_default()
        Framework.reset_default()

        monkeypatch.setattr("src.tui.components._error.is_narrow", lambda: False)
        monkeypatch.setattr("src.tui.terminal.narrow.is_narrow", lambda: False)

        block = ErrorBlock("no fadein")
        result = block.render()

        eight_bit = [s for s in result.spans if s.style.color and s.style.color.type == ColorType.EIGHT_BIT]
        # frame=0 时 FadeIn 不激活，仅 border/pulse/glow 三个 span
        assert len(eight_bit) == 3, f"frame=0 不应有 FadeIn span，实际 {len(eight_bit)}: {result.spans}"

    def test_render_narrow_no_fadein(self, monkeypatch):
        """窄屏 → 即使 frame>0 也不触发 FadeIn"""
        from src.tui.core.animator import AnimatorContext
        from src.tui.framework import Framework

        AnimatorContext.reset_default()
        Framework.reset_default()
        Framework.get_default().get_animator().tick()  # frame=1

        monkeypatch.setattr("src.tui.components._error.is_narrow", lambda: True)

        block = ErrorBlock("narrow fadein")
        result = block.render()

        # 窄屏走静态路径，span 使用 _STYLE_ERROR_GRADIENT
        for span in result.spans:
            assert span.style == _STYLE_ERROR_GRADIENT, (
                f"窄屏预期 {_STYLE_ERROR_GRADIENT}，实际 {span.style}"
            )


class TestUserMsgBlockGradient:
    """UserMsgBlock 渐变色增强测试"""

    def test_render_uses_cyan_style(self, monkeypatch):
        """窄屏时 UserMsgBlock.render() 输出使用青色加粗样式"""
        monkeypatch.setattr("src.tui.components._user_msg.is_narrow", lambda: True)
        block = UserMsgBlock("hello world")
        result = block.render()
        assert isinstance(result, Text)
        for span in result.spans:
            assert span.style == _STYLE_USER_GRADIENT, (
                f"预期 Style(cyan, bold=True)，实际 {span.style}"
            )

    def test_render_message_content_preserved(self):
        """消息文本内容不受样式变更影响"""
        text = "请解释什么是递归"
        block = UserMsgBlock(text)
        result = block.render()
        assert "> " in result.plain
        assert text in result.plain

    def test_render_empty_text(self):
        """空文本不报错"""
        block = UserMsgBlock("")
        result = block.render()
        assert isinstance(result, Text)
        assert "> " in result.plain


class TestUserMsgBlockBeautify:
    """UserMsgBlock 动效美化测试（sparkle 闪烁 + 呼吸色前缀）"""

    def test_render_wide_uses_ansi_effect(self):
        """宽屏时 UserMsgBlock.render() 输出含 ANSI 256 色序列"""
        from rich.color import ColorType
        block = UserMsgBlock("hello world")
        result = block.render()
        assert isinstance(result, Text)
        # Text.from_ansi 将 ANSI 解析为 Rich span，验证存在 256 色 span
        eight_bit = [s for s in result.spans if s.style.color and s.style.color.type == ColorType.EIGHT_BIT]
        assert len(eight_bit) >= 1, f"预期至少 1 个 256 色 span，实际 0: {result.spans}"
        assert "> " in result.plain
        assert "hello world" in result.plain

    def test_render_narrow_static(self, monkeypatch):
        """窄屏时 UserMsgBlock.render() 使用 Rich Text 静态样式"""
        monkeypatch.setattr("src.tui.components._user_msg.is_narrow", lambda: True)
        block = UserMsgBlock("narrow msg")
        result = block.render()
        assert isinstance(result, Text)
        for span in result.spans:
            assert span.style == _STYLE_USER_GRADIENT, (
                f"窄屏预期 {_STYLE_USER_GRADIENT}，实际 {span.style}"
            )

    def test_render_content_preserved(self):
        """消息文本内容不受动效影响"""
        text = "请解释什么是递归"
        block = UserMsgBlock(text)
        result = block.render()
        assert "> " in result.plain
        assert text in result.plain

    def test_render_empty_text_no_crash(self):
        """空文本不崩溃"""
        block = UserMsgBlock("")
        result = block.render()
        assert isinstance(result, Text)
        assert "> " in result.plain

    def test_render_sparkle_present(self):
        """宽屏输出含 sparkle ANSI 序列"""
        from rich.color import ColorType
        block = UserMsgBlock("sparkle test")
        result = block.render()
        assert isinstance(result, Text)
        # Text.from_ansi 将 ANSI 解析为 Rich span，验证存在 256 色 span
        eight_bit = [s for s in result.spans if s.style.color and s.style.color.type == ColorType.EIGHT_BIT]
        assert len(eight_bit) >= 1, f"预期至少 1 个 256 色 span，实际 0: {result.spans}"
        assert "sparkle test" in result.plain


class TestNotificationBlockGradient:
    """NotificationBlock 渐变色增强测试"""

    def test_render_narrow_uses_static_style(self, monkeypatch):
        """窄屏时 render() 使用静态 _STYLE_NOTIFICATION_GRADIENT"""
        monkeypatch.setattr("src.tui.components._notification.is_narrow", lambda: True)
        block = NotificationBlock("task completed")
        result = block.render()
        assert isinstance(result, Text)
        for span in result.spans:
            assert span.style == _STYLE_NOTIFICATION_GRADIENT, (
                f"窄屏预期 Style(bright_green, bold=True)，实际 {span.style}"
            )

    def test_render_wide_contains_content(self):
        """宽屏时 render() 输出包含 · 前缀和消息文本"""
        block = NotificationBlock("task completed")
        result = block.render()
        assert isinstance(result, Text)
        assert "· " in result.plain
        assert "task completed" in result.plain

    def test_render_message_content_preserved(self):
        """消息文本内容不受样式变更影响"""
        text = "文件处理完成"
        block = NotificationBlock(text)
        result = block.render()
        assert "· " in result.plain
        assert text in result.plain

    def test_render_empty_text(self):
        """空文本不报错"""
        block = NotificationBlock("")
        result = block.render()
        assert isinstance(result, Text)
        assert "· " in result.plain

    # ── FadeIn 入场过渡测试 ──
    # FadeIn 融入色号计算（不增加 span），颜色随帧渐亮。

    def test_render_wide_fadein_frame_gt_0(self, monkeypatch):
        """frame>0 + 宽屏 → FadeIn 使边框/辉光色号渐亮"""
        from rich.color import ColorType
        from src.tui.core.animator import AnimatorContext
        from src.tui.framework import Framework

        AnimatorContext.reset_default()
        Framework.reset_default()
        Framework.get_default().get_animator().tick()  # frame=1

        monkeypatch.setattr("src.tui.components._notification.is_narrow", lambda: False)
        monkeypatch.setattr("src.tui.terminal.narrow.is_narrow", lambda: False)

        block = NotificationBlock("fadein note")
        result = block.render()

        assert "· " in result.plain
        assert "fadein note" in result.plain

        eight_bit = [s for s in result.spans if s.style.color and s.style.color.type == ColorType.EIGHT_BIT]
        assert len(eight_bit) == 3, f"FadeIn 融入色号，span 数应保持 3，实际 {len(eight_bit)}"

    def test_render_wide_fadein_frame_0(self, monkeypatch):
        """frame=0 → FadeIn 因子为 0"""
        from rich.color import ColorType
        from src.tui.core.animator import AnimatorContext
        from src.tui.framework import Framework

        AnimatorContext.reset_default()
        Framework.reset_default()

        monkeypatch.setattr("src.tui.components._notification.is_narrow", lambda: False)
        monkeypatch.setattr("src.tui.terminal.narrow.is_narrow", lambda: False)

        block = NotificationBlock("no fade note")
        result = block.render()

        eight_bit = [s for s in result.spans if s.style.color and s.style.color.type == ColorType.EIGHT_BIT]
        assert len(eight_bit) == 3, f"frame=0 span 数应为 3，实际 {len(eight_bit)}"

    def test_render_narrow_no_fadein(self, monkeypatch):
        """窄屏 → 即使 frame>0 也不触发 FadeIn"""
        from src.tui.core.animator import AnimatorContext
        from src.tui.framework import Framework

        AnimatorContext.reset_default()
        Framework.reset_default()
        Framework.get_default().get_animator().tick()  # frame=1

        monkeypatch.setattr("src.tui.components._notification.is_narrow", lambda: True)

        block = NotificationBlock("narrow fade")
        result = block.render()

        for span in result.spans:
            assert span.style == _STYLE_NOTIFICATION_GRADIENT, (
                f"窄屏预期 {_STYLE_NOTIFICATION_GRADIENT}，实际 {span.style}"
            )


# ── Mock OutputAdapter for WriteLineBlock tests ──────────

class _MockAdapter:
    """模拟 OutputAdapter，捕获写入内容。"""
    def __init__(self):
        self.written: list[str] = []
        self.written_raw: list[str] = []

    def write(self, renderable) -> None:
        if hasattr(renderable, 'plain'):
            self.written.append(renderable.plain)
        else:
            self.written.append(str(renderable))

    def write_raw(self, text: str) -> None:
        self.written_raw.append(text)


class TestWriteLineBlockBeautify:
    """WriteLineBlock 左边缘呼吸边框测试"""

    def test_render_wide_has_border(self, monkeypatch):
        """宽屏时 render_to_adapter 含边框字符"""
        monkeypatch.setattr("src.tui.components._write_line.is_narrow", lambda: False)
        block = WriteLineBlock("hello")
        adapter = _MockAdapter()
        block.render_to_adapter(adapter)
        # 纯文本路径应包含边框字符
        raw = "".join(adapter.written_raw)
        assert "\u2502" in raw, f"宽屏应含边框字符，实际: {raw!r}"

    def test_render_narrow_no_border(self, monkeypatch):
        """窄屏时 render_to_adapter 无边框"""
        monkeypatch.setattr("src.tui.components._write_line.is_narrow", lambda: True)
        block = WriteLineBlock("hello")
        adapter = _MockAdapter()
        block.render_to_adapter(adapter)
        raw = "".join(adapter.written_raw)
        assert "\u2502" not in raw, f"窄屏不应含边框字符，实际: {raw!r}"

    def test_render_ansi_preserved(self, monkeypatch):
        """ANSI 文本内容完整（宽屏）"""
        monkeypatch.setattr("src.tui.components._write_line.is_narrow", lambda: False)
        ansi_text = "\033[38;5;41mhello\033[0m"
        block = WriteLineBlock(ansi_text)
        adapter = _MockAdapter()
        block.render_to_adapter(adapter)
        # ANSI 路径走 write()，输出 Text 对象
        combined = "".join(adapter.written)
        assert "hello" in combined, f"ANSI 文本内容应保留，实际: {combined!r}"

    def test_render_fallback_ansi_error(self, monkeypatch):
        """ANSI 解析失败回退不崩溃"""
        monkeypatch.setattr("src.tui.components._write_line.is_narrow", lambda: False)
        # 构造一个含 ANSI 但无法解析的文本（空 \033[ 序列）
        bad_ansi = "test\033["
        block = WriteLineBlock(bad_ansi)
        adapter = _MockAdapter()
        # 不应抛出异常
        result = block.render_to_adapter(adapter)
        assert result >= 0, "回退路径应返回有效行数"

    def test_render_wide_border_present(self):
        """宽屏时 NotificationBlock.render() 输出含呼吸边框字符 │"""
        block = NotificationBlock("border test")
        result = block.render()
        ansi_str = str(result)
        assert "\u2502" in ansi_str, "宽屏 NotificationBlock 应含边框字符 │"

    def test_render_narrow_no_border(self, monkeypatch):
        """窄屏时 NotificationBlock.render() 输出不含边框字符"""
        monkeypatch.setattr("src.tui.components._notification.is_narrow", lambda: True)
        block = NotificationBlock("no border")
        result = block.render()
        ansi_str = str(result)
        assert "\u2502" not in ansi_str, "窄屏 NotificationBlock 不应含边框字符"
