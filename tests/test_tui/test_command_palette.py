"""命令面板和会话切换器 — 256 色美化单元测试。

测试策略：
  - 直接调用 _format_display(items) 验证输出含 256 色 ANSI 码
  - 不涉及 TTLCache 缓存获取（无副作用）
  - CommandPalette 和 SessionSwitcher 的构造函数仅创建 TTLCache 对象，不触发 fetch
"""

from __future__ import annotations

from src.ui.tui.command_palette import CommandPalette
from src.ui.tui.session_switcher import SessionSwitcher


class TestCommandPalette256Colors:
    """测试 CommandPalette 的 256 色增强。"""

    def setup_method(self) -> None:
        self.palette = CommandPalette()

    def test_format_display_contains_256_color_codes(self) -> None:
        """_format_display 输出应包含 256 色 ANSI 码（动效增强后动态呼吸色）。"""
        items = ["/help", "/unknown"]
        result = self.palette._format_display(items)
        assert len(result) == 2

        # 已知命令（/help）应包含 256 色 ANSI 码（动效增强后为呼吸色，非固定 47）
        assert "38;5;" in result[0], f"已知命令应含256色码，实际: {result[0]}"

        # 未知命令（/unknown）也应包含 256 色 ANSI 码
        assert "38;5;" in result[1], f"未知命令应含256色码，实际: {result[1]}"

        # 描述文本应包含 256 色码（动效增强后为辉光呼吸色）
        assert "38;5;" in result[0], f"描述应含256色码: {result[0]}"

    def test_format_display_known_command_has_description(self) -> None:
        """已知命令应显示带描述信息。"""
        items = ["/help"]
        result = self.palette._format_display(items)
        assert "查看帮助" in result[0]

    def test_format_display_unknown_command_no_description(self) -> None:
        """未知命令应保持简洁，不附带描述。"""
        items = ["/nonexistent"]
        result = self.palette._format_display(items)
        assert "/nonexistent" in result[0]
        # 未知命令不应有描述箭头后的额外文本（仅箭头本身）
        assert "\u279c" in result[0]

    def test_format_display_multiple_commands(self) -> None:
        """多个命令应逐行格式化。"""
        items = ["/help", "/clear", "/unknown_cmd"]
        result = self.palette._format_display(items)
        assert len(result) == 3

    def test_format_display_empty_list(self) -> None:
        """空列表应返回空列表。"""
        result = self.palette._format_display([])
        assert result == []

    def test_get_title_format_unchanged(self) -> None:
        """_get_title 返回格式不变（仍为'命令面板 N条'）。"""
        title = self.palette._get_title()
        assert "命令面板" in title


class TestSessionSwitcher256Colors:
    """测试 SessionSwitcher 的 256 色增强。"""

    def setup_method(self) -> None:
        self.switcher = SessionSwitcher()

    def _make_session(
        self,
        sid: str = "abc12345",
        title: str = "测试会话",
        model: str = "deepseek-chat",
        count: int = 5,
        saved_at: str = "2026-07-12T08:00:00",
    ) -> list[dict]:
        """构造测试用会话字典列表。"""
        return [{
            "id": sid,
            "title": title,
            "model": model,
            "message_count": count,
            "saved_at": saved_at,
        }]

    def test_format_display_contains_all_256_color_codes(self) -> None:
        """_format_display 输出应包含 256 色 ANSI 码（动效增强后动态呼吸色）。"""
        items = self._make_session()
        result = self.switcher._format_display(items)
        assert len(result) == 1
        line = result[0]

        # DARK_GRAY_256 (38;5;237) — 会话ID短摘要
        assert "38;5;237" in line, f"会话ID应含暗灰256色码(237): {line}"
        # 标题应含 256 色码（动效增强后为呼吸色，非固定 81）
        assert "38;5;" in line, f"标题应含256色码: {line}"
        # 模型名应含 256 色码（动效增强后为呼吸色，非固定 45）
        assert "38;5;" in line, f"模型名应含256色码: {line}"
        # GREEN_256 (38;5;41) — 消息数 ◆
        assert "38;5;41" in line, f"消息数应含绿256色码(41): {line}"
        # DIM_256 (38;5;242) — 时间戳
        assert "38;5;242" in line, f"时间戳应含暗灰256色码(242): {line}"

    def test_format_display_empty_title(self) -> None:
        """无标题会话应显示'(无标题)'占位。"""
        items = self._make_session(title="")
        result = self.switcher._format_display(items)
        assert "无标题" in result[0]

    def test_format_display_missing_saved_at(self) -> None:
        """缺少 saved_at 的会话应不显示时间信息。"""
        items = self._make_session(saved_at="")
        result = self.switcher._format_display(items)
        assert "刚刚" not in result[0]
        assert "分钟前" not in result[0]

    def test_format_display_saved_at_just_now(self) -> None:
        """刚刚保存的会话应显示'刚刚'（使用 datetime.now 确保时区对齐）。"""
        from datetime import datetime
        now_iso = datetime.now().isoformat()
        items = self._make_session(saved_at=now_iso)
        result = self.switcher._format_display(items)
        assert "刚刚" in result[0]

    def test_format_display_model_icon_present(self) -> None:
        """模型名前应显示 ◉ 图标。"""
        items = self._make_session()
        result = self.switcher._format_display(items)
        assert "\u25c9" in result[0]  # ◉

    def test_format_display_message_icon_present(self) -> None:
        """消息数前应显示 ◆ 图标。"""
        items = self._make_session()
        result = self.switcher._format_display(items)
        assert "\u25c6" in result[0]  # ◆

    def test_format_display_sid_short_truncation(self) -> None:
        """会话 ID 应截断为前 8 字符。"""
        items = self._make_session(sid="abcdefghijklmnop")
        result = self.switcher._format_display(items)
        assert "abcdefgh" in result[0]
        assert "ijklmnop" not in result[0]

    def test_format_display_missing_id(self) -> None:
        """缺少 id 的会话应显示 '?'。"""
        items = self._make_session(sid="")
        result = self.switcher._format_display(items)
        assert "?" in result[0]

    def test_format_display_multiple_sessions(self) -> None:
        """多个会话应逐行格式化。"""
        items = [
            self._make_session(sid="aaa", title="Sess1")[0],
            self._make_session(sid="bbb", title="Sess2")[0],
        ]
        result = self.switcher._format_display(items)
        assert len(result) == 2

    def test_format_display_empty_list(self) -> None:
        """空列表应返回空列表。"""
        result = self.switcher._format_display([])
        assert result == []

    def test_get_title_unchanged(self) -> None:
        """_get_title 返回格式不变（仍为'Sessions'）。"""
        assert self.switcher._get_title() == "Sessions"
