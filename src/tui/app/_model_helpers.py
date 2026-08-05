"""AppModel 模块级渲染辅助（从 model.py 拆分，2026-08-05 架构优化）。

职责：聊天块**卡片渲染**的纯辅助函数与常量——不承载 AppModel 状态/行为，
仅提供块 → ink Line 的转换（角色头/用户前缀/工具修剪阈值）。

从 ``model.py`` 拆分独立，使模型行为（AppModel 方法）与渲染辅助分层清晰。
被 ``model.py`` 与 ``app/chat_view.py`` 消费（经 model re-export 或直连）。
"""

from __future__ import annotations

from src.tui._format import single_line

__all__ = [
    "_TOOL_INCREMENTAL_THRESHOLD",
    "_BASH_OUTPUT_TAIL_LINES",
    "_TOOL_HEAD_TOOLS",
    "_TOOL_HEAD_LINES",
    "_single_line_detail",
    "_user_marker_styled_lines",
    "_role_header_runs",
    "_role_header_line",
]

#: 开放工具块增量提交阈值（方向4）——输出行超出该阈值时经 commit_open_block
#: 增量提交已闭合行到 committed_lines（长工具输出每帧不再全量重渲染）。
_TOOL_INCREMENTAL_THRESHOLD = 64

#: bash/execute_command 工具输出尾显示行数——超过该行数时只保留最后 N 行
#: （对齐终端 ``tail`` 语义；bash 输出常为冗长命令回显/构建日志，防卡片撑爆）。
_BASH_OUTPUT_TAIL_LINES = 3

#: 头显示工具集合——find/search/ls/read_file 输出超过阈值行数时只保留前 N 行
#: （对齐终端 ``head`` 语义；目录列表/文件预览等有序输出看开头即可，防卡片撑爆）。
_TOOL_HEAD_TOOLS = ("find", "search", "ls", "read_file")
_TOOL_HEAD_LINES = 3


def _single_line_detail(detail: str) -> str:
    """工具卡 detail 强制单行：换行/回车转义为字面量（``\\n``/``\\r``）。

    对齐 ``_subagent_render._single_line`` 单行契约——bash 命令参数可能含
    多行（``command`` 值携带 ``\\n``），直接放进单行边框（卡片顶边框/标题行）
    会被终端按物理换行拆成多行，撑破工具卡边框显示错乱；显示前转义为可见
    字面量 ``\\n``/``\\r``。★ 方向5：委托 ``_format.single_line`` 单一真源
    （三处单行契约收敛——model/_subagent_render/subagent_panel）。
    """
    return single_line(detail)


def _user_marker_styled_lines(block, start, stop, width):
    """用户消息每行 `> ` 标记（渲染期变换，每行 `> {segment}` 顶格）。

    输入为 ``build_user_line`` 产出的 AnsiLine（runs[0]=`> ` 前缀 run）：
    剥离前缀 run → 内容 wrap 到 ``inner=width-2`` → 重前缀（每段均 ``> ``，
    Claude Code 顶格列 0，续行同样带 ``> ``——≤ width 安全，满足行级 diff
    宽度不变量）。

    Args:
        block: 用户块（ChatBlock.kind == "user"）。
        start: 起始 AnsiLine 下标。
        stop: 结束下标（不含）；None 表示到块末尾。
        width: 文档宽度；<=0 时仅剥离前缀不换行。

    Returns:
        list[Line] — 用户消息 ink Line 列表。
    """
    from src.tui.app._theme import get_active_palette
    from src.tui.ink import Line, StyledRun
    from src.renderer.ansi.helpers import AnsiLine, Run, wrap_line
    pal = get_active_palette()
    icon = pal.user_icon
    width = width if isinstance(width, int) and width > 0 else 0
    inner = max(1, width - 2) if width > 0 else 0
    out: list[Line] = []
    for ansi_line in block.lines[start:stop]:
        runs = list(ansi_line.runs)
        # 剥离 `> ` 前缀 run（build_user_line 结构：runs[0] = 图标前缀）
        if runs and runs[0].text.startswith("> "):
            first = runs[0]
            if len(first.text) > 2:
                content_runs = [Run(first.text[2:], first.style)] + runs[1:]
            else:
                content_runs = runs[1:]
        else:
            content_runs = runs
        wrapped = (
            wrap_line(AnsiLine(content_runs), inner)
            if (width > 0 and content_runs)
            else ([AnsiLine(content_runs)] if content_runs else [])
        )
        if not wrapped:
            # 空消息 → 保留前缀行 `> `（不吞行）
            out.append(Line([StyledRun("> ", icon)]))
            continue
        for seg in wrapped:
            seg_runs = [StyledRun(r.text, r.style) for r in seg.runs if r.text]
            line = Line([StyledRun("> ", icon)] + seg_runs)
            # ★ 方向8（窄屏防溢出）：``> `` 前缀（2 列）+ 宽字符段可能超
            #   width（width<4 时 CJK 内容段宽 2，总宽 4 > width）——截断至
            #   width 保持行级 diff 宽度不变量（截断点不拆 CJK，与工具卡
            #   窄屏降级语义一致）。
            if width > 0 and line.width > width:
                from src.tui.ink.helpers import truncate_line
                line = truncate_line(line, width)
            out.append(line)
    return out


def _role_header_runs(block, model, live: bool = False) -> list:
    """构建块角色头 StyledRun 列表（卡片首行，按 kind 选样式与文本）。

    无头 kind（content/tool/user/write_line/splash/parse_info）返回空列表
    （不占行）——content 对齐 Claude Code 无头回答；tool 由卡片顶边框替代。
    样式取活动调色板槽位（``get_active_palette()``，dark 下与既有常量同值）；
    reasoning/error 用硬编码兜底（与正文样式语义一致）。

    Args:
        block: 聊天块。
        model: AppModel 实例（调色板解析）。
        live: True = **每帧渲染的 live 路径**（ChatView 未提交块）——推理头
            spinner 化 / 呼吸色生效；False = **提交/冻结路径**（_card_lines /
            _card_lines_committed）——回退静态样式（冻结缓存内容确定，防
            历史里固定显示随机 spinner 帧字符）。
    """
    from src.tui.app._theme import get_active_palette
    from src.tui.core.style import Style
    from src.tui.ink import StyledRun
    kind = block.kind
    pal = get_active_palette()
    if kind == "content":
        # 助手回答无角色头（对齐 Claude Code：markdown 文本直接流动，无
        # `▎回答` 头行；用户消息以 `> ` 前缀区分）
        return []
    if kind == "reasoning":
        # 方向3（动效）：推理块角色头呼吸色——块仍开放（live 推理中）时
        # 从暗灰 242 呼吸到亮灰 252（8s 周期，视觉提示「推理进行中」）；
        # 关闭提交后保持静态暗灰（frozen 缓存不再重算）。
        # ★ BEAUTY-27（2026-08-05 体验动效）：**live 渲染路径**（live=True）
        #   ``💭`` 图标替换为时间基 spinner 帧（10Hz 推进，与解析行 spinner
        #   共用语义）——推理进行中更生动；提交/关闭路径（live=False）回退
        #   静态 💭（冻结缓存内容确定，防历史思考头固定为随机 spinner 帧）。
        if not block.closed and live:
            from src.tui.app._theme import time_glow
            from src.tui.app import _fx
            glow = time_glow(242, 252, 8.0)
            sp = _fx.spinner_char()
            return [StyledRun(f"\u258d{sp} 思考", Style(fg=glow))]
        return [StyledRun("\u258d\U0001f4ad 思考", Style(fg=242))]
    if kind == "tool":
        # 工具卡片顶边框替代 `▎⚡ 工具 X` 角色头（卡片化对齐 Claude Code）；
        # 无头 → _card_lines 不前置独立头行，顶边框即卡片首行。
        return []
    if kind == "notification":
        # ★ BEAUTY-33（2026-08-05 体验动效）：通知角色头 live 渲染路径
        #   （live=True 且未关闭）呼吸——暗灰 242↔252 脉动（8s 周期，与
        #   推理头呼吸同步）；提交/关闭回退静态 pal.notice（冻结缓存确定）。
        if not block.closed and live:
            from src.tui.app._theme import time_glow
            glow = time_glow(242, 252, 8.0)
            return [StyledRun("\u258e", Style(fg=glow)), StyledRun("通知", Style(fg=glow))]
        return [StyledRun("\u258e", pal.notice), StyledRun("通知", pal.notice)]
    if kind == "error":
        # 方向3（动效）：错误标记呼吸色——错误消息醒目但不过度闪烁
        # （196 邻域 8s 周期）。错误块通常立即提交（frozen），呼吸仅在 live
        # 窗口生效，提交后保持静态红。
        if not block.closed:
            from src.tui.app._theme import time_glow
            glow = time_glow(196, 208, 8.0)
            return [StyledRun("\u258e错误", Style(fg=glow, bold=True))]
        return [StyledRun("\u258e错误", Style(fg=196, bold=True))]
    if kind == "subagent":
        # ★ BEAUTY-33：子代理角色头 live 渲染路径呼吸——暗灰 242↔252 脉动
        #   （8s 周期）；提交/关闭回退静态 pal.dim（冻结缓存确定）。
        if not block.closed and live:
            from src.tui.app._theme import time_glow
            glow = time_glow(242, 252, 8.0)
            return [StyledRun("\u258e", Style(fg=glow)), StyledRun("子代理", Style(fg=glow))]
        return [StyledRun("\u258e", pal.dim), StyledRun("子代理", pal.dim)]
    return []


def _role_header_line(block, model, width, live: bool = False) -> "Line | None":
    """构建块角色头行（单行，截断至 width 满足行级 diff 宽度不变量）。

    无头 kind 返回 None。头部必须单行且宽度 <= width（committed_lines 每行
    ink Line 宽度 <= width 不变量）；width<=0 时保持原样（防御）。

    Args:
        live: 透传 ``_role_header_runs``——True（ChatView 每帧渲染路径）启用
            推理头 spinner/呼吸；False（提交/冻结路径）回退静态（见
            ``_role_header_runs`` docstring）。
    """
    runs = _role_header_runs(block, model, live=live)
    if not runs:
        return None
    from src.tui.ink import Line
    from src.tui.ink.helpers import truncate_runs
    if width and width > 0:
        runs = truncate_runs(runs, width)
    return Line(runs)
