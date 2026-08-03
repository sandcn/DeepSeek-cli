"""apply_cmd — RenderCmd → AppModel 状态变更。

移植 TuiRenderer._do_* 全部语义：推理/内容块追加、tool 组开闭、计数、
阶段迁移、错误/通知/写行/用户消息/解析进度/subagent 帧。
"""

from __future__ import annotations

import logging
import math
import time

from src.tui._const import (
    RenderCommand,
    RenderCmd,
    _CLEAR_PARSE_LINE,
)
from src.tui.core.style import Style
from src.renderer.ansi.helpers import AnsiLine, ansi_to_line
# 方向C 步骤4：_S_USER_ICON/_S_USER_TEXT/_S_NOTICE 迁入 app/_theme.py 共享池
# （被 apply 多处使用；享元收敛原则：多处使用才共享）。
from src.tui.app._theme import _S_NOTICE, get_active_palette

_logger = logging.getLogger(__name__)

# 仅单处使用的样式常量保留模块私有（享元收敛原则）
_S_ERROR = Style(fg=196)
_S_ERROR_ICON = Style(fg=196, bold=True)
_S_PARSE = Style(fg=242)
_S_SPLASH = Style(fg=45, bold=True)
_S_SPLASH_DIM = Style(fg=242)


def apply_cmd(model, cmd: RenderCmd) -> None:
    """将单个 RenderCmd 应用到模型。"""
    cid = cmd.cid
    handler = _HANDLERS.get(cid)
    if handler is None:
        _logger.warning("未知渲染命令: %s", _cmd_name(cid))
        return
    try:
        handler(model, cmd)
    except TypeError:
        _logger.warning("渲染命令 %s 参数错误", _cmd_name(cid), exc_info=True)


def _cmd_name(cid: int) -> str:
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)


# ═══════════════════════════════════════════════════════════
# 消息行共享构建（方向C 步骤4）
# ═══════════════════════════════════════════════════════════

def build_user_line(content: str) -> list[AnsiLine]:
    """构建用户消息行列表（按 ``\\n`` 切分，每行 ``> {segment}`` 顶格）。

    Claude Code 视觉对齐：多行/换行内容每行都带 ``> `` 标记（顶格列 0；
    续行前缀由 model 用户分支重前缀）。样式取自活动调色板槽位
    （Claude TUI parity 步骤 2.3；dark 下与 _S_USER_ICON/_S_USER_TEXT 同值）。

    Returns:
        AnsiLine 列表（每行一条）。
    """
    palette = get_active_palette()
    lines = []
    for segment in content.split("\n"):
        line = AnsiLine.of("> ", palette.user_icon)
        if segment:
            line.append(segment, palette.user_text)
        lines.append(line)
    return lines


def build_assistant_line(content: str) -> list[AnsiLine]:
    """构建助手/其他消息行列表（按 ``\\n`` 切分，每行 ``  \u2502 `` 前缀）。

    apply 与 _consumer 共享的唯一真源；样式取自 _theme 共享池。

    按 ``\\n`` 拆行（与 ``build_user_line`` 对称）——含换行的 DISPLAY_MSGS
    消息若塞进单条 AnsiLine，``wrap_line`` 会把 ``\\n`` 当普通字符保留 → 一条
    frame 行渲染成多条终端行 → 行级 diff / 光标定位错位（修复前）。
    """
    lines = []
    for segment in content.split("\n"):
        line = AnsiLine.of("  \u2502 ", _S_NOTICE)
        if segment:
            line.append(segment)
        lines.append(line)
    return lines


# ── 命令分发表 ─────────────────────────────────────


def _do_notification(model, cmd) -> None:
    line = AnsiLine.of("  \u2502 ", _S_NOTICE)
    line.append(cmd.text)
    model.append_committed("notification", [line])


def _do_write_line(model, cmd) -> None:
    model.append_committed("write_line", [ansi_to_line(cmd.text)])


def _do_error(model, cmd) -> None:
    if not cmd.message:
        return
    line = AnsiLine.of("  ! ", _S_ERROR_ICON)
    line.append(cmd.message, _S_ERROR)
    model.append_committed("error", [line])


def _do_splash(model, cmd) -> None:
    """启动品牌屏（BEAUTY-12 美化：✦ 图标 + 版本号 + 模型名）。

    对齐 TopHeader 渐变标题视觉：✦ 前缀（splash 块样式 _S_SPLASH 亮青 bold），
    无模型名时显示版本号（``v2.x.x``），有模型名时显示模型名（更有信息量）。
    """
    from src.app_init._args import VERSION
    line = AnsiLine.of(f"  \u2726 DeepSeek CLI", _S_SPLASH)
    if model.status.model_name:
        line.append(f" \u00b7 {model.status.model_name}", _S_SPLASH_DIM)
    else:
        line.append(f" \u00b7 v{VERSION}", _S_SPLASH_DIM)
    model.append_committed("splash", [line, AnsiLine.of("")])


def _do_subagent_frame(model, cmd) -> None:
    lines = cmd.frame_lines
    if isinstance(lines, (list, tuple)) and lines and isinstance(lines[0], (list, tuple)):
        lines = lines[0]
    if isinstance(lines, (list, tuple)):
        model.subagent_lines = list(lines)
    else:
        model.subagent_lines = []


def _do_reasoning(model, cmd) -> None:
    if not cmd.text:
        return
    rr = model.ensure_reasoning()
    if rr is None:
        return  # 通道已关闭：丢弃
    rr.write(cmd.text)
    _flush_renderer_to_block(model, "reasoning", rr)


def _do_content(model, cmd) -> None:
    if not cmd.text:
        return
    cr = model.ensure_content()
    if cr is None:
        return  # 通道已关闭：丢弃
    cr.write(cmd.text)
    _flush_renderer_to_block(model, "content", cr)


def _flush_renderer_to_block(model, channel: str, renderer) -> None:
    """将渲染器新产出的行固化到对应块，并**增量提交**已闭合行到缓存。

    流式内容只把未闭合尾留在开放块，闭段行立即进 committed_lines 缓存 →
    大响应渲染成本不随响应增长。
    """
    lines = renderer.take_lines()
    if not lines:
        return
    idx = model.reasoning_block_index if channel == "reasoning" else model.content_block_index
    if 0 <= idx < len(model.blocks):
        block = model.blocks[idx]
        block.lines.extend(lines)
        model.commit_open_block(block)


def _do_phase_done(model, cmd) -> None:
    if cmd.phase == "reasoning":
        model.close_reasoning()
    elif cmd.phase == "content":
        model.close_content()


# ── 工具计数单一真源（方向5：apply 与 _ink_bridge 共用） ─────────

def tool_count_inc(st) -> None:
    """工具计数递增（单一真源；apply ``_do_tool_count_inc`` 与
    ``_ink_bridge.InkBridge.increment_tool`` 共用，零行为变化）。"""
    st.tool_count += 1
    st.tool_total += 1
    if st.tool_count > 0 and st.tool_phase_start <= 0:
        st.tool_phase_start = time.monotonic()


def tool_count_dec(st) -> None:
    """工具计数递减（单一真源；apply ``_do_tool_count_dec`` 与
    ``_ink_bridge.InkBridge.decrement_tool`` 共用，零行为变化）。"""
    if st.tool_count > 0:
        st.tool_count -= 1
    if st.tool_count <= 0:
        st.tool_phase_start = 0.0


def tool_fail_inc(st) -> None:
    """工具失败计数递增（单一真源；apply ``_do_tool_fail_inc`` 与
    ``_ink_bridge.InkBridge.increment_tool_fail`` 共用，零行为变化）。"""
    st.tool_fail += 1


def _do_tool_count_inc(model, cmd) -> None:
    tool_count_inc(model.status)


def _do_tool_count_dec(model, cmd) -> None:
    tool_count_dec(model.status)


def _do_tool_fail_inc(model, cmd) -> None:
    tool_fail_inc(model.status)


def _do_main_phase(model, cmd) -> None:
    phase = cmd.phase
    st = model.status
    if phase != st.main_phase:
        st.main_phase_start = time.monotonic()
    st.main_phase = phase
    if phase == "thinking":
        model.reopen_reasoning()
    if phase in ("thinking", "answering"):
        # 新一轮内容开始前重开 content 通道（多轮会话）
        model.reopen_content()


def _do_tool_open(model, cmd) -> None:
    """工具开始：打开该工具的 box（标题立即上屏，输出增量刷新）。"""
    model.open_tool_box(cmd.tool_id, cmd.tool_name, cmd.detail)


def _do_tool_output(model, cmd) -> None:
    if not cmd.text:
        return
    model.append_tool_output(cmd.tool_id, cmd.text)


def _do_tool_close(model, cmd) -> None:
    """工具结束：关闭对应 box 并追加状态底行。"""
    model.close_tool_box(cmd.tool_id, cmd.success)


def _do_tool_summary(model, cmd) -> None:
    # 批内工具已由 ToolDoneEvent → ToolCloseCmd 逐盒关闭；此命令防御性
    # 关闭残留开放 box（兼容旧调用方）。
    # Bug A 修复：不再依赖单值指针（close_tool_group 已删除），
    # 遍历 tool_boxes 逐个防御性关闭。
    for tool_id in list(model.tool_boxes.keys()):
        model.close_tool_box(tool_id, True)


def _do_parse_info(model, cmd) -> None:
    """解析进度：更新实时行（parse_line）在原位置刷新；Done 时提交到文档。"""
    if cmd.tokens == _CLEAR_PARSE_LINE:
        # 提交当前进度行到文档（等价旧 \n 结束进度行），清空实时行
        if model.parse_line is not None:
            model.append_committed("parse_info", [model.parse_line])
            model.parse_line = None
        return
    if isinstance(cmd.tokens, (int, float)):
        tokens_str = f"{int(cmd.tokens)}t" if math.isfinite(cmd.tokens) else "?"
    else:
        tokens_str = str(cmd.tokens)
    model.parse_line = AnsiLine.of(f"  ~ {cmd.tool_names} {tokens_str} {cmd.elapsed:.2f}s", _S_PARSE)


def _do_user_message(model, cmd) -> None:
    model.append_committed("user", build_user_line(cmd.text))


def _do_subagent_markdown(model, cmd) -> None:
    """subagent 提词/返回 markdown → 消息区块（kind "subagent"）。

    使用主 agent 回答的流式 markdown 渲染路径（``AnsiStreamRenderer``，
    零 Rich Console 往返），渲染结果直接产出 AnsiLine 提交到已关闭块——
    与主 agent 内容渲染一致，无特殊样式。
    """
    if not cmd.text or not cmd.text.strip():
        return
    from src.renderer.ansi import AnsiStreamRenderer
    renderer = AnsiStreamRenderer(width=max(model.width, 20))
    try:
        renderer.write(cmd.text)
    finally:
        renderer.close()
    lines = renderer.take_lines()
    if not lines:
        return
    model.append_committed("subagent", lines)


def _do_display_messages(model, cmd) -> None:
    from src.tui.pipeline.message_display import _content_str
    messages = cmd.messages or []
    for msg in messages:
        role = msg.get("role", "")
        content = _content_str(msg.get("content", ""))
        if role == "user":
            model.append_committed("user", build_user_line(content))
        elif role in ("assistant", "other"):
            model.append_committed("write_line", build_assistant_line(content))
    # 无消息间分隔线（对齐 Claude Code：消息间仅空行分隔，由卡片尾空行承担）


_HANDLERS: dict[int, object] = {

    RenderCommand.NOTIFICATION: _do_notification,
    RenderCommand.WRITE_LINE: _do_write_line,
    RenderCommand.ERROR: _do_error,
    RenderCommand.SPLASH: _do_splash,
    RenderCommand.SUBAGENT_FRAME: _do_subagent_frame,
    RenderCommand.REASONING: _do_reasoning,
    RenderCommand.CONTENT: _do_content,
    RenderCommand.PHASE_DONE: _do_phase_done,
    RenderCommand.TOOL_COUNT_INC: _do_tool_count_inc,
    RenderCommand.TOOL_COUNT_DEC: _do_tool_count_dec,
    RenderCommand.TOOL_FAIL_INC: _do_tool_fail_inc,
    RenderCommand.MAIN_PHASE: _do_main_phase,
    RenderCommand.TOOL_OUTPUT: _do_tool_output,
    RenderCommand.TOOL_SUMMARY: _do_tool_summary,
    RenderCommand.TOOL_OPEN: _do_tool_open,
    RenderCommand.TOOL_CLOSE: _do_tool_close,
    RenderCommand.PARSE_INFO: _do_parse_info,
    RenderCommand.USER_MSG: _do_user_message,
    RenderCommand.DISPLAY_MSGS: _do_display_messages,
    RenderCommand.SUBAGENT_MARKDOWN: _do_subagent_markdown,
}

__all__ = ["apply_cmd", "build_user_line", "build_assistant_line"]
