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
            # 空 box：只有标题行（lines[0]），无主体输出内容
            if len(block.lines) <= 1:
                self.close_tool_box(tool_id, True)
                closed += 1
        return closed

    def _next_tool_id(self) -> str:
        self._tool_id_seq += 1
        return f"tool-{self._tool_id_seq}"


__all__ = ["_ToolOutputMixin"]
