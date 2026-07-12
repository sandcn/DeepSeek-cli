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

from src.chat_ui.components._error import ErrorBlock
from src.chat_ui.components._user_msg import UserMsgBlock
from src.chat_ui.components._notification import NotificationBlock
from src.chat_ui.const import (
    _STYLE_ERROR_GRADIENT,
    _STYLE_USER_GRADIENT,
    _STYLE_NOTIFICATION_GRADIENT,
)


class TestErrorBlockGradient:
    """ErrorBlock 渐变色增强测试"""

    def test_render_uses_bright_red_style(self):
        """ErrorBlock.render() 输出使用亮红加粗样式"""
        block = ErrorBlock("test error")
        result = block.render()
        assert isinstance(result, Text)
        # 检查两个 span（! 前缀和消息体）都使用增强样式
        for span in result.spans:
            assert span.style == _STYLE_ERROR_GRADIENT, (
                f"预期 Style(bright_red, bold=True)，实际 {span.style}"
            )

    def test_render_message_content_preserved(self):
        """消息文本内容不受样式变更影响"""
        msg = "磁盘空间不足"
        block = ErrorBlock(msg)
        result = block.render()
        assert "! " in result.plain
        assert msg in result.plain

    def test_render_long_message_truncated(self):
        """超长消息仍正常截断"""
        from src.chat_ui.const import _MAX_ERROR_LENGTH
        long_msg = "x" * (_MAX_ERROR_LENGTH + 100)
        block = ErrorBlock(long_msg)
        result = block.render()
        body = result.plain.replace("\n  ! ", "", 1)
        assert len(body) <= _MAX_ERROR_LENGTH + 3  # +3 为 "..."
        assert "..." in body


class TestUserMsgBlockGradient:
    """UserMsgBlock 渐变色增强测试"""

    def test_render_uses_cyan_style(self):
        """UserMsgBlock.render() 输出使用青色加粗样式"""
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


class TestNotificationBlockGradient:
    """NotificationBlock 渐变色增强测试"""

    def test_render_uses_bright_green_style(self):
        """NotificationBlock.render() 输出使用亮绿加粗样式"""
        block = NotificationBlock("task completed")
        result = block.render()
        assert isinstance(result, Text)
        for span in result.spans:
            assert span.style == _STYLE_NOTIFICATION_GRADIENT, (
                f"预期 Style(bright_green, bold=True)，实际 {span.style}"
            )

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
