"""AppModel 工具输出 Mixin — 工具 box 生命周期（open/append/close）。

模块边界（2026-08-05 架构优化）：从 ``app/model.py`` 拆分——工具输出处理
（开放工具 box 的创建/追加/修剪/关闭/已提交行替换）独立为 mixin，
``AppModel(_ToolOutputMixin)`` 组合。mixin 方法操作 ``self`` 状态
（``tool_boxes``/``committed_lines``/``blocks``），依赖宿主提供的
``append_block``/``commit_open_block``/``commit_block``/``_block_to_ink_lines``
（AppModel 主类实现）。

日志名保持 ``src.tui.app.model``（外部 caplog/过滤按旧名监听）。
"""

from __future__ import annotations

import logging
import time

# ★ 状态常量/辅助来自 model 门面（re-export 自 _state_types/_model_helpers）——
#   避免 mixin 反向依赖 model 主模块造成循环；toolcard 行生成经函数内惰性
#   import（toolcard 零依赖，无循环风险）。
from src.tui.app._state_types import ChatBlock
from src.tui.app._model_helpers import (
    _TOOL_INCREMENTAL_THRESHOLD,
    _BASH_OUTPUT_TAIL_LINES,
    _TOOL_HEAD_TOOLS,
    _TOOL_HEAD_LINES,
    _bash_output_tail_lines,
    _tool_head_lines,
    _tool_head_tools,
    _tool_incremental_threshold,
    _single_line_detail,
)
# ★ ToolCard React Ink 组件化：工具卡行生成/状态前缀收敛到 app/toolcard.py
#   （模块级零依赖，函数内惰性 import——无循环风险）。
from src.tui.app.toolcard import (
    _tool_running_prefix_text,
    _tool_md_content_width,
    tool_card_lines,
)
# core.style 为 Layer 0 底层（无 app 依赖），模块级 import 无循环风险；
# 用于模块级样式常量（_S_TOOL_OUT 工具输出前缀色）。
from src.tui.core.style import Style

_logger = logging.getLogger("src.tui.app.model")

#: 工具输出行前缀样式（append_tool_output 每段输出共用；模块级单例复用——
#   修复前每段新建 ``Style(fg=242)``，长工具输出数万行时无谓分配）
_S_TOOL_OUT = Style(fg=242)


class _ToolOutputMixin:
    """AppModel 工具输出行为 mixin（工具 box 生命周期）。"""

    def open_tool_box(self, tool_id: str, tool_name: str, detail: str = "") -> ChatBlock:
        """打开一个工具分组：卡片标题行立即显示，输出增量追加（卡片化）。

        方向D 步骤15：extra 记录工具状态（running）与标题行 detail
        （``tool_detail``）；输出行不再增量提交 committed_lines（关闭时统一
        提交/冻结，避免 committed_lines 与块状态不一致）。

        防孤儿卡（同一 tool_id 重复 open）：非空 tool_id 已存在开放 box 时
        **复用**——修复前直接新建块并覆盖 ``tool_boxes[tool_id]``，旧块成为
        孤儿（永不关闭、无主体，只渲染一个 `● ⚙ 工具` 标题行，TUI 显示多一行）。
        触发场景：同一 tool_call_id 重复 ToolStartedEvent（重试/重复投递），
        或 append_tool_output 兜底建 box 后 ToolStartedEvent 后到。复用并更新
        标题/状态（如兜底 box 的 tool_name="" → 后到 open 补全 Bash·detail）。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        from src.tools.registry import get_tool_display_name
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        # 工具卡片标题行 detail 数据源（tool_card_lines 消费）；
        # ★ bash 多行命令 detail 含 \n——强制单行转义（对齐 _single_line 契约，
        #   防 \n 拆破单行标题行）。title/active_tool 复用转义后值（同源单行）。
        detail = _single_line_detail(detail)
        if tool_id:
            existing = self.tool_boxes.get(tool_id)
            if existing is not None:
                # 复用已开放 box：更新工具名/状态/detail + 标题行
                # （live 渲染下一帧生效；开放 box 未提交，更新安全）
                # ★ P3（review 2026-08-18）：复用路径检测**兜底空 box**
                #   （``append_tool_output`` 未知 tool_id 兜底建的
                #   ``open_tool_box(tool_id, "")``——原 tool_name 为空且无
                #   主体输出）时重置 ``_tool_started_at``——兜底 box 的开始
                #   时间是首个输出到达时间，后到的真实 ToolStartedEvent 才
                #   是执行开始，耗时自真实开始起算（纯重复投递场景——
                #   原本就有 tool_name——保持首次时间不变，防重复事件刷耗时）。
                was_fallback_empty = (
                    not existing.extra.get("tool_name")
                    and len(existing.lines) <= 1
                )
                existing.extra["tool_name"] = tool_name
                existing.extra["tool_status"] = "running"
                existing.extra["tool_detail"] = detail
                if was_fallback_empty:
                    existing.extra["_tool_started_at"] = time.monotonic()
                title = f"  \u00b7 {display}"
                if detail:
                    title = f"  \u00b7 {display} \u00b7 {detail}"
                if existing.lines:
                    # ★ P2-10（双数据源说明）：``lines[0]`` 为**数据层保留
                    #   字段**——渲染（tool_card_lines）实际读
                    #   ``block.extra["tool_name"/"tool_detail"]``（标题行在
                    #   渲染期重建），此处同步更新 lines[0] 仅为保持模型层
                    #   不变式（``block.lines[0].plain.startswith("  · ")``
                    #   等测试断言依赖），非渲染数据源。
                    existing.lines[0] = AnsiLine.of(title, Style(fg=23, bold=True))
                # ★ BUG-22（review 方向）：已增量提交过的 box（输出 > 阈值，
                #   标题行已在 committed_lines）复用更新标题时**同步重建
                #   committed_lines 标题行**——修复前仅更新块内标题行，
                #   渲染仍显示旧标题（如兜底 box 的空工具名）。
                #   ★ BUG-30（review 方向）：经 ``_replace_committed_line``
                #   替换新 Line + 列表身份变化——修复前直接 ``committed_lines[offset]
                #   = Line(...)`` 替换元素但列表身份不变 → 前缀缓存命中返回旧
                #   元素 → 新标题不上屏（与 close_tool_box 图标翻转同根因）。
                if existing.committed_line_count > 0:
                    offset = existing.extra.get("_first_committed_offset")
                    if offset is not None and 0 <= offset < len(self.committed_lines):
                        from src.tui.ink import Line
                        head = tool_card_lines(
                            existing, getattr(self, "width", 0), 0, None,
                        )
                        if head:
                            self._replace_committed_line(offset, Line(head[0]))
                self.active_tool = {
                    "name": display, "detail": detail, "status": "running",
                    "tool_name": tool_name or "",
                }
                return existing
        block = self.append_block("tool")
        block.extra["tool_id"] = tool_id or ""
        block.extra["tool_name"] = tool_name
        block.extra["tool_status"] = "running"
        block.extra["tool_detail"] = detail
        # ★ BEAUTY-35（状态行元信息）：记录工具开始时间戳——close_tool_box
        #   关闭时计算耗时（``_tool_duration``）。Claude Code 极简样式后渲染
        #   层不显示独立状态行（状态由标题行图标表达），耗时字段保留供内部/
        #   测试消费。
        #   复用路径（同一 tool_id 重复 open 防重复投递）不重置——保持首次
        #   开始时间，避免重复事件刷新耗时。
        block.extra["_tool_started_at"] = time.monotonic()
        title = f"  \u00b7 {display}"
        if detail:
            title = f"  \u00b7 {display} \u00b7 {detail}"
        block.lines.append(AnsiLine.of(title, Style(fg=23, bold=True)))
        # 方向1 B8：记录实际存储 key（非空 tool_id 即自身；空 tool_id 场景为
        # _next_tool_id() 生成值）。``_box_key`` 记录**原始传入 tool_id**——
        # 非空时即实际存储 key（tool_boxes 按原 id 存取）；空 id 场景为 ""，
        # 供 ``close_tool_box("")`` 按空 id 匹配匿名 box 关闭（修复空 tool_id
        # box 泄漏：旧实现空 id open 存于生成 key，close("") 永远 pop 不到）。
        key = tool_id or self._next_tool_id()
        block.extra["_box_key"] = tool_id
        self.tool_boxes[key] = block
        # Claude TUI parity 步骤 2.2：记录进行中工具（ToolStatusHeader 消费）
        self.active_tool = {
            "name": display, "detail": detail, "status": "running",
            "tool_name": tool_name or "",
        }
        return block

    def append_tool_output(self, tool_id: str, text: str, chat_hidden: bool = False) -> None:
        """追加工具输出行到对应分组（卡片主体行）。

        ``chat_hidden=True``（用户需求：read_file 聊天区工具卡只显示标题行）——
        追加的行登记到 ``block.extra["_chat_hidden_lines"]``，聊天区工具卡渲染
        时跳过这些行（内容仍保留在 ``block.lines``，轨迹 Trace / 详情视图可见）。

        方向4（开放工具块增量提交）：输出行数超过阈值
        （``_TOOL_INCREMENTAL_THRESHOLD``）时经 ``commit_open_block`` 增量提交
        已闭合行到 committed_lines——长工具输出每帧不再全量重渲染（开放块只
        渲染未提交尾）；关闭时 ``commit_block`` 追加剩余尾（状态行数据行
        渲染时跳过——状态由标题行图标表达），
        ``committed_line_count`` 计数保证不重复（「关闭后无重复行」不变量）。

        Bug A 修复：按 tool_id 精确路由——key 命中精确追加；key 未命中且
        tool_id 非空 → 创建匿名 box（标题回退「工具」，输出不丢失）；
        tool_id 为空 → 丢弃并 debug 日志（无归属输出不静默错路由）。
        """
        from src.renderer.ansi.helpers import AnsiLine, ansi_to_line
        # ★ 输出归属解析（空工具卡防御 + 未知 tool_id 兜底建 box；与
        #   ``append_tool_markdown`` 共用 ``_output_target_block``）：
        #   - tool_id 为 "assistant"：工具上下文之外的 print_to_terminal 回退
        #     （如后台任务完成提示），不归属任何工具 box——兜底创建空「工具」
        #     卡会永不闭合；直接丢弃（上层 _on_tool_output 已过滤，防御冗余）；
        #   - tool_id 为空：无归属输出丢弃；
        #   - tool_id 未知：创建匿名兜底 box（输出不丢失）。
        block = self._output_target_block(tool_id, text, "append_tool_output")
        if block is None:
            return
        # ★ 用户需求（read_file 聊天卡隐藏内容）：chat_hidden 输出行登记到
        #   ``_chat_hidden_lines``（行对象引用）——聊天卡渲染跳过（数据保留，
        #   Trace 可见）。非 hidden 调用零开销（hidden_rows 保持 None）。
        hidden_rows = (
            block.extra.setdefault("_chat_hidden_lines", []) if chat_hidden else None
        )
        segs = text.split("\n")
        # ★ BUG-78（工具卡尾部空行）：工具输出常以 ``\n`` 结尾（bash/ls 等
        #   命令回显）——``split("\n")`` 产生尾部空 segment，追加后渲染为
        #   「│ 」空引导行（占用卡片一行、视觉多余）。剔除末尾空 segment
        #   （中间空行保留——段落/结构分隔语义）；与 ``_trim_tool_output_head``
        #   既有的「剔除尾空行」语义对齐。空输出（``""``）剔除后不追加行。
        while segs and segs[-1] == "":
            segs.pop()
        for seg in segs:
            l = AnsiLine.of("  ", _S_TOOL_OUT)
            # ★ 工具输出可能含 Rich/pygments 高亮 ANSI 序列（read_file 等）。
            #   原样保留进 Run.text 会让宽度测量把转义码当可见字符（宽度膨胀→
            #   误触发 wrap），wrap_line 逐字符截断把转义序列拦腰截断（如残留
            #   ;49;00m）渲染错乱。经 ansi_to_line 解析为带样式 Run，宽度测量
            #   与 wrap 按样式安全处理。
            for r in ansi_to_line(seg).runs:
                l.append_run(r)
            block.lines.append(l)
            if hidden_rows is not None:
                hidden_rows.append(l)
        # bash/execute_command：输出超过阈值行数时只保留最后 N 行（tail 显示，
        # 对齐 Claude Code 收敛冗长 bash 输出；修剪后行数 ≤ N+1，不触发增量提交）
        if block.extra.get("tool_name") in ("bash", "execute_command"):
            self._trim_tool_output_tail(block, _bash_output_tail_lines())
        # find/search/ls/read_file：输出超过阈值行数时只保留前 N 行（head 显示，
        # 对齐终端 head 语义——目录列表/文件预览等有序输出看开头即可，防卡片撑爆）
        if block.extra.get("tool_name") in _tool_head_tools():
            self._trim_tool_output_head(block, _tool_head_lines())
        # ★ 用户需求：trim 删除行后同步清理聊天卡隐藏行登记——被删行对象滞留
        #   会被 id() 复用误判（隐藏错误行）。read_file 走 head trim。
        if hidden_rows is not None:
            self._prune_chat_hidden(block)
        # ★ 方向4：增量提交阈值——长工具输出不每帧全量重渲染（超过阈值即提交
        #   已闭合行到 committed_lines；开放块渲染只取未提交尾）。
        if len(block.lines) - block.committed_line_count >= _tool_incremental_threshold():
            self.commit_open_block(block)

    def _output_target_block(self, tool_id: str, text: str, caller: str):
        """解析工具输出归属块（``append_tool_output`` / ``append_tool_markdown`` 共用）。

        归属策略（空工具卡防御 + 未知 id 兜底，单一真源）：

          - ``tool_id == "assistant"``：工具上下文之外的 ``print_to_terminal``
            回退（如后台任务完成提示），不归属任何工具 box——返回 None
            （上层调用方丢弃；兜底创建空「工具」卡会永不闭合）；
          - ``tool_id`` 为空：无归属输出，返回 None（debug 日志）；
          - ``tool_id`` 未知（box 不存在）：创建匿名兜底 box（输出不丢失，
            后续真实 ToolStartedEvent 到达时经 open_tool_box 复用补全标题）。

        Args:
            tool_id: 工具调用 ID。
            text: 输出文本（仅用于 debug 日志截断展示）。
            caller: 调用方名（日志前缀）。

        Returns:
            归属的 ChatBlock；应丢弃时返回 None。
        """
        if tool_id == "assistant":
            _logger.debug("%s: 无归属输出（assistant），丢弃: %.80s", caller, text)
            return None
        block = self.tool_boxes.get(tool_id)
        if block is None:
            if not tool_id:
                _logger.debug("%s: 收到空 tool_id，输出丢弃: %.80s", caller, text)
                return None
            block = self.open_tool_box(tool_id, "")
        return block

    def append_tool_markdown(self, tool_id: str, text: str) -> None:
        """追加工具卡内 **markdown** 输出（流式 markdown 渲染为卡片正文）。

        与 ``append_tool_output``（纯文本行，逐行写入 ``block.lines``）并列：
        markdown 源文本经 ``AnsiStreamRenderer`` 增量渲染为 ``AnsiLine``，累积到
        ``block.extra["_tool_md_lines"]``（渲染行缓冲）+ ``_tool_md_preview``
        （未闭合尾预览）——**不写入** ``block.lines``（保持其为纯文本数据源，
        避免与行截断/隐藏行/增量提交逻辑纠缠）。``toolcard.tool_card_lines``
        渲染卡片正文时优先取该缓冲（``│ `` 引导线 + 单行截断 + 满宽背景，
        与既有卡片视觉一致）。

        渲染器实例 / 源文本 / 渲染宽度随块保存（``_tool_md_renderer`` /
        ``_tool_md_source`` / ``_tool_md_render_width``）：终端 resize（宽度
        变化）时下一次追加经 ``_tool_md_content_width`` 检测并重放源文本，
        ``toolcard`` 侧亦会按新宽度整块重渲染（见 ``_tool_md_body_lines``）。

        关闭时由 ``close_tool_box`` 收尾（``_finalize_tool_md``：``close()``
        刷出未闭合段落/代码块残差）。可多次调用（逐块流式发布）。

        Args:
            tool_id: 工具调用 ID（归属解析同 ``append_tool_output``）。
            text: markdown 源文本块。
        """
        if not text:
            return
        block = self._output_target_block(tool_id, text, "append_tool_markdown")
        if block is None:
            return
        from src.renderer.ansi import AnsiStreamRenderer
        width = _tool_md_content_width(getattr(self, "width", 0))
        source_so_far = block.extra.get("_tool_md_source", "")
        renderer = block.extra.get("_tool_md_renderer")
        if renderer is None or block.extra.get("_tool_md_render_width") != width:
            # 首块 / 宽度变化：新建渲染器并把已累积源文本重放（流式状态连续；
            # 宽度变化时历史行按新宽度重排）。重放会重建**全部**已渲染行，
            # 故先清空行缓冲——避免与旧缓冲重复（宽度变化场景旧行已入缓冲）。
            renderer = AnsiStreamRenderer(width=width)
            block.extra["_tool_md_lines"] = []
            block.extra["_tool_md_preview"] = []
            if source_so_far:
                renderer.write(source_so_far)
            block.extra["_tool_md_renderer"] = renderer
            block.extra["_tool_md_render_width"] = width
        renderer.write(text)
        block.extra["_tool_md_source"] = source_so_far + text
        self._sync_tool_md_lines(block)

    def _sync_tool_md_lines(self, block) -> None:
        """从 markdown 渲染器取出新行并入缓冲（``_tool_md_lines`` / 预览）。

        渲染器缓冲语义与主内容通道一致（``take_lines`` 消费已确定行、
        ``take_preview_lines`` 返回未闭合块预览快照）——合并进块缓冲并递增
        ``_tool_md_version``（toolcard 帧/正文缓存据此失效重建）。
        """
        renderer = block.extra.get("_tool_md_renderer")
        if renderer is None:
            return
        lines = block.extra.get("_tool_md_lines")
        if lines is None:
            lines = []
        lines.extend(renderer.take_lines())
        block.extra["_tool_md_lines"] = lines
        take_preview = getattr(renderer, "take_preview_lines", None)
        block.extra["_tool_md_preview"] = (
            list(take_preview()) if callable(take_preview) else []
        )
        block.extra["_tool_md_version"] = block.extra.get("_tool_md_version", 0) + 1

    def _finalize_tool_md(self, block) -> None:
        """工具关闭时收尾 markdown 渲染器（``close()`` 刷出残差后释放实例）。

        主内容通道关闭同语义：``close()`` 让解析器 flush 残差（未闭合段落/
        代码块尾部）并渲染为行，随后追加进 ``_tool_md_lines``。无 markdown
        输出（未走 ``append_tool_markdown``）时零成本跳过。
        """
        renderer = block.extra.get("_tool_md_renderer")
        if renderer is None:
            return
        try:
            renderer.close()
        except Exception:
            _logger.debug("工具卡 markdown 渲染器关闭异常", exc_info=True)
        self._sync_tool_md_lines(block)
        block.extra.pop("_tool_md_renderer", None)

    def _drop_tool_body_cache(self, block, line) -> None:
        """从工具卡内容行缓存中移除行键（trim 删除行后同步清理，P1-1）。

        ``_tool_card_body_cache`` 为 dict（键=``(AnsiLine 行对象, width, bg)``
        元组——toolcard.py ``tool_card_lines`` 写入，值=超宽行截断（单行 +
        省略号）+ 背景填充结果 runs）——
        trim 从 ``block.lines`` 删除行后若不同步 pop，被删行对象仍被 cache
        持有直到工具 box 关闭（长输出工具在 box 存活期内内存线性增长）。
        键按 ``k[0] is line`` 身份匹配（同一行对象可能以不同 width 多次入
        缓存，逐一删除）；cache 未建立（None）时零开销返回。
        """
        body_cache = getattr(block, "_tool_card_body_cache", None)
        if body_cache is not None:
            # ★ 修复（P1）：缓存键为 ``(ansi_line, width, bg)`` 元组（toolcard.py
            #   ``tool_card_lines`` 写入）——修复前 ``body_cache.pop(line, None)``
            #   用单个 AnsiLine 作键永不命中，被 trim 删除的行对象仍被缓存持有
            #   （长输出工具内存线性增长）。遍历删除键首元素 is line 的条目。
            for k in [k for k in body_cache if k[0] is line]:
                body_cache.pop(k, None)

    def _prune_chat_hidden(self, block) -> None:
        """清理聊天卡隐藏行登记中已不在 ``block.lines`` 的行（trim 删除后同步）。

        ``_chat_hidden_lines`` 存行对象引用（``toolcard.tool_card_lines`` 按
        ``id()`` 判定跳过）——trim 从 ``block.lines`` 删除行后若不同步清理，
        被删行对象仍被列表持有（长工具内存滞留），且对象释放后 ``id()`` 可能
        被新行复用导致误隐藏。按 ``id`` 集合做 O(N) 重建（低频：仅隐藏输出
        追加后调用一次）。
        """
        hidden = block.extra.get("_chat_hidden_lines")
        if not hidden:
            return
        live = {id(l) for l in block.lines}
        block.extra["_chat_hidden_lines"] = [l for l in hidden if id(l) in live]

    def _trim_tool_output_tail(self, block, keep: int) -> None:
        """工具块输出修剪为最后 keep 行（保留标题行 block.lines[0]）。

        bash 尾显示：输出超过 keep 行时删除前置输出行（下标 1..N-keep），
        累计省略数记入 ``block.extra["_bash_omitted_lines"]``（卡片渲染时
        前置「… 前 N 行省略」提示）；同步 ``committed_line_count``（已提交行
        被删则回退计数，防越界/重复提交）。修剪后行数 ≤ 1+keep，远低于增量
        提交阈值 → 无增量提交。

        方向3（trim 与增量提交协同）：已提交前缀（``committed_line_count`` 行）
        不可删除——删除会令 committed_lines 前缀与块行映射错位。★ P2（review）：
        已提交场景改为修剪其**之后的未提交尾部**（下标 >= committed_line_count
        可安全删除，计数不变），不再整体跳过（修复前「已提交即永不修剪」致
        块行随输出线性增长）。
        """
        lines = block.lines
        if len(lines) <= 1 + keep:
            return
        # ★ P2（review）：已增量提交的块改为修剪**未提交尾部**——修复前直接
        #   return（「已提交即永不修剪」），长输出工具（空名兜底 box 输出
        #   >64 行触发增量提交后补全工具名）后续 `block.lines` 随输出线性
        #   增长（渲染/内存无上限）。已提交前缀不可删（committed_lines 映射），
        #   但下标 >= committed_line_count 的未提交行可安全删除（计数不变）。
        committed = block.committed_line_count
        if committed > 0:
            pending = len(lines) - committed
            if pending <= keep:
                return
            del_count = pending - keep
            removed = lines[committed:committed + del_count]
            for line in removed:
                self._drop_tool_body_cache(block, line)
            del lines[committed:committed + del_count]
            block.extra["_bash_omitted_lines"] = (
                block.extra.get("_bash_omitted_lines", 0) + del_count
            )
            return
        del_count = len(lines) - 1 - keep
        # ★ P1-1（工具输出缓存无界增长）：删除前先捕获被删行引用并同步清理
        #   ``_tool_card_body_cache``（dict，键=行对象）——修复前仅删除
        #   block.lines 中的行，被删行对象仍被 cache 持有直到工具 box 关闭
        #   （长输出工具在 box 存活期内内存线性增长）。
        removed = lines[1:1 + del_count]
        for line in removed:
            self._drop_tool_body_cache(block, line)
        del lines[1:1 + del_count]
        block.extra["_bash_omitted_lines"] = (
            block.extra.get("_bash_omitted_lines", 0) + del_count
        )

    def _trim_tool_output_head(self, block, keep: int) -> None:
        """工具块输出修剪为前 keep 行（保留标题行 block.lines[0]）。

        find/search/ls/read_file 头显示：输出超过 keep 行时删除后置输出行
        （下标 1+keep..末尾），累计省略数记入 ``block.extra["_head_omitted_lines"]``
        （卡片渲染时在主体行后置「… 后 N 行省略」提示）；同步
        ``committed_line_count``（已提交行被删则回退计数，防越界/重复提交）。
        修剪后行数 ≤ 1+keep，远低于增量提交阈值 → 无增量提交。

        方向3（trim 与增量提交协同）：已提交前缀（``committed_line_count`` 行）
        不可删除——删除会令 committed_lines 前缀与块行映射错位。★ P2（review）：
        已提交场景改为仅保留「已提交前缀 + 前 keep 行未提交内容」，删除其后
        未提交行（计数不变），不再整体跳过（与 ``_trim_tool_output_tail`` 一致）。
        """
        lines = block.lines
        # ★ P2（review）：已增量提交 → 修剪未提交尾部（见 docstring）。
        committed = block.committed_line_count
        if committed > 0:
            keep_end = committed + keep
            if len(lines) <= keep_end:
                return
            del_count = len(lines) - keep_end
            removed = lines[keep_end:]
            for line in removed:
                self._drop_tool_body_cache(block, line)
            del lines[keep_end:]
            block.extra["_head_omitted_lines"] = (
                block.extra.get("_head_omitted_lines", 0) + del_count
            )
            return
        # 尾部换行符产生的空行（text.split("\n") 尾空 seg → 仅前缀的空行）不
        # 算内容行——先剔除，避免「前 N 行」计数被尾空行占位（如 read_file
        # 整文件输出以 \n 结尾时尾空行无意义，会挤占前 3 行显示位）。
        # ★ P1-1（同 tail trim）：删除行同步清理 ``_tool_card_body_cache``
        #   行键，防被删行对象被缓存持有（内存线性增长）。
        while len(lines) > 1 and lines[-1].plain.strip() == "":
            self._drop_tool_body_cache(block, lines[-1])
            del lines[-1]
        if len(lines) <= 1 + keep:
            return
        del_count = len(lines) - (1 + keep)
        removed = lines[1 + keep:]
        for line in removed:
            self._drop_tool_body_cache(block, line)
        del lines[1 + keep:]
        block.extra["_head_omitted_lines"] = (
            block.extra.get("_head_omitted_lines", 0) + del_count
        )

    def close_tool_box(self, tool_id: str, success: bool) -> None:
        """关闭工具分组：置状态、冻结并提交（工具卡片）。

        方向D 步骤15：
          - extra.tool_status = done/fail（卡片标题行状态图标原位翻转 ✔/✖）；
          - 关闭块冻结 _cached_ink_lines（跳过状态行数据行，免每帧 Style merge）。

        Bug A 修复：按 tool_id 精确 pop，不再 fallback 到 _current_tool_box
        （单值指针语义已移除）；找不到对应 box 时静默丢弃（debug 日志）。

        方向1 B8：空 tool_id 关闭——``pop("")`` 未命中且 tool_id 为空时遍历
        ``tool_boxes`` 按 ``_box_key == ""``（open 记录的原始空 id 标记）查找
        匿名 box 关闭（**正序取最早打开者**——P2-4：与打开顺序一致，防多空
        id 场景逆序弹栈错配）；找不到时静默丢弃（debug 日志）。
        修复空 tool_id box 泄漏。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        block = self.tool_boxes.pop(tool_id, None)
        if block is None and not tool_id:
            # ★ P2-4（多空 tool_call_id 逆序弹栈）：空 id 匿名 box 按**打开
            #   顺序**（正序遍历）匹配关闭——修复前 reversed 逆序弹栈：
            #   多个空 tool_call_id 的 tool 结果消息连续关闭时 LIFO 与打开
            #   顺序相反（如 A→B 打开、B→A 关闭）→ 输出错配。正序 FIFO
            #   与打开顺序一致（先开先关）。
            for stored_key, candidate in self.tool_boxes.items():
                if candidate.extra.get("_box_key") == "":
                    block = self.tool_boxes.pop(stored_key)
                    break
        if block is None:
            _logger.debug(
                "close_tool_box: 未找到 tool_id=%r 的工具 box，静默丢弃", tool_id,
            )
            return
        # ★ markdown 工具卡收尾：关闭渲染器刷出未闭合段落/代码块残差（在冻结/
        #   提交前完成，保证卡片正文完整——见 ``_finalize_tool_md``）。
        self._finalize_tool_md(block)
        status = "\u2714" if success else "\u2716"
        # ★ BEAUTY-35（状态行元信息）：计算工具耗时（open 记录的开始时间戳 →
        # 关闭时差）。Claude Code 极简样式后渲染层不显示独立状态行（耗时字段
        # 保留供内部/测试消费）。无开始时间戳（旧块/外部构造）时跳过（防御）。
        started = block.extra.get("_tool_started_at")
        if started is not None:
            block.extra["_tool_duration"] = max(0.0, time.monotonic() - started)
        # 记录状态行下标（卡片渲染跳过该主体行——状态由标题行状态前缀表达；
        # 模型层不变式 block.lines[-1].plain.strip()=="✔" 保留）
        block.extra["_status_line_index"] = len(block.lines)
        block.lines.append(AnsiLine.of(f"  {status}", Style(fg=41 if success else 196)))
        block.extra["tool_status"] = "done" if success else "fail"
        # Claude TUI parity 步骤 2.2：关闭后无进行中工具（ToolStatusHeader 隔离
        # 测试仍消费 active_tool；app 组件树已移除该组件）
        self.active_tool = None

        # ★ 1.6 修复 + BUG-30（review 方向）修复：长工具输出（>
        #   _TOOL_INCREMENTAL_THRESHOLD 触发增量提交后标题行已在 committed_lines）
        #   关闭时更新 committed_lines 中标题行（前缀保留最终运行时间 + 元信息
        #   去掉耗时）。
        #   **BUG-30（渲染陈旧）**：修复前原地修改 ``top_line.runs``（保留 Line
        #   对象引用）——committed-chat 前缀缓存（``chat_view._paint`` 键
        #   ``(id(lines), n, box.y)``）与 diff 身份短路（``p is f`` → 相等跳过）
        #   都按「Line 对象身份 = 内容不变」优化：内存中 Line 虽更新，但
        #   prev 帧与 new 帧引用同一 Line 对象 → 渲染器认为无差异 → **终端标题行
        #   恒显示旧内容**（必现，长工具输出触发增量提交后关闭必现）。
        #   修复：**新建 Line 对象替换**（不复用旧对象）+ ``_replace_committed_line``
        #   令 committed_lines 列表身份变化（浅拷贝）→ 前缀缓存键中 ``id(lines)``
        #   失效 → 下一帧重建前缀 → diff 对新 Line 对象做 runs 值比较 → 标题行
        #   被重写。短工具（未增量提交，offset 不存在）关闭时经 commit_block
        #   提交的标题行已按关闭态生成，无需更新。
        #   卡片结构：``_first_committed_offset`` 指向卡片**首行（标题行）**。
        offset = block.extra.get("_first_committed_offset")
        if offset is not None and 0 <= offset < len(self.committed_lines):
            # ★ 2026-10-09（用户需求）：完成后标题行前缀保留最终运行时间
            #   （不再换成 ✔/✖），且尾部元信息去掉耗时——标题行内容整体变化
            #   （前缀文本 + 元信息），故按当前状态**整行重建**（真源 =
            #   block.extra 的 tool_name/tool_detail + 模型状态），替代旧的
            #   「原位替换状态图标 run」（无法增删元信息、且时间/图标宽度不同
            #   需另行钳制宽度）。重建行自带宽度钳制与满宽背景
            #   （``tool_card_lines`` 内 ``_apply_line_bg``）。
            #   ★ BUG-30：``_replace_committed_line`` 新建 Line 对象 + 令
            #   committed_lines 列表身份变化 → 前缀缓存失效 → 下一帧重写标题
            #   行；不变量：``_first_committed_offset`` 为卡片首行（tool 块无
            #   角色头 → 即标题行）。
            head = tool_card_lines(block, getattr(self, "width", 0), 0, 1)
            if head:
                from src.tui.ink import Line
                self._replace_committed_line(offset, Line(head[0]))

        block.closed = True
        # ★ 方向4（增量提交协同）：冻结仅**未提交部分**（已提交行在
        #   committed_lines 中，避免重复存储；``_block_styled_lines`` 冻结
        #   缓存分支已调整为 ``cache[0:]``——冻结缓存即未提交部分，start 参数
        #   对冻结缓存无意义）。关闭后 ``commit_block`` 追加剩余尾（状态行数据
        #   行渲染时跳过），``committed_line_count`` 计数保证不重复追加已提交行。
        block._cached_ink_lines = self._block_to_ink_lines(block, block.committed_line_count)
        block._open_styled_cache = None  # 冻结后开放缓存不再需要
        self.commit_block(len(self.blocks) - 1)
        # ★ PERF-6：清理工具卡缓存须在 ``commit_block`` **之后**——commit_block
        #   内部 ``_block_to_ink_lines``（tool 分支）会经 ``tool_card_lines``
        #   重建缓存（close_tool_box 提前清理会被重建覆盖）。关闭块冻结后渲染走
        #   ``_cached_ink_lines``，不再访问 tool 卡缓存，此处无条件释放。
        block._tool_card_body_cache = None
        block._tool_card_frame_cache = None
        block._tool_card_body_lines_cache = None

    def _replace_committed_line(self, offset: int, new_line) -> None:
        """替换 committed_lines[offset] 并令列表身份变化（已提交行原地更新）。

        已提交行（committed_lines）被 committed-chat 前缀缓存（``chat_view._paint``
        键 ``(id(lines), n, box.y)``）与 diff 身份短路引用——**原地替换元素但保持
        列表身份**时前缀缓存不失效、渲染输出陈旧（BUG-30 同族）。本方法经
        ``self.committed_lines = self.committed_lines.copy()`` 浅拷贝令 ``id(lines)``
        变化 → 前缀缓存失效 → 下一帧重建前缀（新前缀引用新 Line 对象）→ diff 做
        runs 值比较 → 目标行被重写。

        与 ``commit_block``/``commit_open_block`` 的原地 ``extend`` 语义正交：
        追加新行保持列表身份（前缀缓存命中仅追加新增行，零重建）；本方法仅在
        更新**已提交行内容**时触发（低频：工具状态图标翻转/标题更新）。
        """
        if not (0 <= offset < len(self.committed_lines)):
            return
        new_list = list(self.committed_lines)
        new_list[offset] = new_line
        self.committed_lines = new_list

    def refresh_running_tool_titles(self) -> None:
        """运行中工具卡已提交标题行的运行时间实时刷新（2026-10-09 用户需求）。

        需求：运行中的工具以**实时运行时间**替代 ``●`` 图标（如 ``0.1
        UserSelect``），所有工具一致、实时刷新。

        未增量提交的工具卡标题行由 ``ToolCard`` 组件每帧渲染（运行时间随帧
        重算），无需本方法；**已增量提交**（输出超过阈值后标题行进入
        ``committed_lines``）的工具卡标题行是静态 Line，须由渲染帧主动刷新
        ——否则长时间运行的工具卡时间会冻结在提交时刻。

        实现：遍历开放工具 box，对「运行中 + 已提交标题行」的块按
        ``format_elapsed`` 量化文本比较（0.1s 粒度），变化时经
        ``tool_card_lines(block, width, 0, 1)`` 重建标题行（只取标题行）并
        ``_replace_committed_line`` 替换（新建 Line + 列表身份变化，committed
        前缀缓存失效 → 下一帧重绘）。时间文本未变（同 0.1s 桶）时零开销
        返回；非运行中/未提交/无时间戳的块跳过。

        幂等；每帧由渲染循环调用（宿主钩子，异常由调用方兜底）。
        """
        boxes = getattr(self, "tool_boxes", None)
        if not boxes:
            return
        from src.tui.ink import Line
        width = getattr(self, "width", 0)
        for block in list(boxes.values()):
            if block.closed or block.extra.get("tool_status") != "running":
                continue
            # 标题行尚未提交（live 渲染每帧重算运行时间）→ 无需刷新。
            if block.committed_line_count <= 0:
                continue
            offset = block.extra.get("_first_committed_offset")
            if offset is None or not (0 <= offset < len(self.committed_lines)):
                continue
            text = _tool_running_prefix_text(block)
            if text is None:
                continue
            # ★ 0.1s 粒度去重：同桶（时间文本相同）不重复替换（免每帧拷贝
            #   committed_lines + 前缀缓存失效）。
            if text == block.extra.get("_committed_running_prefix"):
                continue
            block.extra["_committed_running_prefix"] = text
            head = tool_card_lines(block, width, 0, 1)
            if head:
                self._replace_committed_line(offset, Line(head[0]))

    def close_empty_tool_boxes(self) -> int:
        """自动闭合开放但无主体内容的空工具 box，返回闭合数量。

        ★ 空工具卡防御：后台任务等非工具上下文输出可能经兜底创建只有标题行
        （``block.lines`` 仅 1 行标题）的空「工具」box（● 工具），这类
        box 永远不会有 ToolCloseCmd。每轮对话结束（round_end）时调用本方法，
        将空 box 以完成态关闭（闭合后渲染为 ``● 工具``，无边框/无独立状态行
        ——2026-08-06 去边框 + Claude Code 极简样式），避免空卡永久保持
        ● running 悬挂。
        """
        closed = 0
        for tool_id in list(self.tool_boxes.keys()):
            block = self.tool_boxes.get(tool_id)
            if block is None or block.closed:
                continue
            # 空 box：只有标题行（lines[0]），无主体输出内容
            if len(block.lines) <= 1:
                self.close_tool_box(tool_id, True)
                closed += 1
        return closed

    def _next_tool_id(self) -> str:
        self._tool_id_seq += 1
        return f"tool-{self._tool_id_seq}"


__all__ = ["_ToolOutputMixin"]
