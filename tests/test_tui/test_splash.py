"""SplashScreen 彩虹/霓虹效果测试。

测试范围：
1. 宽屏渲染输出含彩虹 ANSI 格式（\\033[38;5;{n}m 交替出现）
2. 窄屏渲染仍为静态样式
3. _shown 标记正确阻止二次渲染
"""

from __future__ import annotations

import pytest

from src.tui.components._splash import SplashScreen


class TestSplashScreenRainbow:
    """SplashScreen 彩虹效果测试"""

    def test_render_wide_contains_rainbow_ansi(self):
        """宽屏时 SplashScreen.render() 输出含彩虹 ANSI 256 色序列"""
        SplashScreen.reset_shown()
        splash = SplashScreen("test-model")
        result = splash.render()

        # 彩虹 ANSI 格式：\033[38;5;{n}m 每个字符一个颜色
        assert "\033[38;5;" in result, f"宽屏输出应含彩虹 ANSI 色号，实际: {result[:100]!r}..."
        assert "\033[0m" in result, "输出应以 RESET 结尾"

    def test_render_wide_contains_model_and_help(self):
        """宽屏输出包含模型名和帮助信息（字符散布在 ANSI 码中）"""
        SplashScreen.reset_shown()
        splash = SplashScreen("deepseek-chat")
        result = splash.render()

        # 去除 ANSI 序列后验证内容
        import re
        plain = re.sub(r"\033\[[0-9;]*m", "", result)
        assert "deepseek-chat" in plain, f"去除 ANSI 后应含模型名，实际: {plain!r}"
        assert "Chat" in plain
        assert "/help" in plain
        assert "Esc中断" in plain
        assert "Tab 补全" in plain

    def test_render_wide_has_separator_line(self):
        """宽屏输出包含分隔线字符"""
        SplashScreen.reset_shown()
        splash = SplashScreen("m")
        result = splash.render()
        # 分隔线使用 ─ 字符
        assert "\u2500" in result, "宽屏应含横线分隔符"

    def test_render_wide_has_border_pipe(self):
        """宽屏输出包含边框 │ 字符"""
        SplashScreen.reset_shown()
        splash = SplashScreen("m")
        result = splash.render()
        assert "\u2502" in result, "宽屏应含边框 │ 字符"

    def test_render_narrow_no_rainbow(self, monkeypatch):
        """窄屏时 SplashScreen.render() 不含彩虹 ANSI（使用静态样式）"""
        monkeypatch.setattr("src.tui.components._splash.is_narrow", lambda: True)
        SplashScreen.reset_shown()
        splash = SplashScreen("test-model")
        result = splash.render()

        # 窄屏使用 Rich Style 生成 ANSI，不应含 rainbow 的 256 色号序列
        # Rich Style 的 ANSI 格式为 \033[38;5;45m（Style(fg=45)）
        assert "\033[38;5;45m" in result, "窄屏应使用青色 Style"
        # 彩虹的效果是逐字符变色的，窄屏不应出现
        assert "\033[38;5;196m" not in result, "窄屏不应出现彩虹红色"

    def test_render_narrow_has_static_content(self, monkeypatch):
        """窄屏输出仍包含模型名和帮助信息"""
        monkeypatch.setattr("src.tui.components._splash.is_narrow", lambda: True)
        SplashScreen.reset_shown()
        splash = SplashScreen("my-model")
        result = splash.render()

        import re
        plain = re.sub(r"\033\[[0-9;]*m", "", result)
        assert "my-model" in plain
        assert "Chat" in plain
        assert "/help" in plain
        assert "Esc中断" in plain

    def test_render_narrow_has_border_chars(self, monkeypatch):
        """窄屏输出包含边框 │ 和横线字符"""
        monkeypatch.setattr("src.tui.components._splash.is_narrow", lambda: True)
        SplashScreen.reset_shown()
        splash = SplashScreen("m")
        result = splash.render()

        assert "\u2502" in result, "窄屏也应含边框 │ 字符（静态样式）"
        assert "\u2500" in result, "窄屏也应含横线分隔符"

    def test_shown_flag_prevents_second_render(self):
        """_shown 标记正确阻止二次渲染"""
        SplashScreen.reset_shown()
        splash = SplashScreen("model")
        result1 = splash.render()
        assert result1 != "", "首次渲染应返回非空内容"
        result2 = splash.render()
        assert result2 == "", "二次渲染应返回空字符串"

    def test_shown_flag_reset_allows_rerender(self):
        """reset_shown() 后新建实例可以重新渲染"""
        SplashScreen.reset_shown()
        splash = SplashScreen("model")
        splash.render()
        assert splash.render() == "", "二次渲染应为空"
        # reset_shown 重置类级 _shown，但实例级 _shown 已设置
        # 需新建实例以反映类级重置
        SplashScreen.reset_shown()
        splash2 = SplashScreen("model")
        result = splash2.render()
        assert result != "", "reset_shown() 后新建实例应可重新渲染"

    def test_render_wide_output_structure(self):
        """宽屏输出结构：模型名行 + 分隔线行 + 帮助信息行"""
        SplashScreen.reset_shown()
        splash = SplashScreen("model")
        result = splash.render()

        # 应为 3 行
        lines = result.split("\n")
        assert len(lines) == 3, f"预期 3 行，实际 {len(lines)}: {lines!r}"

    def test_render_default_model_lazy_load(self):
        """未指定 model_name 时惰性加载 MODEL 配置"""
        SplashScreen.reset_shown()
        splash = SplashScreen()  # 不传 model_name
        result = splash.render()

        # 应该成功渲染（通过惰性 import 获取 MODEL）
        assert result != "", "未指定模型名时应通过配置惰性加载"
        import re
        plain = re.sub(r"\033\[[0-9;]*m", "", result)
        assert "Chat" in plain
        assert "/help" in plain

    def test_render_respects_model_name_param(self):
        """传入 model_name 参数时使用该值而非配置"""
        SplashScreen.reset_shown()
        splash = SplashScreen("custom-model-v2")
        result = splash.render()

        import re
        plain = re.sub(r"\033\[[0-9;]*m", "", result)
        assert "custom-model-v2" in plain
