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
import os
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
    _GROUPABLE_TOOLS,
    _COLLAPSIBLE_GROUP_TOOLS,
    _write_bash_output_file,
    _single_line_detail,
)
# ★ ToolCard React Ink 组件化：工具卡行生成/状态图标收敛到 app/toolcard.py
#   （模块级零依赖，函数内惰性 import——无循环风险）。
from src.tui.app.toolcard import _tool_icon_runs, tool_card_lines
# core.style 为 Layer 0 底层（无 app 依赖），模块级 import 无循环风险；
# 用于模块级样式常量（_S_TOOL_OUT 工具输出前缀色）。
from src.tui.core.style import Style

_logger = logging.getLogger("src.tui.app.model")

#: 工具输出行前缀样式（append_tool_output 每段输出共用；模块级单例复用——
#   修复前每段新建 ``Style(fg=242)``，长工具输出数万行时无谓分配）
_S_TOOL_OUT = Style(fg=242)


def _bash_line_text(line) -> str:
    """工具输出行内容文本（剥离渲染前缀 ``  ``，还原原始输出）。

    ``append_tool_output`` 每输出行经 ``AnsiLine.of("  ", _S_TOOL_OUT)``
    前置 2 空格渲染前缀——``line.plain`` 含该前缀（如 ``"  line0"``）。
    文件级截断落盘（``_bash_dropped_text``/tail 拼接）需**原始输出**文本，
    统一剥离前缀还原（前缀恒为 2 空格，确定性）。防御：非 ``  `` 开头
    原样返回。
    """
    plain = getattr(line, "plain", "")
    if plain.startswith("  "):
        return plain[2:]
    return plain


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
                if existing.extra.get("_group"):
                    # ── 群组成员 open（Phase B）：不覆盖群组标题/状态 ──
                    # 群组卡已由 open_tool_group 建立；成员 ToolStartedEvent
                    # 的单个 ToolOpenCmd 被 Dispatcher 抑制，此处仅防御
                    # （append_tool_output 兜底等路径）。更新该成员 detail
                    # + active_tool 后复用群组块返回。
                    for member in existing.extra.get("_members", []):
                        if member["tool_id"] == tool_id:
                            member["detail"] = detail
                            break
                    self.active_tool = {
                        "name": display, "detail": detail, "status": "running",
                        "tool_name": tool_name or "",
                    }
                    return existing
                # 复用已开放 box：更新工具名/状态/detail + 标题行
                # （live 渲染下一帧生效；开放 box 未提交，更新安全）
                existing.extra["tool_name"] = tool_name
                existing.extra["tool_status"] = "running"
                existing.extra["tool_detail"] = detail
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

    def open_tool_group(self, tool_name: str, members) -> ChatBlock:
        """打开一个分组工具卡（同一 assistant 消息内 ≥2 个连续同类分组工具）。

        Phase B（对齐 Claude Code grouped tool use）：多个同类分组工具
        （``_GROUPABLE_TOOLS``）合并为**一张卡**——群组 = 单个
        ``ChatBlock(kind="tool")``，extra 记录：
          ``_group=True`` / ``_group_tool=<raw name>`` / ``_collapsed``（Phase C，
          可折叠工具为 True）/ ``_members=[{tool_id, detail, status}, ...]``。
        ``block.lines`` 仅 1 行标题占位（保持模型不变式
        ``block.lines[0].plain.startswith("  · ")``）。成员 id 注册进现有
        ``tool_boxes`` dict → 群组块，使 ``append_tool_output``（输出丢弃）/
        ``close_tool_box``（成员关闭路由）对群组路由不变。

        Args:
            tool_name: 工具名（raw，如 ``read_file``）。
            members: 成员可迭代——dict（``{"tool_id"/"id", "detail"}``）或
                ``(tool_id, detail)`` 二元组。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        from src.tools.registry import get_tool_display_name
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        block = self.append_block("tool")
        block.extra["_group"] = True
        block.extra["_group_tool"] = tool_name
        block.extra["_collapsed"] = tool_name in _COLLAPSIBLE_GROUP_TOOLS
        block.extra["tool_status"] = "running"
        block.extra["_tool_started_at"] = time.monotonic()
        normalized: list[dict] = []
        for member in members:
            if isinstance(member, dict):
                mid = member.get("tool_id") or member.get("id") or ""
                detail = member.get("detail", "") or ""
            elif isinstance(member, (tuple, list)) and len(member) >= 1:
                mid = member[0] or ""
                detail = member[1] if len(member) >= 2 else ""
            else:
                mid = str(member or "")
                detail = ""
            normalized.append({
                "tool_id": mid,
                "detail": _single_line_detail(detail),
                "status": "running",
            })
            if mid:
                self.tool_boxes[mid] = block
        block.extra["_members"] = normalized
        block.lines.append(AnsiLine.of(f"  \u00b7 {display}", Style(fg=23, bold=True)))
        self._tool_groups[id(block)] = block
        self.active_tool = {
            "name": display, "detail": "", "status": "running",
            "tool_name": tool_name or "",
        }
        return block

    def append_tool_output(self, tool_id: str, text: str) -> None:
        """追加工具输出行到对应分组（卡片主体行）。

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
        # ★ 空工具卡防御（模型层双保险）：tool_id 为 "assistant" 时说明该输出
        #   来自工具上下文之外的 print_to_terminal 回退（如后台任务完成提示），
        #   不归属任何工具 box——兜底创建空「工具」卡会永不闭合（● ⚙ 工具）。
        #   直接丢弃（上层 _on_tool_output 已过滤，此为防御冗余）。
        if tool_id == "assistant":
            _logger.debug("append_tool_output: 无归属输出（assistant），丢弃: %.80s", text)
            return
        block = self.tool_boxes.get(tool_id)
        if block is None:
            if not tool_id:
                _logger.debug(
                    "append_tool_output: 收到空 tool_id，输出丢弃: %.80s", text,
                )
                return
            block = self.open_tool_box(tool_id, "")
        # ── 群组成员输出丢弃（Phase B）：群组卡为**摘要卡**，不渲染成员
        # 全文输出（全文仍作为 tool result 进入对话，模型正确性不受影响；
        # 同时规避分组卡内无界正文的增量提交/修剪复杂度）。
        if block.extra.get("_group"):
            _logger.debug(
                "append_tool_output: 群组成员 %r 输出丢弃（摘要卡）: %.80s",
                tool_id, text,
            )
            return
        for seg in text.split("\n"):
            l = AnsiLine.of("  ", _S_TOOL_OUT)
            # ★ 工具输出可能含 Rich/pygments 高亮 ANSI 序列（read_file 等）。
            #   原样保留进 Run.text 会让宽度测量把转义码当可见字符（宽度膨胀→
            #   误触发 wrap），wrap_line 逐字符截断把转义序列拦腰截断（如残留
            #   ;49;00m）渲染错乱。经 ansi_to_line 解析为带样式 Run，宽度测量
            #   与 wrap 按样式安全处理。
            for r in ansi_to_line(seg).runs:
                l.append_run(r)
            block.lines.append(l)
        # bash/execute_command：输出超过阈值行数时只保留最后 N 行（tail 显示，
        # 对齐 Claude Code 收敛冗长 bash 输出；修剪后行数 ≤ N+1，不触发增量提交）
        if block.extra.get("tool_name") in ("bash", "execute_command"):
            self._trim_tool_output_tail(block, _BASH_OUTPUT_TAIL_LINES)
        # find/search/ls/read_file：输出超过阈值行数时只保留前 N 行（head 显示，
        # 对齐终端 head 语义——目录列表/文件预览等有序输出看开头即可，防卡片撑爆）
        if block.extra.get("tool_name") in _TOOL_HEAD_TOOLS:
            self._trim_tool_output_head(block, _TOOL_HEAD_LINES)
        # ★ 方向4：增量提交阈值——长工具输出不每帧全量重渲染（超过阈值即提交
        #   已闭合行到 committed_lines；开放块渲染只取未提交尾）。
        if len(block.lines) - block.committed_line_count >= _TOOL_INCREMENTAL_THRESHOLD:
            self.commit_open_block(block)

    def _drop_tool_body_cache(self, block, line) -> None:
        """从工具卡内容行缓存中移除行键（trim 删除行后同步清理，P1-1）。

        ``_tool_card_body_cache`` 为 dict（键=``(AnsiLine 行对象, width)``
        元组——toolcard.py ``tool_card_lines`` 写入，值=wrap 结果 runs）——
        trim 从 ``block.lines`` 删除行后若不同步 pop，被删行对象仍被 cache
        持有直到工具 box 关闭（长输出工具在 box 存活期内内存线性增长）。
        键按 ``k[0] is line`` 身份匹配（同一行对象可能以不同 width 多次入
        缓存，逐一删除）；cache 未建立（None）时零开销返回。
        """
        body_cache = getattr(block, "_tool_card_body_cache", None)
        if body_cache is not None:
            # ★ 修复（P1）：缓存键为 ``(ansi_line, width)`` 元组（toolcard.py
            #   ``tool_card_lines`` 写入）——修复前 ``body_cache.pop(line, None)``
            #   用单个 AnsiLine 作键永不命中，被 trim 删除的行对象仍被缓存持有
            #   （长输出工具内存线性增长）。遍历删除键首元素 is line 的条目。
            for k in [k for k in body_cache if k[0] is line]:
                body_cache.pop(k, None)

    def _trim_tool_output_tail(self, block, keep: int) -> None:
        """工具块输出修剪为最后 keep 行（保留标题行 block.lines[0]）。

        bash 尾显示：输出超过 keep 行时删除前置输出行（下标 1..N-keep），
        累计省略数记入 ``block.extra["_bash_omitted_lines"]``（卡片渲染时
        前置「… 前 N 行省略」提示）；同步 ``committed_line_count``（已提交行
        被删则回退计数，防越界/重复提交）。修剪后行数 ≤ 1+keep，远低于增量
        提交阈值 → 无增量提交。

        方向3（trim 与增量提交协同）：已增量提交的行（``committed_line_count>0``）
        不可删除——删除会令 committed_lines 前缀与块行映射错位（回退计数但
        前缀残留 → 渲染重复/错位）。已提交场景跳过 trim（保留全部，正确性
        优先）。正常路径 trim 在增量提交前已压缩到 ≤keep 行，本分支仅覆盖
        「空名 box 输出 >64 行触发增量提交后工具名补全」的罕见时序。
        """
        lines = block.lines
        if len(lines) <= 1 + keep:
            return
        if block.committed_line_count > 0:
            _logger.debug(
                "bash tail trim 跳过（块已增量提交 %d 行，无法安全删除）",
                block.committed_line_count,
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
        # ★ 文件级截断（对齐 CC）：被删行**纯文本**累积进
        #   ``extra["_bash_dropped_text"]``——close_tool_box 落盘时与保留尾
        #   拼接还原完整输出（dropped 恒为前缀、tail 为后缀，按行顺序连续）。
        #   仅发生 trim 时累积（无省略则无落盘需求）；空行 plain="" 以
        #   "\n" 参与 join 还原原始换行结构。
        dropped_text = "\n".join(_bash_line_text(ln) for ln in removed)
        if dropped_text:
            prev = block.extra.get("_bash_dropped_text", "")
            block.extra["_bash_dropped_text"] = (
                prev + ("\n" if prev else "") + dropped_text
            )

    def _trim_tool_output_head(self, block, keep: int) -> None:
        """工具块输出修剪为前 keep 行（保留标题行 block.lines[0]）。

        find/search/ls/read_file 头显示：输出超过 keep 行时删除后置输出行
        （下标 1+keep..末尾），累计省略数记入 ``block.extra["_head_omitted_lines"]``
        （卡片渲染时在主体行后置「… 后 N 行省略」提示）；同步
        ``committed_line_count``（已提交行被删则回退计数，防越界/重复提交）。
        修剪后行数 ≤ 1+keep，远低于增量提交阈值 → 无增量提交。

        方向3（trim 与增量提交协同）：已增量提交的行（``committed_line_count>0``）
        不可删除——删除会令 committed_lines 前缀与块行映射错位。已提交场景
        跳过 trim（保留全部，正确性优先；与 ``_trim_tool_output_tail`` 一致）。
        """
        lines = block.lines
        if block.committed_line_count > 0:
            _logger.debug(
                "head trim 跳过（块已增量提交 %d 行，无法安全删除）",
                block.committed_line_count,
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
        # ── 群组成员 close（Phase B）：路由到 _close_group_member ──
        # 成员 id 注册在 tool_boxes → 群组块；关闭只更新成员状态并聚合群组
        # 状态，不 pop 群组块本身（全部成员关闭后才 _finalize_group）。
        block = self.tool_boxes.get(tool_id)
        if block is not None and block.extra.get("_group"):
            self._close_group_member(block, tool_id, success)
            return
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
        status = "\u2714" if success else "\u2716"
        # ★ BEAUTY-35（状态行元信息）：计算工具耗时（open 记录的开始时间戳 →
        # 关闭时差）。Claude Code 极简样式后渲染层不显示独立状态行（耗时字段
        # 保留供内部/测试消费）。无开始时间戳（旧块/外部构造）时跳过（防御）。
        started = block.extra.get("_tool_started_at")
        if started is not None:
            block.extra["_tool_duration"] = max(0.0, time.monotonic() - started)
        # 记录状态行下标（卡片渲染跳过该主体行——状态由标题行状态图标表达；
        # 模型层不变式 block.lines[-1].plain.strip()=="✔" 保留）
        block.extra["_status_line_index"] = len(block.lines)
        block.lines.append(AnsiLine.of(f"  {status}", Style(fg=41 if success else 196)))
        block.extra["tool_status"] = "done" if success else "fail"
        # ★ 文件级截断（对齐 CC，2026-08-08）：bash 输出超过 tail 行数被修剪
        #   （``_bash_omitted_lines>0``）时，关闭一次性将完整输出（被删前缀 +
        #   保留尾）落盘，卡片渲染 ``Output truncated (XKB total). Full output
        #   saved to: <path>`` 替代省略行。写失败回退省略提示（异常安全、
        #   幂等——`_bash_truncation_file` 仅成功路径写入）。在渲染线程执行，
        #   单次小文件写（< MB 级），可接受。
        if block.extra.get("_bash_omitted_lines", 0) > 0 and not block.extra.get("_bash_truncation_file"):
            dropped = block.extra.get("_bash_dropped_text", "")
            tail = "\n".join(_bash_line_text(ln) for ln in block.lines[1:-1])
            if dropped and tail:
                full = dropped + "\n" + tail
            else:
                full = dropped + tail
            try:
                path = _write_bash_output_file(full)
                block.extra["_bash_truncation_file"] = path
                block.extra["_bash_truncation_bytes"] = len(full.encode("utf-8"))
            except Exception:
                # 落盘失败：保留省略提示（渲染回退「… 前 N 行省略」）
                _logger.debug(
                    "bash 大输出落盘失败，回退省略提示（%d 行）",
                    block.extra.get("_bash_omitted_lines", 0),
                    exc_info=True,
                )
        # Claude TUI parity 步骤 2.2：关闭后无进行中工具（ToolStatusHeader 隔离
        # 测试仍消费 active_tool；app 组件树已移除该组件）
        self.active_tool = None

        # ★ 1.6 修复 + BUG-30（review 方向）修复：长工具输出（>
        #   _TOOL_INCREMENTAL_THRESHOLD 触发增量提交后标题行已在 committed_lines）
        #   关闭时更新 committed_lines 中标题行状态图标。
        #   **BUG-30（渲染陈旧）**：修复前原地修改 ``top_line.runs``（保留 Line
        #   对象引用）——committed-chat 前缀缓存（``chat_view._paint`` 键
        #   ``(id(lines), n, box.y)``）与 diff 身份短路（``p is f`` → 相等跳过）
        #   都按「Line 对象身份 = 内容不变」优化：内存中 Line 虽改为 ✔，但
        #   prev 帧与 new 帧引用同一 Line 对象 → 渲染器认为无差异 → **终端标题行
        #   恒显示 ●，与关闭状态矛盾**（必现，长工具输出触发增量提交后关闭必现）。
        #   修复：**新建 Line 对象替换**（不复用旧对象）+ ``_replace_committed_line``
        #   令 committed_lines 列表身份变化（浅拷贝）→ 前缀缓存键中 ``id(lines)``
        #   失效 → 下一帧重建前缀 → diff 对新 Line 对象做 runs 值比较 → 标题行
        #   被重写。短工具（未增量提交，offset 不存在）关闭时经 commit_block
        #   提交的标题行已带 done/fail 图标，无需更新。
        #   卡片结构：``_first_committed_offset`` 指向卡片**首行（标题行）**，
        #   状态图标为标题行 runs[0]（无边框——2026-08-06 去边框后不再有
        #   ``┌─ `` 边框前缀）。
        offset = block.extra.get("_first_committed_offset")
        if offset is not None and 0 <= offset < len(self.committed_lines):
            icon = _tool_icon_runs(block)
            if icon:
                top_line = self.committed_lines[offset]
                runs = list(top_line.runs)
                # 标题行结构：[0]=状态图标, [1:]=标题内容
                idx = 0
                if not (runs and runs[0].text.strip() in ("\u25cf", "\u2714", "\u2716")):
                    # 防御：超窄宽度下标题被截断时按图标字符扫描定位
                    for i, r in enumerate(runs):
                        if r.text and r.text.strip() in ("\u25cf", "\u2714", "\u2716"):
                            idx = i
                            break
                # ★ BUG-30：新建 Line 对象（不复用旧对象）+ 列表身份变化
                from src.tui.ink import Line
                self._replace_committed_line(offset, Line(runs[:idx] + icon + runs[idx + 1:]))

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

    def _close_group_member(self, block, tool_id: str, success: bool) -> None:
        """关闭群组单个成员：置成员状态 + 聚合群组状态 + 原位翻转标题图标。

        群组块由成员 id 路由（``close_tool_box`` 已判 ``_group``）；本方法：
          - 置成员 status（done/fail）；
          - 聚合 ``_tool_status``（任一 running→running / 否则任一 fail→fail
            / 否则 done）——群组卡标题状态图标数据源（``_tool_icon_runs``
            经 ``_group_status`` 读取）；
          - 已增量提交（``_first_committed_offset`` 存在）时经
            ``_replace_committed_line`` 原位翻转标题图标（BUG-30 安全——
            新建 Line 对象 + 列表身份变化，防前缀缓存命中返回旧标题）；
          - 全部成员关闭（无 running）→ ``_finalize_group`` 冻结提交。

        不追加 ``  ✔`` 状态数据行（群组卡摘要，无正文/状态行）。
        """
        members = block.extra.get("_members", [])
        for member in members:
            if member["tool_id"] == tool_id:
                member["status"] = "done" if success else "fail"
                break
        # 该成员工具 box 不再 active（群组块保留到全部成员关闭）
        self.tool_boxes.pop(tool_id, None)
        running = any(m["status"] == "running" for m in members)
        failed = any(m["status"] == "fail" for m in members)
        block.extra["_tool_status"] = (
            "running" if running else ("fail" if failed else "done")
        )
        # BUG-30 安全：已提交标题行原位翻转图标（群组块仅在极端时序下
        # 提前提交，防御路径）
        offset = block.extra.get("_first_committed_offset")
        if offset is not None and 0 <= offset < len(self.committed_lines):
            from src.tui.ink import Line
            head = tool_card_lines(block, getattr(self, "width", 0), 0, None)
            if head:
                self._replace_committed_line(offset, Line(head[0]))
        if not running:
            self._finalize_group(block)

    def _finalize_group(self, block) -> None:
        """最终化群组卡：弹出全部成员、置 closed、冻结并提交。

        全部成员关闭（或 ``flush_tool_groups`` 回合末兜底）后调用一次：
          - 弹出 tool_boxes 中剩余成员（防御：成员未逐一 close 的异常时序）；
          - 从 ``_tool_groups`` 移除（防重复 flush）；
          - 置 closed + 冻结未提交部分 + ``commit_block``（与 close_tool_box
            一致）；冻结前 ``tool_status`` 同步为聚合 ``_tool_status``
            （标题图标数据源兼容）；
          - 释放工具卡缓存（冻结后渲染走 ``_cached_ink_lines``）。
        幂等：已 closed 群组再次调用直接返回（防 flush + 成员 close 竞态）。
        """
        if block.closed:
            return
        for member in block.extra.get("_members", []):
            mid = member["tool_id"]
            if mid in self.tool_boxes:
                self.tool_boxes.pop(mid, None)
        for gid in list(self._tool_groups.keys()):
            if self._tool_groups[gid] is block:
                self._tool_groups.pop(gid, None)
                break
        block.extra["tool_status"] = block.extra.get("_tool_status", "done")
        block.closed = True
        block._cached_ink_lines = self._block_to_ink_lines(
            block, block.committed_line_count,
        )
        block._open_styled_cache = None
        self.commit_block(len(self.blocks) - 1)
        block._tool_card_body_cache = None
        block._tool_card_frame_cache = None
        block._tool_card_body_lines_cache = None
        # 群组全部结束 → 无进行中工具（与单卡 close_tool_box 置 None 一致）
        self.active_tool = None

    def flush_tool_groups(self) -> None:
        """回合末强制结束未完成的群组（对齐 ``close_empty_tool_boxes``）。

        某群组成员因异常/取消未逐一 close 时，回合末将未关闭成员置 done 后
        最终化群组——避免群组卡永久 ● running 悬挂。由 ``_on_round_end``
        （``_session_setup``）调用。
        """
        for gid in list(self._tool_groups.keys()):
            block = self._tool_groups[gid]
            if block.closed:
                continue
            for member in block.extra.get("_members", []):
                if member["status"] == "running":
                    member["status"] = "done"
            block.extra["_tool_status"] = "done"
            self._finalize_group(block)

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
            # ── 群组块跳过（Phase B）：群组卡仅 1 行标题占位，若按「空 box」
            # 判定会被误判为空卡自动闭合——成员状态由 close_tool_box/
            # flush_tool_groups 管理，此处不自动闭合。
            if block.extra.get("_group"):
                continue
            # 空 box：只有标题行（lines[0]），无主体输出内容
            if len(block.lines) <= 1:
                self.close_tool_box(tool_id, True)
                closed += 1
        return closed

    def _next_tool_id(self) -> str:
        self._tool_id_seq += 1
        return f"tool-{self._tool_id_seq}"

    def _unlink_truncation_files(self) -> None:
        """清理全部工具块已落盘的 bash 截断文件（reset_display/回放重渲染复用）。

        ``close_tool_box`` 落盘的 ``deepseek-bash-*`` 临时文件在会话内保留
        （用户可查看 ``Full output saved to`` 路径）；清屏/回放重渲染
        （Ctrl+L、/editmsg 等经 reset_display）时旧块被丢弃，临时文件不再
        被引用——统一 unlink 防 /tmp 累积。幂等：未落盘块（无
        ``_bash_truncation_file``）零开销。
        """
        for block in self.blocks:
            path = block.extra.get("_bash_truncation_file")
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                block.extra["_bash_truncation_file"] = ""


__all__ = ["_ToolOutputMixin"]
