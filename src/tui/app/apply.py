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
from src.tui.ink._cmd_priority import _cmd_name
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

#: 历史回放工具卡标题 detail 防御性截断上限（字符数）。
#: 当前 extract_key_params 内部已截断（已知工具单值 ≤60、未知工具整体 ≤80），
#: 此处纯防御——防止 core 层未来放宽阈值后超长 detail 撑破标题行。
_TOOL_DETAIL_MAX_LEN = 200


def apply_cmd(model, cmd: RenderCmd) -> None:
    """将单个 RenderCmd 应用到模型。"""
    cid = cmd.cid
    handler = _HANDLERS.get(cid)
    if handler is None:
        _logger.warning("未知渲染命令: %s", _cmd_name(cid))
        return
    try:
        handler(model, cmd)
    except Exception:
        # ★ P3-3（review 修复）：handler **内部**异常统一经 ``except Exception``
        #   独立记录——修复前仅捕获 TypeError：① 参数校验失败（TypeError）与
        #   ② handler 内部 bug 混在同一 except，且 handler 抛出的非 TypeError
        #   内部异常（KeyError/ValueError/AttributeError 等）穿透中断命令处理
        #   （渲染线程崩溃/命令丢失）。参数校验已前置：``_HANDLERS`` 键存在性
        #   检查（未知命令直接返回）+ 各 handler 内部字段防御（如 _do_parse_info
        #   的 float() 归一化、_do_bg_bash_count 的 int()），此处捕获即 handler
        #   实现异常——exc_info=True 保留完整堆栈，可据 traceback 定位根因。
        _logger.warning("渲染命令 %s 执行异常", _cmd_name(cid), exc_info=True)


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
    # ★ 渲染错误（BUG-75 同族）：通知文本可能含 ``\n``（外部消息/工具输出
    #   拼接）——修复前直接 ``line.append(cmd.text)`` 把换行符嵌进单条
    #   AnsiLine，frame 行内嵌字面换行符渲染成多条终端行，破坏行级 diff
    #   模型与光标定位（与 ``build_assistant_line`` 按 \n 拆行同语义）。
    lines = []
    for segment in str(cmd.text).split("\n"):
        line = AnsiLine.of("  \u2502 ", _S_NOTICE)
        if segment:
            line.append(segment)
        lines.append(line)
    model.append_committed("notification", lines)


def _do_write_line(model, cmd) -> None:
    # ★ 渲染错误（BUG-75）：WRITE_LINE 文本可能含 ``\n``——修复前
    #   ``ansi_to_line(cmd.text)`` 把换行符当普通字符保留在单条 AnsiLine 中
    #   （frame 行内嵌 \n → 一条 frame 行渲染成多条终端行，行级 diff / 光标
    #   定位错位）。按 \n 拆行，每段独立解析 ANSI；空段保留为空行（结构
    #   保持）。
    lines = []
    for segment in str(cmd.text).split("\n"):
        if segment:
            line = ansi_to_line(segment)
            if line.runs:
                lines.append(line)
        else:
            lines.append(AnsiLine())
    model.append_committed("write_line", lines)


def _do_error(model, cmd) -> None:
    if not cmd.message:
        return
    # ★ 渲染错误（BUG-75 同族）：错误消息可能含 ``\n``——按 \n 拆行，每行
    #   前缀 ``✖ `` 标记（2026-08-19 美化：``!`` 升级为 ✖ 图标，红色醒目；
    #   与 ``build_user_line`` 多行前缀语义一致）。
    lines = []
    for segment in str(cmd.message).split("\n"):
        line = AnsiLine.of("  \u2716 ", _S_ERROR_ICON)
        if segment:
            line.append(segment, _S_ERROR)
        lines.append(line)
    model.append_committed("error", lines)


def _do_splash(model, cmd) -> None:
    """启动品牌屏：✦ 品牌符号 + 模型名 + 版本。

    ★ BEAUTY-36（2026-08-19 美化）：品牌符号 ``✦``（强调青加粗）+ 模型名
    （亮青加粗）+ ``· 版本``（dim 弱化）三层视觉分层；无模型名时回退显示
    版本号（``v2.x.x``）避免空屏。
    ★ P3（review 2026-08-19）：VERSION 导入 try/except 防御（与
    chat_view._welcome_version 一致——加载循环/未来重构失败时回退空版本
    段，仍渲染 ✦ + 模型名，不丢整块启动屏）。
    """
    try:
        from src.app_init._args import VERSION
        version = str(VERSION)
    except Exception:
        version = ""
    line = AnsiLine.of("  ", None)
    line.append("\u2726 ", Style(fg=45, bold=True))
    if model.status.model_name:
        line.append(model.status.model_name, _S_SPLASH)
        if version:
            # VERSION 已含 ``v`` 前缀（"v2.2.0"）——直接拼接
            # （修复前 ``v{VERSION}`` 产生 ``vv2.2.0``）。
            line.append(f" \u00b7 {version}", Style(fg=242))
    else:
        line.append(version or "\u2726", _S_SPLASH)
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
        # ★ 2026-08-16 修复（多轮工具循环「思考最后一行不显示」）：推理通道已
        #   关闭（CLOSED）但内容仍到来——工具调用后模型继续新一轮推理，且
        #   reasoning.py 的 phase_thinking_sent 每流只发布一次 MainPhase
        #   （reopen_reasoning 未触发）。自动重开通道接收新一轮思考（新块），
        #   避免工具调用后的思考被整体丢弃。reopen 仅 CLOSED→INACTIVE 生效，
        #   正常 INACTIVE/ACTIVE 状态不受影响。
        model.reopen_reasoning()
        rr = model.ensure_reasoning()
        if rr is None:
            return  # 重开后仍不可用（防御）：丢弃
    rr.write(cmd.text)
    _flush_renderer_to_block(model, "reasoning", rr)


def _do_content(model, cmd) -> None:
    if not cmd.text:
        return
    cr = model.ensure_content()
    if cr is None:
        # ★ 2026-08-16 修复（多轮工具循环「回答最后一行不显示」）：内容通道已
        #   关闭（content_closed=True）但内容仍到来——工具调用时 tool_calls.py
        #   在 content_full 非空时发布了 PhaseDone("content")（close_content
        #   关闭通道），工具调用后模型继续输出最终回答，而 phase_answering_sent
        #   每流只发布一次 MainPhase（reopen_content 未触发）。自动重开通道
        #   接收新一轮回答（新块），避免工具调用后的回答被整体丢弃。
        model.reopen_content()
        cr = model.ensure_content()
        if cr is None:
            return  # 重开后仍不可用（防御）：丢弃
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
    # ★ P3（review 2026-08-19）：按块真实状态传递成功位——残留开放 box
    #   的 ``tool_status`` 已为 fail 时按失败关闭（✔/✖ 与真实结果一致，
    #   修复前一律 success=True 把失败工具标成完成态）。
    for tool_id in list(model.tool_boxes.keys()):
        box = model.tool_boxes.get(tool_id)
        success = True
        if box is not None:
            extra = getattr(box, "extra", None) or {}
            success = extra.get("tool_status", "running") != "fail"
        model.close_tool_box(tool_id, success)


def _do_parse_info(model, cmd) -> None:
    """解析进度：更新实时行（parse_line）在原位置刷新；Done 时直接清除（不留文档）。

    ★ 2026-08-16（用户需求）：接收参数完成后**删除**进度行——不再
    append_committed 提交到文档（修复前 ``~ Edit 2608t 8.44s`` 进度行
    残留为会话历史中的 parse_info 块）。live 进度行在参数接收期间原位
    刷新，完成后即消失，工具卡/回答之间不残留进度信息。
    """
    if cmd.tokens == _CLEAR_PARSE_LINE:
        # 删除当前进度行（不提交到文档），清空实时行
        model.parse_line = None
        return
    if isinstance(cmd.tokens, (int, float)):
        tokens_str = f"{int(cmd.tokens)}t" if math.isfinite(cmd.tokens) else "?"
    else:
        # ★ 修复（P3）：tokens 非 int/float 且非 _CLEAR_PARSE_LINE 时
        #   str(None) 显示 "None"——与 elapsed 归一化同族防御（tokens 为
        #   None/缺省时回退空串，不中断进度行渲染）。
        tokens_str = "" if cmd.tokens is None else str(cmd.tokens)
    # ★ review 修复：elapsed 归一化——None/str 等非 float 输入在
    #   f"{cmd.elapsed:.2f}s" 抛 TypeError（被 apply_cmd 吞，进度行缺失）；
    #   float() 归一化失败/非有限值一律回退 0.0（不中断渲染）。
    try:
        elapsed = float(cmd.elapsed)
        if not math.isfinite(elapsed):
            elapsed = 0.0
    except (TypeError, ValueError, OverflowError):
        # ★ 2026-08-06：OverflowError——超大 Decimal（如 1e999999）float()
        #   也抛 OverflowError，补进捕获（修复前穿透 apply_cmd 的
        #   except TypeError 向上冒泡中断命令处理）。
        elapsed = 0.0
    # ★ P2-3（review 修复）：tool_names 单行化——工具名列表可能含 ``\n``
    #   （多工具并行时逗号拼接带换行），直接放进进度行会被终端按物理换行
    #   拆行，破坏「同位置刷新」的进度行语义。复用 ``_single_line_detail``
    #   （委托 ``_format.single_line`` 单一真源：换行/回车转义为字面量）。
    from src.tui.app._model_helpers import _single_line_detail
    # ★ 2026-08-16（用户需求）：进度行（接收参数）显示前确保思考内容先渲染——
    #   防御性固化开放推理通道已渲染行（ReasoningCmd 与 ParseInfoCmd 同批
    #   入队处理时思考内容先上屏，不滞后于进度行；渲染器无残留时零成本跳过）。
    model.flush_reasoning_live()
    model.parse_line = AnsiLine.of(
        f"  ~ {_single_line_detail(cmd.tool_names or '')} {tokens_str} {elapsed:.2f}s",
        _S_PARSE,
    )


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


def _render_markdown_lines(text: str, width: int) -> list:
    """将 markdown 文本渲染为 AnsiLine 列表（与流式内容渲染同管线）。

    历史消息回放（/load、--load、/editmsg、/deitmsg 重渲染）按 ChatView
    语义渲染 assistant 的推理/回答——与流式生成时的 ``AnsiStreamRenderer``
    完全一致（markdown 标题/代码块/表格等格式化、TOC）。
    """
    from src.renderer.ansi import AnsiStreamRenderer
    renderer = AnsiStreamRenderer(width=max(width, 20))
    try:
        renderer.write(text)
    finally:
        renderer.close()
    return renderer.take_lines()


def _append_assistant_rich(model, msg) -> None:
    """assistant 历史消息按 ChatView 语义分块渲染（reasoning/content/tool）。

    用户需求（/editmsg 等历史回放）：思考/回答/工具调用显示与消息区
    （ChatView）渲染一致——不再回退 ``  │ 原文本`` 纯文本行：
      - reasoning_content → reasoning 块（💭 思考 角色头 + markdown 行）；
      - content → content 块（💬 回答 角色头 + markdown 行）；
      - tool_calls → 工具块（ToolCard 卡片，open_tool_box 后续 tool 消息
        经 ``_append_tool_rich`` 追加输出并关闭）。
    """
    from src.tui.pipeline.message_display import _content_str
    reasoning = _content_str(msg.get("reasoning_content", "")).strip()
    content = _content_str(msg.get("content", "")).strip()
    tool_calls = msg.get("tool_calls") or []

    width = getattr(model, "width", 80)
    if reasoning:
        lines = _render_markdown_lines(reasoning, width)
        if lines:
            model.append_committed("reasoning", lines)
    if content:
        lines = _render_markdown_lines(content, width)
        if lines:
            model.append_committed("content", lines)
    # 与正常执行路径（tool_executor_async._execute_one_async 经
    # extract_key_params）一致：工具卡标题 detail 用关键参数**值**
    # （如 Bash → `pwd`、read_file → `src/main.py`），而非原始 JSON
    # （`{"command": "pwd"}`）——历史回放（/editmsg /deitmsg /load
    # 重渲染）与流式执行的工具卡标题显示统一。extract_key_params
    # 兼容 str（JSON 串）与 dict 两种 arguments 形态。
    # import 置于循环外（函数体内惰性 import，与 _do_parse_info 风格一致）
    from src.core.param_formatter import extract_key_params
    for tc in tool_calls:
        # ★ 修复（P3）：tool_calls 元素可能非 dict（str 等异常数据）——
        #   tc.get 抛 AttributeError；非 dict 跳过（安全处理）。
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        # ★ P3（review 2026-08-22）：function 值为非 dict（str 等异常数据）时
        #   fn.get 抛 AttributeError——回退空 dict（tc 已判 dict，fn 此处补判）。
        if not isinstance(fn, dict):
            fn = {}
        name = fn.get("name", "") or ""
        # 保留空 dict 形态（{} → extract_key_params 空 dict 分支返回 ""）；
        # `or ""` 仅兜底 None（arguments 键缺失/显式 None），不拦截空 dict。
        args = fn.get("arguments", "")
        if args is None:
            args = ""
        detail = extract_key_params(name, args)
        # 防御性长度截断：extract_key_params 内部已截断（已知工具单值 ≤60
        # 字符、未知工具整体 ≤80 字符），此处保留以防 core 层未来放宽阈值。
        # 单行化（\n → 字面量 \n）由 open_tool_box 内部统一承担（同源单行）。
        if len(detail) > _TOOL_DETAIL_MAX_LEN:
            detail = detail[:_TOOL_DETAIL_MAX_LEN] + "..."
        model.open_tool_box(tc.get("id") or "", name, detail)


def _append_tool_rich(model, msg) -> None:
    """tool 历史消息：追加工具输出并关闭对应工具块（ToolCard 完整显示）。"""
    from src.tui.pipeline.message_display import _content_str
    tool_call_id = msg.get("tool_call_id") or ""
    content = _content_str(msg.get("content", ""))
    if content.strip():
        model.append_tool_output(tool_call_id, content)
    # 历史回放中的工具调用均已执行完成
    model.close_tool_box(tool_call_id, True)


def _do_display_messages(model, cmd) -> None:
    """历史消息回放：按 ChatView 语义渲染（user/assistant/tool 角色）。

    用户需求（/editmsg 编辑后重渲染等历史显示）：与消息区既有渲染一致——
      - user：``> 内容``（build_user_line）；
      - assistant：reasoning → 💭 思考块 / content → 💬 回答块 /
        tool_calls → 工具卡片；
      - tool：工具输出追加到对应工具卡片并关闭；
      - 其他角色（other）：回退纯文本（防御性，保持既有行为）。
    """
    from src.tui.pipeline.message_display import _content_str
    messages = cmd.messages or []
    for msg in messages:
        # ★ P2-4（review 修复）：消息元素可能非 dict（str/None 等外部注入）——
        #   ``msg.get`` 抛 AttributeError 中断回放；非 dict 跳过（安全处理，
        #   与 _append_assistant_rich 的 tool_calls 元素防御一致）。
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role == "user":
            content = _content_str(msg.get("content", ""))
            if not content.strip():
                # content 为 None/空：跳过不渲染，避免 /load 回放时出现
                # n 行 "None"/空行。
                continue
            model.append_committed("user", build_user_line(content))
        elif role == "assistant":
            _append_assistant_rich(model, msg)
        elif role == "tool":
            _append_tool_rich(model, msg)
        elif role in ("other",):
            content = _content_str(msg.get("content", ""))
            if not content.strip():
                continue
            model.append_committed("write_line", build_assistant_line(content))
    # 防御：回放结束仍有未关闭工具块（tool 结果消息缺失的异常会话）——
    # 强制以完成态关闭，避免工具卡片残留 running（● 呼吸）状态。
    for tool_id in list(getattr(model, "tool_boxes", {}).keys()):
        model.close_tool_box(tool_id, True)
    # 无消息间分隔线（对齐 Claude Code：消息间仅空行分隔，由卡片尾空行承担）


def _do_clear_msgs(model, cmd) -> None:
    """清空消息区显示（/editmsg /deitmsg 等编辑后重渲染前使用）。

    复用 ``model.reset_display()``（Ctrl+L 清屏语义）：清空聊天块/增量缓存/
    推理内容通道/subagent 行/进行中工具/解析行，保留 ``status/input/completion``
    （用户输入与底部栏状态不丢）。随后同批 ``DisplayMsgsCmd`` 重新渲染剩余消息
    ——旧显示（含被编辑消息及其后内容）从屏幕消失，不再追加残留副本。
    """
    model.reset_display()


def _do_bg_bash_count(model, cmd) -> None:
    """后台任务数量更新（bash 与 subagent 分开聚合）。

    由 BackgroundTaskChangedEvent → BgBashCountCmd 驱动，更新模式行行首
    显示的后台 bash / subagent 任务数量。
    """
    # ★ 修复（P3）：cmd.count 可能为 None/非数字字符串（外部注入）——
    #   int() 抛 ValueError 被 apply_cmd 吞、计数不更新；归一化失败回退 0。
    #   ★ P3（review 2026-08-19）：补捕获 OverflowError——``int(inf)`` 抛
    #   OverflowError（与 _do_parse_info 的 elapsed 归一化同族防御）。
    try:
        count = int(cmd.count)
    except (TypeError, ValueError, OverflowError):
        count = 0
    try:
        sa_count = int(getattr(cmd, "subagent_count", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        sa_count = 0
    model.status.bg_bash_count = max(0, count)
    model.status.bg_subagent_count = max(0, sa_count)


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
    RenderCommand.CLEAR_MSGS: _do_clear_msgs,
    RenderCommand.BG_BASH_COUNT: _do_bg_bash_count,
}

__all__ = ["apply_cmd", "build_user_line", "build_assistant_line"]
