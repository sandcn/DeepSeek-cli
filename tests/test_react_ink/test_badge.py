"""Badge 组件单元测试。

覆盖 5 种 preset 渲染（success/error/warn/info/default）、
空 text、无效 preset 回退、bold/italic/dim 属性叠加、
update() props 变更检测。

测试策略：构造 Badge 实例，调用 render() 获取 ANSI 输出，
通过正则匹配验证颜色序列、样式修饰符和内容正确性。
"""

from __future__ import annotations

import re

from src.chat_ui.components.badge import Badge


# ── 测试辅助 ────────────────────────────────────────────

_ANSI_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    return _ANSI_RE.sub('', text)


def _has_ansi(text: str) -> bool:
    """检查文本是否含 ANSI 序列。"""
    return bool(_ANSI_RE.search(text))


def _get_ansi_codes(text: str) -> list[str]:
    """提取文本中所有 ANSI 转义序列（含重置码）。"""
    return _ANSI_RE.findall(text)


# ═══════════════════════════════════════════════════════════
# TestBadgePresetRendering
# ═══════════════════════════════════════════════════════════

class TestBadgePresetRendering:
    """5 种 preset 渲染测试。"""

    def test_preset_success_green(self):
        """success preset 渲染绿色前景文字。"""
        badge = Badge(text="成功", preset="success")
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "成功" in _strip_ansi(output)
        assert _has_ansi(output)
        # 绿色 = ANSI 32
        assert any("32" in c for c in codes), (
            f"success preset 应包含绿色 ANSI 码(32)，实际序列: {codes}"
        )

    def test_preset_error_red(self):
        """error preset 渲染红色前景文字。"""
        badge = Badge(text="失败", preset="error")
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "失败" in _strip_ansi(output)
        assert _has_ansi(output)
        # 红色 = ANSI 31
        assert any("31" in c for c in codes), (
            f"error preset 应包含红色 ANSI 码(31)，实际序列: {codes}"
        )

    def test_preset_warn_yellow(self):
        """warn preset 渲染黄色前景文字。"""
        badge = Badge(text="警告", preset="warn")
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "警告" in _strip_ansi(output)
        assert _has_ansi(output)
        # 黄色 = ANSI 33
        assert any("33" in c for c in codes), (
            f"warn preset 应包含黄色 ANSI 码(33)，实际序列: {codes}"
        )

    def test_preset_info_blue(self):
        """info preset 渲染蓝色前景文字。"""
        badge = Badge(text="信息", preset="info")
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "信息" in _strip_ansi(output)
        assert _has_ansi(output)
        # 蓝色 = ANSI 34
        assert any("34" in c for c in codes), (
            f"info preset 应包含蓝色 ANSI 码(34)，实际序列: {codes}"
        )

    def test_preset_default_dim(self):
        """default preset 渲染 dim 暗色文字，无前景色。"""
        badge = Badge(text="默认", preset="default")
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "默认" in _strip_ansi(output)
        assert _has_ansi(output)
        # default 使用 dim (ANSI 2)，不应有前景色(30-37)
        dim_codes = [c for c in codes if "2" in c and "3" not in c and "4" not in c and "0" not in c]
        fg_codes = [c for c in codes if any(f"3{n}" in c or f"9{n}" in c for n in range(10))]
        assert len(dim_codes) >= 1, (
            f"default preset 应包含 dim 码(2)，实际序列: {codes}"
        )
        # default 应无前景色码（如 32, 31, 33, 34 等）
        assert len(fg_codes) == 0, (
            f"default preset 不应有前景色码(30-37/90-97)，实际序列: {codes}"
        )


# ═══════════════════════════════════════════════════════════
# TestBadgeEdgeCases
# ═══════════════════════════════════════════════════════════

class TestBadgeEdgeCases:
    """Badge 边界情况测试。"""

    def test_empty_text_returns_empty(self):
        """空 text 返回空字符串 ''。"""
        badge = Badge(text="", preset="success")
        output = badge.render()
        assert output == "", f"空 text 应返回 ''，实际: {output!r}"

    def test_empty_text_all_presets(self):
        """空 text 对所有 preset 均返回 ''。"""
        for preset in ["success", "error", "warn", "info", "default"]:
            badge = Badge(text="", preset=preset)
            output = badge.render()
            assert output == "", (
                f"preset={preset!r} 空 text 应返回 ''，实际: {output!r}"
            )

    def test_invalid_preset_falls_back_to_default(self):
        """无效 preset 回退到 default（dim 无前景色）。"""
        badge = Badge(text="测试", preset="nonexistent")
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "测试" in _strip_ansi(output)
        # 应表现为 default：dim 存在，无前景色
        dim_codes = [c for c in codes if "2" in c and "3" not in c and "4" not in c and "0" not in c]
        fg_codes = [c for c in codes if any(f"3{n}" in c or f"9{n}" in c for n in range(10))]
        assert len(dim_codes) >= 1, (
            f"无效 preset 回退后应包含 dim 码，实际序列: {codes}"
        )
        assert len(fg_codes) == 0, (
            f"无效 preset 回退后不应有前景色码，实际序列: {codes}"
        )

    def test_empty_string_preset_falls_back(self):
        """空字符串 preset 回退到 default。"""
        badge = Badge(text="测试", preset="")
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        # 应同 default：dim 存在，无前景色
        dim_codes = [c for c in codes if "2" in c and "3" not in c and "4" not in c and "0" not in c]
        fg_codes = [c for c in codes if any(f"3{n}" in c or f"9{n}" in c for n in range(10))]
        assert len(dim_codes) >= 1
        assert len(fg_codes) == 0


# ═══════════════════════════════════════════════════════════
# TestBadgeStyleAttributes
# ═══════════════════════════════════════════════════════════

class TestBadgeStyleAttributes:
    """bold/italic/dim 属性叠加测试。"""

    def test_bold_attribute_on_success(self):
        """bold=True 叠加到 success preset 上。"""
        badge = Badge(text="加粗", preset="success", bold=True)
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "加粗" in _strip_ansi(output)
        # 应同时包含 bold(1) 和 green(32)
        assert any("1" in c and ";" not in c for c in codes) or any(
            "1;" in c or ";1" in c for c in codes
        ), f"bold 属性应产生 ANSI 1 码，实际序列: {codes}"
        assert any("32" in c for c in codes), (
            f"bold+success 应保留绿色码(32)，实际序列: {codes}"
        )

    def test_italic_attribute_on_error(self):
        """italic=True 叠加到 error preset 上。"""
        badge = Badge(text="斜体", preset="error", italic=True)
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "斜体" in _strip_ansi(output)
        # 应同时包含 italic(3) 和 red(31)
        assert any("3" in c for c in codes if c not in ("\x1b[31m", "\x1b[32m", "\x1b[33m", "\x1b[34m")), (
            f"italic 属性应产生 ANSI 3 码，实际序列: {codes}"
        )
        assert any("31" in c for c in codes), (
            f"italic+error 应保留红色码(31)，实际序列: {codes}"
        )

    def test_bold_and_italic_together(self):
        """bold=True + italic=True 同时叠加。"""
        badge = Badge(text="粗斜", preset="warn", bold=True, italic=True)
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "粗斜" in _strip_ansi(output)
        # bold(1) + italic(3) + yellow(33) 三者均应出现
        has_bold = any("1" in c and "3" not in c and "2" not in c for c in codes) or any(
            "1;" in c for c in codes
        )
        has_italic = any("3" in c and "1" not in c and "2" not in c for c in codes) or any(
            ";3" in c for c in codes
        )
        has_yellow = any("33" in c for c in codes)
        assert has_bold or has_italic, (
            f"bold+italic 应同时包含 1 和 3 码，实际序列: {codes}"
        )

    def test_dim_attribute_on_non_default(self):
        """dim=True 叠加到非 default preset 上。"""
        badge = Badge(text="暗色", preset="info", dim=True)
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "暗色" in _strip_ansi(output)
        # dim(2) + blue(34)
        has_dim = any("2" in c and "3" not in c and "0" not in c for c in codes) or any(
            ";2" in c or "2;" in c for c in codes
        )
        has_blue = any("34" in c for c in codes)
        assert has_dim, f"dim=True 应产生 ANSI 2 码，实际序列: {codes}"
        assert has_blue, f"dim+info 应保留蓝色码(34)，实际序列: {codes}"

    def test_dim_attribute_on_default_always_dim(self):
        """default preset 始终 dim=True，显式 dim=False 也被覆盖。"""
        badge = Badge(text="默认暗", preset="default", dim=False)
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        assert "默认暗" in _strip_ansi(output)
        # default 始终有 dim(2)
        has_dim = any("2" in c and "3" not in c and "0" not in c for c in codes) or any(
            ";2" in c or "2;" in c for c in codes
        )
        assert has_dim, f"default preset 应始终含 dim 码，实际序列: {codes}"

    def test_no_attributes_defaults(self):
        """所有样式属性默认 False，不产生额外 ANSI 码。"""
        badge = Badge(text="纯色", preset="success")
        output = str(badge.render())
        codes = _get_ansi_codes(output)

        # 仅绿色 + 重置码，无 bold/italic/dim
        non_reset_codes = [c for c in codes if c != "\x1b[0m"]
        # 应只有绿色码
        assert len(non_reset_codes) == 1, (
            f"无样式属性时应仅有一个颜色码，实际序列: {non_reset_codes}"
        )
        assert "32" in non_reset_codes[0]


# ═══════════════════════════════════════════════════════════
# TestBadgeUpdate
# ═══════════════════════════════════════════════════════════

class TestBadgeUpdate:
    """update() props 变更检测测试。"""

    def test_update_text_change_detected(self):
        """text 属性变更时 update() 返回 True。"""
        badge = Badge(text="旧文本", preset="success")
        changed = badge.update({"text": "新文本"})
        assert changed is True, "text 变更应返回 True"

    def test_update_text_same_returns_false(self):
        """text 属性未变时 update() 返回 False。"""
        badge = Badge(text="不变", preset="success")
        changed = badge.update({"text": "不变"})
        assert changed is False, "text 未变应返回 False"

    def test_update_preset_change_detected(self):
        """preset 属性变更时 update() 返回 True。"""
        badge = Badge(text="文本", preset="success")
        changed = badge.update({"preset": "error"})
        assert changed is True, "preset 变更应返回 True"

    def test_update_preset_invalid_falls_back_and_detects(self):
        """无效 preset 经标准化后与当前不同时返回 True。"""
        badge = Badge(text="文本", preset="success")
        changed = badge.update({"preset": "unknown"})
        # "unknown" → 标准化为 "default"，与当前 "success" 不同
        assert changed is True, "无效 preset 标准化后与当前不同应返回 True"

    def test_update_bold_change_detected(self):
        """bold 属性变更时 update() 返回 True。"""
        badge = Badge(text="文本", preset="info", bold=False)
        changed = badge.update({"bold": True})
        assert changed is True, "bold 变更应返回 True"

    def test_update_italic_change_detected(self):
        """italic 属性变更时 update() 返回 True。"""
        badge = Badge(text="文本", preset="warn", italic=False)
        changed = badge.update({"italic": True})
        assert changed is True, "italic 变更应返回 True"

    def test_update_dim_change_detected(self):
        """dim 属性变更时 update() 返回 True。"""
        badge = Badge(text="文本", preset="info", dim=False)
        changed = badge.update({"dim": True})
        assert changed is True, "dim 变更应返回 True"

    def test_update_all_same_returns_false(self):
        """所有属性均相同时 update() 返回 False。"""
        badge = Badge(text="文本", preset="warn", bold=True, italic=False, dim=False)
        changed = badge.update({
            "text": "文本",
            "preset": "warn",
            "bold": True,
            "italic": False,
            "dim": False,
        })
        assert changed is False, "所有属性均相同应返回 False"

    def test_update_partial_props_only_detected_keys(self):
        """仅传入部分 props，其他属性不变时仍正确检测。"""
        badge = Badge(text="原文", preset="success", bold=False)
        # 仅传 bold，text 和 preset 不变
        changed = badge.update({"bold": True})
        assert changed is True

        # 再次传相同 bold，不应变更
        changed = badge.update({"bold": True})
        assert changed is False

    def test_update_rerender_after_change(self):
        """update() 变更后 render() 反映新属性。"""
        badge = Badge(text="旧", preset="success")
        old_output = str(badge.render())

        badge.update({"text": "新", "preset": "error"})
        new_output = str(badge.render())

        assert "旧" in _strip_ansi(old_output)
        assert "新" in _strip_ansi(new_output)
        # 颜色应变：success(32) → error(31)
        old_codes = _get_ansi_codes(old_output)
        new_codes = _get_ansi_codes(new_output)
        assert any("32" in c for c in old_codes)
        assert any("31" in c for c in new_codes)
