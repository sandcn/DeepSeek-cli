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
    ContentCmd,
    ReasoningCmd,
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

#: 流式内容块保留的 markdown 源文本上限（仅用于终端 resize 时整块重渲染）。
#: 超限后停止累积并标记 ``extra["source_truncated"]``——避免超长回答常驻
#: 内存、且 resize 时退化为 O(全文) 重渲染（重渲染路径见 AppModel）。
_SOURCE_TEXT_MAX = 1_000_000

# 历史回放工具卡标题 detail 不再做固定长度截断（2026-10-05 用户需求：工具卡
# 标题行参数达到终端宽度）——detail 完整交给 open_tool_box，标题行渲染时按
# 终端宽度 ``truncate_runs`` 截断（toolcard.tool_card_lines）。


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


def _strip_control_text(text: str) -> str:
    """去终端控制序列后的可见文本（判定「纯控制序列」段落）。

    先剥离 ANSI 转义序列（``strip_ansi``：CSI/OSC/SGR 等），再剔除 C0/C1
    控制字符（含 ``\\r``/``\\n``/``\\t`` 与 DEL）——剩下空串即「纯控制序列」。
    仅剔除控制字符（不用 ``str.isprintable``：后者会误伤零宽连接符等
    组合字符，破坏 emoji/合字序列）。
    """
    from src.renderer.ansi.helpers import strip_ansi

    cleaned = strip_ansi(text or "")
    return "".join(
        ch for ch in cleaned
        if not (ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F)
    )


def _do_write_line(model, cmd) -> None:
    # ★ 渲染错误（BUG-75）：WRITE_LINE 文本可能含 ``\n``——修复前
    #   ``ansi_to_line(cmd.text)`` 把换行符当普通字符保留在单条 AnsiLine 中
    #   （frame 行内嵌 \n → 一条 frame 行渲染成多条终端行，行级 diff / 光标
    #   定位错位）。按 \n 拆行，每段独立解析 ANSI；空段保留为空行（结构
    #   保持）。
    # ★ 裸终端控制序列过滤（BUG-78）：WRITE_LINE 是「原始输出」通道——
    #   非 TUI 路径用它清行/刷 spinner，典型为 ``"\r\033[K"``（工具解析
    #   结束时清理进度行，见 ``pipeline_async._cleanup_display``）。该文本
    #   在 TUI 下会被当作**文档内容行**提交：``ansi_to_line`` 只解析 SGR，
    #   ``\r`` / ``\x1b[K`` 原样保留 → 渲染写入终端时执行回车 + 清行（清掉
    #   刚写入的本行内容），且每轮工具调用都往文档中部插一行、把后续行整体
    #   下移（滚动 + 重写 → 屏幕内容抖动/"刷出"）。TUI 自行管理重绘与进度行
    #   清理，此处过滤「去控制序列后无可见文本」的段（不产出行）。
    lines = []
    for segment in str(cmd.text).split("\n"):
        if segment:
            if not _strip_control_text(segment):
                continue  # 纯终端控制序列：不产出内容行
            line = ansi_to_line(segment)
            if line.runs:
                lines.append(line)
        else:
            lines.append(AnsiLine())
    if not lines:
        return  # 全为纯控制序列：不产出空块（原行为会留下空 write_line 块）
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
    """启动欢迎卡（提交块；内容与空状态欢迎卡**同源**）。

    ★ 2026-10-07（空状态/启动界面重构）：旧实现仅输出 ``✦ 模型名 · 版本``
    一行（模型名未知时退化为 ``✦ v2.2.0``），既与顶部标题栏品牌/版本重复、
    信息单薄。现改为提交与聊天区空态同源的**欢迎卡**（``_welcome`` 单一
    真源）：运行环境信息（模型/模式/主题/目录）+ 操作引导——不再重复
    标题栏品牌行，信息量显著提升。

    提交块 kind 保持 ``"splash"``（轨迹视图跳过、模型助手按无头块处理等
    既有语义不变）。
    """
    from src.tui.app._welcome import welcome_card_rows

    # ★ 2026-10-07（欢迎屏卡片化）：splash 卡片宽度取会话当前宽度（未设置时
    #   回退 80）——与聊天区空态欢迎卡**同一卡片视觉**（同源构建）；终端
    #   resize 后由 committed reflow 按新宽度重排（与其它已提交内容一致）。
    try:
        _width = int(getattr(model, "width", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        _width = 0
    if _width <= 0:
        _width = 80
    lines = []
    for row in welcome_card_rows(model, _width):
        line = AnsiLine()
        for text, style in row:
            line.append(text, style)
        lines.append(line)
    lines.append(AnsiLine.of("", None))
    model.append_committed("splash", lines)


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
    _flush_renderer_to_block(model, "reasoning", rr, source_delta=cmd.text)


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
    _flush_renderer_to_block(model, "content", cr, source_delta=cmd.text)


def _flush_renderer_to_block(model, channel: str, renderer,
                             source_delta: str = "") -> None:
    """将渲染器新产出的行固化到对应块，并**增量提交**已闭合行到缓存。

    流式内容只把未闭合尾留在开放块，闭段行立即进 committed_lines 缓存 →
    大响应渲染成本不随响应增长。未闭合块预览行（preview_lines）同步刷新
    ——整体替换语义，使段落/代码块/表格在闭合前实时可见。``source_delta``
    累积块的 markdown 源文本（供终端 resize 时整块重渲染重排）。
    """
    lines = renderer.take_lines()
    take_preview = getattr(renderer, "take_preview_lines", None)
    preview = take_preview() if callable(take_preview) else []
    idx = model.reasoning_block_index if channel == "reasoning" else model.content_block_index
    if 0 <= idx < len(model.blocks):
        block = model.blocks[idx]
        if source_delta:
            # ★ 源文本上限：``source_text`` 仅用于 resize 时整块重渲染；超长
            #   回答无上限累积会常驻内存并使重渲染退化为 O(全文)。达到上限后
            #   停止累积并标记——重渲染路径检测标记后保留现有行（避免用截断
            #   源渲染出错误内容）。
            cur = getattr(block, "source_text", "")
            if len(cur) < _SOURCE_TEXT_MAX:
                merged = cur + source_delta
                if len(merged) > _SOURCE_TEXT_MAX:
                    merged = merged[:_SOURCE_TEXT_MAX]
                    block.extra["source_truncated"] = True
                    _logger.debug("内容块源文本超限（%d 字符），停止累积",
                                  _SOURCE_TEXT_MAX)
                block.source_text = merged
        if lines:
            block.lines.extend(lines)
            model.commit_open_block(block)
        block.preview_lines = list(preview)


def _do_phase_done(model, cmd) -> None:
    if cmd.phase == "reasoning":
        model.close_reasoning()
    elif cmd.phase == "content":
        model.close_content()
    elif cmd.phase == "segment_end":
        # ★ 思考和回答都完成（segment_end 收尾信号）：让流式 markdown 渲染
        #   出所有剩余内容并清空流式指示（spinner）。
        #   - close_reasoning/close_content 幂等——推理/回答已由前序
        #     PhaseDone("reasoning"/"content") 分阶段关闭时零成本跳过；
        #   - 纯思考/仅回答等场景由本事件兜底收尾，保证「思考和回答完成」
        #     在所有正常结束路径都会收尾（不再依赖工具调用分支）。
        model.finish_stream_render()


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
    model.append_tool_output(
        cmd.tool_id, cmd.text,
        chat_hidden=bool(getattr(cmd, "chat_hidden", False)),
    )


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
    # 保留源文本：终端 resize 时按新宽度整块重渲染（与流式块/历史回放块
    # 一致，表格/代码块随宽度重排）。
    model.append_committed("subagent", lines).source_text = cmd.text


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


def _append_assistant_rich(model, msg, anon_ids: list | None = None) -> None:
    """assistant 历史消息按 ChatView 语义分块渲染（reasoning/content/tool）。

    用户需求（/editmsg 等历史回放）：思考/回答/工具调用显示与消息区
    （ChatView）渲染一致——不再回退 ``  │ 原文本`` 纯文本行：
      - reasoning_content → reasoning 块（💭 思考 角色头 + markdown 行）；
      - content → content 块（💬 回答 角色头 + markdown 行）；
      - tool_calls → 工具块（ToolCard 卡片，open_tool_box 后续 tool 消息
        经 ``_append_tool_rich`` 追加输出并关闭）。

    Args:
        anon_ids: 匿名（无 tool_call_id）工具调用的**合成 id 队列**（★ P3
            review）——回放中无 id 的 tool_calls 生成稳定合成 id（
            ``__replay_N``）并登记，后续无 id 的 tool 消息按序取用配对，
            修复前统一以空 id 打开/关闭（多无 id 调用时用 FIFO 匹配，
            与 tool 结果消息错配）。
    """
    from src.tui.pipeline.message_display import _content_str
    reasoning = _content_str(msg.get("reasoning_content", "")).strip()
    content = _content_str(msg.get("content", "")).strip()
    tool_calls = msg.get("tool_calls") or []

    width = getattr(model, "width", 80)
    if reasoning:
        lines = _render_markdown_lines(reasoning, width)
        if lines:
            # 保留源文本：终端 resize 时按新宽度整块重渲染（表格/代码块等
            # 定宽结构随宽度重排，与流式块一致——修复前历史回放块 resize 后
            # 表格框线被逐行 wrap 拆断）。
            model.append_committed("reasoning", lines).source_text = reasoning
    if content:
        lines = _render_markdown_lines(content, width)
        if lines:
            model.append_committed("content", lines).source_text = content
    # 与正常执行路径（tool_executor_async._execute_one_async 经
    # extract_key_params）一致：工具卡标题 detail 用关键参数**值**
    # （如 Bash → `pwd`、read_file → `src/main.py`），而非原始 JSON
    # （`{"command": "pwd"}`）——历史回放（/editmsg /deitmsg /load
    # 重渲染）与流式执行的工具卡标题显示统一。extract_key_params
    # 兼容 str（JSON 串）与 dict 两种 arguments 形态。
    # import 置于循环外（函数体内惰性 import，与 _do_parse_info 风格一致）
    from src.core.param_formatter import extract_key_params
    for _ti, tc in enumerate(tool_calls):
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
        # 单行化（\n → 字面量 \n）由 open_tool_box 内部统一承担（同源单行）；
        # 显示截断（按终端宽度）由 toolcard 标题行 ``truncate_runs`` 承担。
        # ★ P3（review）：无 tool_call_id 时生成稳定合成 id（并登记配对队列）
        #   ——修复前统一空 id 打开（匿名 box 靠 FIFO 关闭），多个无 id 调用
        #   与 tool 结果消息混排时易错配。
        tid = tc.get("id") or ""
        if not tid and anon_ids is not None:
            tid = f"__replay_{_ti}"
            anon_ids.append(tid)
        model.open_tool_box(tid, name, detail)


def _append_tool_rich(model, msg, anon_ids: list | None = None) -> None:
    """tool 历史消息：追加工具输出并关闭对应工具块（ToolCard 完整显示）。

    ★ P3（review）：无 ``tool_call_id`` 时从 ``anon_ids`` 队列取配对合成 id
    （见 ``_append_assistant_rich``）；成功位按消息携带的失败标记还原
    （``is_error``/``status``）——修复前无条件 ``True``，历史回放中原失败的
    工具被渲染为 ✔（信息失真）。消息无该字段时保持 True（无法还原）。
    """
    from src.tui.pipeline.message_display import _content_str
    tool_call_id = msg.get("tool_call_id") or ""
    if not tool_call_id and anon_ids:
        tool_call_id = anon_ids.pop(0)
    content = _content_str(msg.get("content", ""))
    # ★ 用户需求：read_file 聊天区工具卡只显示标题行（隐藏读到的文件内容）——
    #   历史回放的工具消息内容为工具返回值（成功读取 = ``文件: <path>\n<内容>``），
    #   命中该形态即标记聊天卡隐藏该内容行；内容行仍保留在工具块数据中
    #   （轨迹 Trace / 详情视图照常可见）。读取失败/空文件等提示不隐藏。
    chat_hidden = False
    _box = getattr(model, "tool_boxes", {}).get(tool_call_id)
    if _box is not None:
        _extra = getattr(_box, "extra", None) or {}
        if (
            _extra.get("tool_name") == "read_file"
            and content.lstrip().startswith("文件: ")
        ):
            chat_hidden = True
    if content.strip():
        model.append_tool_output(tool_call_id, content, chat_hidden=chat_hidden)
    # 历史回放中的工具调用均已执行完成；失败信息按消息字段还原
    _is_err = bool(msg.get("is_error")) or str(msg.get("status", "")).lower() in (
        "error", "failed", "fail",
    )
    model.close_tool_box(tool_call_id, not _is_err)


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
    # ★ P3（review）：匿名（无 tool_call_id）工具调用的合成 id 配对队列
    anon_ids: list = []
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
            _append_assistant_rich(model, msg, anon_ids)
        elif role == "tool":
            _append_tool_rich(model, msg, anon_ids)
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


def _do_compaction(model, cmd) -> None:
    """上下文压缩状态更新（模式行行首 ``compact · N``）。

    由 CompactionChangedEvent → CompactionCmd 驱动，更新当前正在压缩的
    Agent 数量（主 Agent + 全部 SubAgent 聚合）。
    """
    try:
        active = int(getattr(cmd, "active", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        active = 0
    model.status.compaction_active = max(0, active)


#: 同帧合并的增量文本命令（纯追加语义，合并后渲染结果等价）
_COALESCE_TEXT_CIDS: frozenset = frozenset({
    int(RenderCommand.CONTENT), int(RenderCommand.REASONING),
})


def coalesce_commands(commands: list) -> list:
    """合并同一渲染帧内的相邻增量文本命令（流式渲染性能）。

    模型高速流式输出时，一帧可能排空多条 ``CONTENT`` / ``REASONING`` 命令；
    逐条应用会让 markdown 渲染器对同一未闭合块重复做全量预览刷新（超长单行
    时每次刷新都要重渲染 + 重新换行整个尾部窗口）。这些命令是**纯追加**语义
    且同帧内无中间渲染，合并后渲染结果完全一致，而刷新次数降到「每帧每通道
    一次」。

    仅合并**相邻同类**命令——非文本命令（工具卡 / 阶段 / 计数 / 清屏…）作为
    边界原样保留，跨类型命令顺序不变（等价性由 tests 锁定）。
    """
    out: list = []
    pending_cid: int | None = None
    pending: list[str] = []

    def _flush() -> None:
        nonlocal pending_cid
        if pending_cid is None:
            return
        text = "".join(pending)
        if pending_cid == int(RenderCommand.CONTENT):
            out.append(ContentCmd(text=text))
        else:
            out.append(ReasoningCmd(text=text))
        pending_cid = None
        pending.clear()

    for cmd in commands:
        cid = int(getattr(cmd, "cid", -1))
        if cid in _COALESCE_TEXT_CIDS and type(cmd) in (ContentCmd, ReasoningCmd):
            if pending_cid is not None and pending_cid != cid:
                _flush()
            pending_cid = cid
            pending.append(getattr(cmd, "text", "") or "")
            continue
        _flush()
        out.append(cmd)
    _flush()
    return out


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
    RenderCommand.COMPACTION: _do_compaction,
}

__all__ = ["apply_cmd", "coalesce_commands", "build_user_line", "build_assistant_line"]
