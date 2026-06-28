"""KeyValue 组件单元测试。

覆盖键值对渲染、自动/固定对齐、空 items、单项、长键名、update() props 变更，
以及 ANSI dim 样式验证。

测试策略：构造 KeyValue 实例，调用 render() 获取输出，
通过 _strip_ansi 剥离 ANSI 后验证格式正确性；
通过 _has_ansi 验证样式序列存在。
"""

from __future__ import annotations

import re
import pytest

from src.chat_ui.components.key_value import KeyValue


# ── 测试辅助 ────────────────────────────────────────────

_ANSI_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    return _ANSI_RE.sub('', text)


def _has_ansi(text: str) -> bool:
    """检查文本是否含 ANSI 序列。"""
    return bool(_ANSI_RE.search(text))


def _render(kv: KeyValue) -> str:
    """渲染 KeyValue 并转为纯 ANSI 字符串。

    render() 空 items 时返回 ""（str），非空时返回 StyledText。
    统一转为 str 以便后续 _strip_ansi / _has_ansi 处理。
    """
    result = kv.render()
    return str(result) if result else ""


# ═══════════════════════════════════════════════════════════
# TestKeyValueRendering
# ═══════════════════════════════════════════════════════════

class TestKeyValueRendering:
    """KeyValue 渲染测试 — 覆盖正常渲染与边界情况。"""

    def test_normal_key_value_rendering(self):
        """正常键值对渲染，验证 'key: value' 格式。"""
        kv = KeyValue(items=[("Name", "Alice"), ("Age", "30")])
        output = _render(kv)
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert len(lines) == 2
        # 逐行验证键与值均存在
        assert "Name" in lines[0] and "Alice" in lines[0]
        assert "Age" in lines[1] and "30" in lines[1]
        # 验证 "key: value" 格式（冒号 + 空格分隔）
        assert re.search(r"Name:\s+Alice", lines[0])
        assert re.search(r"Age:\s+30", lines[1])
        # 每行以 2 空格前缀开头
        assert lines[0].startswith("  ")
        assert lines[1].startswith("  ")

    def test_auto_alignment_key_width_zero(self):
        """自动对齐：key_width=0 时取最长键宽度，冒号列对齐。"""
        kv = KeyValue(items=[("Name", "Alice"), ("Occupation", "Engineer")])
        output = _render(kv)
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert len(lines) == 2
        # "Occupation" (10 chars) > "Name" (4 chars)，auto width=10
        # Name 应右对齐到 10 列: "      Name: Alice"
        assert "Name" in lines[0]
        assert "Occupation" in lines[1]

        # 两个键值对中冒号应在同一列（对齐验证）
        name_colon = lines[0].index(":")
        occ_colon = lines[1].index(":")
        assert name_colon == occ_colon, (
            f"冒号应对齐: Name 行冒号在 {name_colon}, "
            f"Occupation 行冒号在 {occ_colon}"
        )

    def test_fixed_key_width_alignment(self):
        """固定 key_width 对齐：显式指定宽度时按该宽度右对齐。"""
        kv = KeyValue(items=[("Key", "Value")], key_width=20)
        output = _render(kv)
        clean = _strip_ansi(output)

        assert "Key" in clean and "Value" in clean
        # key "Key" (3 chars) + 2 前缀空格 = 开头的 5 字符后是右对齐的 key
        # 总宽度: 2 (前缀) + 20 (key_width) + 2 (: ) = 至少 24
        colon_pos = clean.index(":")
        # 冒号前应有 2 + 20 = 22 个字符宽度
        assert colon_pos >= 22, (
            f"key_width=20 冒号位置应 ≥22，实际 {colon_pos}"
        )

    def test_empty_items(self):
        """空 items 返回空字符串。"""
        kv = KeyValue(items=[])
        output = kv.render()
        assert output == ""

    def test_single_key_value(self):
        """单项键值对正常渲染。"""
        kv = KeyValue(items=[("Key", "Value")])
        output = _render(kv)
        clean = _strip_ansi(output)

        assert "Key" in clean and "Value" in clean
        # 单行，无换行符
        assert "\n" not in clean
        assert clean.startswith("  Key:")

    def test_long_key_name_alignment(self):
        """长键名对齐：超长键名时短键右对齐到该宽度。"""
        kv = KeyValue(items=[
            ("Short", "val1"),
            ("A_Very_Long_Key_Name_Indeed", "val2"),
        ])
        output = _render(kv)
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert len(lines) == 2
        # 长键名行应有完整键名
        assert "A_Very_Long_Key_Name_Indeed" in lines[1]
        # 短键名行的冒号应与长键名行冒号在同一列
        short_colon = lines[0].index(":")
        long_colon = lines[1].index(":")
        assert short_colon == long_colon, (
            f"冒号应对齐: Short 行冒号在 {short_colon}, "
            f"长键名行冒号在 {long_colon}"
        )
        # 短键名前应有足够空白填充
        assert lines[0].startswith("  ")
        # 长键名行也应有前缀空白
        assert lines[1].startswith("  ")

    def test_ansi_dim_style_on_keys(self):
        """键名使用 dim ANSI 样式（SGR 参数 2）。"""
        kv = KeyValue(items=[("StyledKey", "Value")])
        output = _render(kv)

        # 输出应包含 ANSI 序列
        assert _has_ansi(output), "键名应有 dim ANSI 样式"
        # dim 对应 SGR 参数 2（\033[2m 或组合如 \033[2;Xm）
        assert "\033[2m" in output or "\033[2;" in output, (
            f"输出应含 dim SGR 序列，实际: {output!r}"
        )


# ═══════════════════════════════════════════════════════════
# TestKeyValueUpdate
# ═══════════════════════════════════════════════════════════

class TestKeyValueUpdate:
    """KeyValue update() 测试 — 覆盖 props 变更检测与渲染更新。"""

    def test_update_items_changes_render(self):
        """update() 传入新 items 后渲染反映新内容。"""
        kv = KeyValue(items=[("Old", "value")])
        old_clean = _strip_ansi(_render(kv))
        assert "Old" in old_clean

        changed = kv.update({"items": [("New", "data")]})
        assert changed is True

        new_clean = _strip_ansi(_render(kv))
        assert "New" in new_clean and "data" in new_clean
        assert "Old" not in new_clean

    def test_update_key_width_changes_alignment(self):
        """update() 变更 key_width 后对齐宽度变化。"""
        kv = KeyValue(items=[("K", "V")])
        changed = kv.update({"key_width": 30})
        assert changed is True

        clean = _strip_ansi(_render(kv))
        colon_pos = clean.index(":")
        # key_width=30 → 冒号前至少 2 + 30 = 32 字符
        assert colon_pos >= 32, (
            f"key_width=30 时冒号位置应 ≥32，实际 {colon_pos}"
        )

    def test_update_both_props(self):
        """update() 同时变更 items 和 key_width 均生效。"""
        kv = KeyValue(items=[("A", "1")])
        kv.update({"items": [("LongKey", "2"), ("B", "3")], "key_width": 0})

        clean = _strip_ansi(_render(kv))
        lines = clean.split("\n")
        assert len(lines) == 2
        assert "LongKey" in lines[0] and "2" in lines[0]
        assert "B" in lines[1] and "3" in lines[1]
        # key_width=0 自动对齐：LongKey(7) > B(1)，冒号应在同一列
        assert lines[0].index(":") == lines[1].index(":")
