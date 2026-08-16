"""bash stderr 行颜色包裹 → 工具卡空白行回归测试（BUG-79）。

根因：``display()/web_display()/_handle_line`` 的 stderr 分支原按
``f"{RED}{safe}{RESET}"`` 包裹输出行——``_read_loop`` 按行收集自带行尾
``\\n``，包裹后换行符被夹在 color 与 RESET 之间，下游
``EventDispatcher._on_tool_output`` 的 ``rstrip("\\n")`` 与
``append_tool_output`` 的「剔除尾空 segment」（BUG-78）全部失效
（文本以 ``\\x1b[0m`` 结尾，split 出纯 RESET 空 segment）→ 工具卡每个
stderr 行多渲染一个空白行（用户报障「调用 bash 工具后 TUI 显示空白行」）。

修复：``_wrap_colored_line`` 把行尾 ``\\n`` 移到 RESET 之后。

覆盖：
- ``_wrap_colored_line`` 单元：有/无行尾 \\n 的包裹位置；
- 全链路（bash 发布文本 → EventDispatcher → AppModel）：修复后包裹格式
  不产生空白行；旧的错误格式（\\n 在 RESET 前）产生空白行（锁定回归）；
- stdout 行不受影响（无空白行）。
"""
from __future__ import annotations

from src.tools._bash_support import _wrap_colored_line
from src.tui._const import ToolOpenCmd, ToolOutputCmd
from src.tui._dispatcher import EventDispatcher
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui.events.event_types import ToolOutputChunkEvent

_RED = "\x1b[31m"
_RESET = "\x1b[0m"


# ── _wrap_colored_line 单元 ───────────────────────────────

def test_wrap_colored_line_newline_after_reset():
    """行尾 \\n 保持在 RESET 之后（下游 rstrip/尾空 segment 剔除可恢复）。"""
    assert _wrap_colored_line("err1\n", _RED) == f"{_RED}err1{_RESET}\n"
    # 无行尾 \n（EOF 残留行）：原样包裹
    assert _wrap_colored_line("err1", _RED) == f"{_RED}err1{_RESET}"
    # 多行（中间 \n 保留）：仅末尾 \n 移到 RESET 之后
    assert _wrap_colored_line("a\n\nb\n", _RED) == f"{_RED}a\n\nb{_RESET}\n"


# ── 全链路：bash 发布文本 → EventDispatcher → AppModel ────

def _dispatch_tool_output(text: str) -> ToolOutputCmd | None:
    """模拟 EventDispatcher._on_tool_output 对 bash 发布文本的处理。"""
    cmds: list = []
    dispatcher = EventDispatcher(push_cmd=cmds.append, main_label="main")
    dispatcher._on_tool_output(ToolOutputChunkEvent(
        label="t1", tool_id="t1", text=text, source="agent",
    ))
    for c in cmds:
        if isinstance(c, ToolOutputCmd):
            return c
    return None


def _card_plains(text: str) -> list[str]:
    """bash 工具卡模型行 plain 列表（标题行 + 内容行）。"""
    m = AppModel()
    m.width = 50
    apply_cmd(m, ToolOpenCmd(tool_name="bash", tool_id="t1", detail="cmd 2>&1"))
    cmd = _dispatch_tool_output(text)
    if cmd is not None:
        apply_cmd(m, cmd)
    return [l.plain for l in m.tool_boxes["t1"].lines]


def test_stderr_wrapped_line_no_blank_line():
    """修复后 stderr 包裹格式（\\n 在 RESET 之后）→ 工具卡无空白行。"""
    plains = _card_plains(f"{_RED}err1{_RESET}\n")
    assert plains == ["  · Bash · cmd 2>&1", "  err1"], (
        f"stderr 行不应产生空白行: {plains}"
    )


def test_stderr_old_wrap_format_blank_line_regression():
    """旧的错误包裹格式（\\n 在 RESET 前）→ 每行多一个空白行（锁定回归）。"""
    plains = _card_plains(f"{_RED}err1\n{_RESET}")
    assert plains == ["  · Bash · cmd 2>&1", "  err1", "  "], (
        f"旧格式应产生尾空白行（回归锁定）: {plains}"
    )


def test_stderr_multi_line_no_blank_line():
    """连续 stderr 行：每行内容后均无空白行。"""
    m = AppModel()
    m.width = 50
    apply_cmd(m, ToolOpenCmd(tool_name="bash", tool_id="t1", detail="cmd 2>&1"))
    for line in ("err1", "err2", "err3"):
        cmd = _dispatch_tool_output(f"{_RED}{line}{_RESET}\n")
        assert cmd is not None
        apply_cmd(m, cmd)
    plains = [l.plain for l in m.tool_boxes["t1"].lines]
    assert plains == ["  · Bash · cmd 2>&1", "  err1", "  err2", "  err3"], (
        f"连续 stderr 行不应有空白行: {plains}"
    )


def test_stdout_line_no_blank_line():
    """stdout 路径（无颜色包裹）不受影响：无空白行。"""
    plains = _card_plains("out1\n")
    assert plains == ["  · Bash · cmd 2>&1", "  out1"], plains
