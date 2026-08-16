"""OutputConsumer ink 输出模型测试 — 用户需求「所有 TUI 都要用 React Ink 控件跟布局实现所有」。

覆盖：
  1. OutputConsumer 消费 OutputEvent 后按 level 经 ``_LEVEL_STYLES``（core.style.Style）
     + ``ink Line.render()`` 输出——不再手工拼接 ANSI 色串（统一 ink 输出模型，
     与 message_display/_diff_renderer 回退路径迁移语义一致）；
  2. 非 raw 级别带 Style 渲染（error 亮红 196 / warning 黄 220 / success 绿 41 /
     info 中灰 244），raw 级别原样输出（不附加任何样式）；
  3. 旧 ``_LEVEL_COLORS``/``_RESET`` 保留为 deprecated 兼容 re-export（生产路径
     零引用——AST 守卫 test_ink_guard.py R8 守护）；
  4. 未知 level 回退空 Style（原样输出，防御行为）。
"""

from __future__ import annotations

import io
import re

import pytest

from src.tui.core.style import Style
from src.tui.events import OutputConsumer
from src.tui.events.consumers import _LEVEL_COLORS, _LEVEL_STYLES, _RESET
from src.tui.events.event_types import OutputEvent


def _consumer(stream=None):
    """创建 OutputConsumer（chat_ui_managed=False：跳过 ChatUI 活跃检测直写）。"""
    return OutputConsumer(stream=stream or io.StringIO(), chat_ui_managed=False)


def _emit_text(level: str, text: str = "hello") -> str:
    """构造消费者并发布一条 OutputEvent，返回流中输出。"""
    stream = io.StringIO()
    c = _consumer(stream)
    c._on_output(OutputEvent(text=text, level=level, source="test"))
    return stream.getvalue()


# ═══════════════════════════════════════════════════════════
# 1. ink 输出模型（Style + Line.render()）
# ═══════════════════════════════════════════════════════════

def test_error_level_renders_via_style():
    """error 级别经 _LEVEL_STYLES['error']（亮红 196）+ Line.render() 输出。"""
    out = _emit_text("error")
    assert out == _LEVEL_STYLES["error"].apply("hello") + "\n"
    assert out.startswith("\x1b[38;5;196m")
    assert out.endswith("hello\x1b[0m\n")


def test_warning_level_renders_via_style():
    """warning 级别经 _LEVEL_STYLES['warning']（黄 220）渲染。"""
    out = _emit_text("warning")
    assert out == _LEVEL_STYLES["warning"].apply("hello") + "\n"
    assert out.startswith("\x1b[38;5;220m")


def test_success_level_renders_via_style():
    """success 级别经 _LEVEL_STYLES['success']（绿 41）渲染。"""
    out = _emit_text("success")
    assert out == _LEVEL_STYLES["success"].apply("hello") + "\n"
    assert out.startswith("\x1b[38;5;41m")


def test_info_level_renders_via_style():
    """info 级别经 _LEVEL_STYLES['info']（中灰 244）渲染。"""
    out = _emit_text("info")
    assert out == _LEVEL_STYLES["info"].apply("hello") + "\n"
    assert out.startswith("\x1b[38;5;244m")


def test_raw_level_passthrough():
    """raw 级别原样输出（不附加任何 ANSI 样式）。"""
    out = _emit_text("raw", text="plain text")
    assert out == "plain text\n"


def test_unknown_level_falls_back_to_plain():
    """未知 level 回退空 Style（原样输出，防御行为）。"""
    out = _emit_text("unknown-level", text="x")
    assert out == "x\n"


def test_style_values_match_legacy_visual_semantics():
    """新 Style 色号与旧 16 色语义等价（error 红 / warning 黄 / success 绿 / info 灰）。"""
    # 旧色：31(红)/33(黄)/32(绿)/90(暗灰)；新 256 色：196/220/41/244
    assert _LEVEL_STYLES["error"].fg == 196
    assert _LEVEL_STYLES["warning"].fg == 220
    assert _LEVEL_STYLES["success"].fg == 41
    assert _LEVEL_STYLES["info"].fg == 244
    # 视觉语义不变：error 偏红系、warning 黄系、success 绿系
    assert _LEVEL_STYLES["error"].fg >= 190  # 亮红域
    assert 200 <= _LEVEL_STYLES["warning"].fg <= 230  # 黄域
    assert 30 <= _LEVEL_STYLES["success"].fg <= 60  # 绿域


# ═══════════════════════════════════════════════════════════
# 2. 旧 ANSI 常量兼容 re-export
# ═══════════════════════════════════════════════════════════

def test_legacy_ansi_constants_kept_for_compat():
    """旧 _LEVEL_COLORS/_RESET 保留为 deprecated 兼容 re-export（值不变）。"""
    assert _LEVEL_COLORS["error"] == "\033[31m"
    assert _LEVEL_COLORS["warning"] == "\033[33m"
    assert _LEVEL_COLORS["success"] == "\033[32m"
    assert _LEVEL_COLORS["info"] == "\033[90m"
    assert _LEVEL_COLORS["raw"] == ""
    assert _RESET == "\033[0m"


def test_production_path_no_legacy_ansi_constant_usage():
    """生产输出路径（_write）不得引用旧 _LEVEL_COLORS（统一经 _LEVEL_STYLES）。"""
    import inspect
    from src.tui.events import consumers as consumers_mod
    src = inspect.getsource(consumers_mod.OutputConsumer._write)  # 类方法源码
    # _write 函数体内不得出现 _LEVEL_COLORS / _RESET 引用（生产路径零引用）
    assert "_LEVEL_COLORS" not in src
    assert "_RESET" not in src
    # 应引用 _LEVEL_STYLES 与 Line（ink 输出模型）
    assert "_LEVEL_STYLES" in src
    assert "Line" in src
    assert "render()" in src


# ═══════════════════════════════════════════════════════════
# 3. 输出行形态（单行 + 换行 + 无多余转义）
# ═══════════════════════════════════════════════════════════

def test_output_is_single_line_with_newline():
    """输出为单行（含 ANSI 的文本 + 换行），无额外空白行。"""
    out = _emit_text("info", text="line")
    assert out.count("\n") == 1
    stripped = out.rstrip("\n")
    assert "line" in stripped


def test_ansi_sequence_is_complete_and_balanced():
    """ANSI 序列完整闭合（每个 SGR 前缀都有对应 reset）。"""
    out = _emit_text("error", text="hi")
    prefixes = re.findall(r"\x1b\[[0-9;]*m", out)
    # error: \x1b[38;5;196m + \x1b[0m —— 前缀出现 2 次（start + reset）
    assert prefixes == ["\x1b[38;5;196m", "\x1b[0m"]


# ═══════════════════════════════════════════════════════════
# 4. 其他 level 值安全
# ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("level", ["error", "warning", "success", "info", "raw", ""])
def test_all_levels_no_crash(level):
    """全部已知 level（含空串）输出不崩溃。"""
    out = _emit_text(level, text="t")
    assert out.endswith("\n")


def test_style_mapping_contains_raw_as_empty_style():
    """raw 映射为无样式 Style（Line.render 原样返回文本）。"""
    assert _LEVEL_STYLES["raw"] == Style()
    assert Style().apply("x") == "x"
