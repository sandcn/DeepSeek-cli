"""AppModel — 聊天 UI 应用模型。

单一真源：RenderCmd → AppModel 状态变更（apply.py），
组件树读取 AppModel 渲染。替代 ChatRenderState + _BottomBar 状态域。

块列表：每个聊天块 = ChatBlock（kind + AnsiLine 行列表）。
推理/内容通道：AnsiStreamRenderer 流式累积，PhaseDone 关闭后固化到块。
阶段状态机：推理 INACTIVE/ACTIVE/CLOSED + content 关闭标志（多轮重开）。

卡片结构：``committed_lines`` 为「卡片文档」——每块提交为
``[角色头] + [正文] + [空行]``（无头 kind 为 ``[正文] + [空行]``，如
user/write_line/splash/parse_info；工具块为渲染期卡片
``[标题行] + [主体行]``——Claude Code 极简样式（2026-08-06 用户需求），
标题行替代 ``▎⚡ 工具 X`` 角色头，状态由标题行状态图标（●/✔/✖）表达，
无独立状态行）。角色头经 ``_role_header_line`` 截断保证单行 ≤width；空行
经 ``_append_card_trailer`` 在块关闭提交时追加恰好一次。正文-only 冻结
缓存 ``_cached_ink_lines`` 不含卡片头/空行（``len == len(block.lines)``
不变式；工具卡跳过状态行数据行，``len == len(block.lines) - 1``）。

终端 resize：``reflow_committed(width)`` 在宽度变化后按新宽度重建全部
已提交行（committed_lines 提交时按旧宽度 wrap，宽度变化后需重排）——
产出新列表对象使前缀缓存自动失效；live/开放块经 ink 布局按当前宽度换行
自动适配。

模块边界（2026-08-05 架构优化）：
  - ``_state_types.py``：纯状态 dataclass（ChatBlock/CompletionState/...）
  - ``_model_helpers.py``：模块级渲染辅助（角色头/用户前缀/修剪阈值常量）
  - ``_tool_output_mixin.py``：工具 box 生命周期（open/append/trim/close/
    已提交行替换）——AppModel 组合该 mixin
  - 本模块聚焦 AppModel 行为（块管理/通道），状态与辅助 re-export
    保持旧导入路径兼容（``from src.tui.app.model import ChatBlock`` 等）。
"""

from __future__ import annotations

import logging
from typing import Any

# ★ 状态类型与渲染辅助已拆分至同级模块（模块边界优化，2026-08-05）；
#   re-export 保持旧导入路径兼容（测试/外部调用面）。
from src.tui.app._state_types import (
    ChatBlock,
    CompletionState,
    UserSelectState,
    StatusState,
    HistorySearchState,
    ReasoningState,
)
from src.tui.app._model_helpers import (
    _TOOL_INCREMENTAL_THRESHOLD,
    _BASH_OUTPUT_TAIL_LINES,
    _TOOL_HEAD_TOOLS,
    _TOOL_HEAD_LINES,
    _single_line_detail,
    _user_marker_styled_lines,
    _role_header_runs,
    _role_header_line,
)
# ★ 工具输出行为（模块边界优化，2026-08-05）：工具 box 生命周期
#   （open/append/trim/close/已提交行替换）迁至 _tool_output_mixin.py；
#   AppModel 组合该 mixin，对外 API 不变。
from src.tui.app._tool_output_mixin import _ToolOutputMixin
# ★ ToolCard React Ink 组件化：工具卡行生成收敛到 app/toolcard.py
#   （模块级零依赖，函数内惰性 import——无循环风险）。
from src.tui.app.toolcard import tool_card_lines

_logger = logging.getLogger(__name__)


class AppModel(_ToolOutputMixin):
    """聊天 UI 应用模型。"""

    def __init__(self) -> None:
        # ── 聊天块 ──
        self.blocks: list[ChatBlock] = []
        # ★ 增量渲染缓存：已关闭（提交）块的渲染行。
        #   静态历史只渲染一次并缓存，每帧不重建 → 大历史下渲染 O(live+新增)。
        self.committed_lines: list = []
        self.committed_count: int = 0
        # 推理/内容通道（AnsiStreamRenderer 惰性创建）
        self.reasoning_renderer: Any = None
        self.content_renderer: Any = None
        self.reasoning_state: ReasoningState = ReasoningState.INACTIVE
        self.content_closed: bool = False
        self.reasoning_block_index: int = -1
        self.content_block_index: int = -1
        # 终端宽度（session 每帧更新；渲染器 TOC 边框用）
        self.width: int = 80
        # 工具调用组
        self.in_tool_group: bool = False
        self.tool_block_index: int = -1
        # 每工具 box 跟踪（tool_id → 开放 box）
        self.tool_boxes: dict = {}
        self._tool_id_seq: int = 0
        # 状态栏
        self.status: StatusState = StatusState()
        # 输入
        self.input_text: str = ""
        self.input_cursor: int = 0
        # 补全
        self.completion: CompletionState = CompletionState()
        # 用户选择弹窗（React Ink 化：user_select 工具 → UserSelectPopup 组件）
        self.user_select: UserSelectState = UserSelectState()
        # 实时解析进度行（同位置刷新；ParseInfoDone 后提交并清空）
        self.parse_line: Any = None
        # subagent 面板行（控制器推送）
        self.subagent_lines: list = []
        # 反向历史搜索状态（None=未激活；input_area 渲染覆盖行）
        self.history_search: "HistorySearchState | None" = None
        # ── 模态全屏视图（2026-08-17 通用机制） ──
        # fullscreen: 当前模态全屏视图 id（""=正常界面；"trace"=轨迹视图；
        #   未来可扩展其他全屏视图）。fullscreen 非空时 App 按视图注册表
        #   （``app.FULLSCREEN_VIEWS``）**整屏渲染**对应组件，组件经
        #   ``use_fullscreen(True)`` 声明模态——独占键盘输入（未消费按键不
        #   落入输入缓冲，杜绝看不见的输入）。新增全屏视图两步：注册表加
        #   条目 + 设置 model.fullscreen（整屏渲染/输入接管/光标隐藏全部
        #   自动生效）。
        # trace_open: fullscreen=="trace" 的兼容别名（property，见类底部）。
        self.fullscreen: str = ""
        # ── 模态底部视图（2026-08-17 通用机制，user_select 独立成底部视图） ──
        # bottom_view: 当前模态底部视图 id（""=正常底部区；"user_select"=
        #   用户选择弹窗独立界面）。bottom_view 非空时 App 按底部视图注册表
        #   （``app.BOTTOM_VIEWS``）**只渲染对应视图**（状态栏/输入区不显示
        #   ——「弹窗打开时底部框不显示，弹窗在原来底部框位置独立显示」），
        #   组件经 ``use_modal(True)`` 声明模态——独占键盘输入（未消费按键
        #   不落入输入缓冲）。新增底部视图两步：注册表加条目 + 设置
        #   model.bottom_view（底部区渲染/输入接管/光标隐藏全部自动生效）。
        # 与 fullscreen 的关系：互斥共存设计——fullscreen 整屏渲染优先
        # （App 先判 fullscreen 再判 bottom_view）；底部视图激活期间 fullscreen
        # 恒为空（工具/协议打开 user_select 时不会同时打开全屏视图）。
        self.bottom_view: str = ""
        # trace_selected: 选中记录索引（records 下标，0-based）；-1 表示
        #   「跟随尾部」（默认——打开时定位最新记录，流式追加自动跟进；用户
        #   导航后写回具体索引退出尾部跟随）。
        # message_source: agent 消息列表访问器 ``() -> list[dict]``（真实会话
        #   消息：system/user/assistant(+tool_calls)/tool 返回）——轨迹视图
        #   **以 agent 消息列表为数据源**（对齐 DSH：从 Session 消息组装业务
        #   记录，而非 TUI 渲染过的聊天块）；None=未注入（回退块构建路径，
        #   测试/无装配场景）。
        self.trace_selected: int = -1
        self.message_source: object | None = None
        # trace_subagent_label: 轨迹视图当前显示的 subagent 轨迹 label
        #   （None=显示主 agent 轨迹；非 None=主轨迹中按 Enter 选中 subagent
        #   记录后进入其轨迹——嵌套 TraceView，内容与 mainagent 轨迹同构：
        #   system/user/assistant/tool 消息 → 台账 + 检查器；Esc/Ctrl+H 返回
        #   主轨迹，再次 Esc/Ctrl+H 关闭整个轨迹视图）。
        # 顶部工具调用状态（Claude TUI parity 步骤 2.2：active_tool 为模型
        # 数据——原 ToolStatusHeader 渲染消费，组件已移除（工具状态改由工具
        # 卡片顶边框 ● 展示，双份冗余）；字段保留供测试/未来消费，None=无
        # 进行中工具）
        self.active_tool: dict | None = None

    # ── 块管理 ──────────────────────────────────────

    def append_block(self, kind: str, lines=None) -> ChatBlock:
        """追加聊天块（不自动提交，供流式累积）。"""
        block = ChatBlock(kind, list(lines) if lines else [])
        self.blocks.append(block)
        return block

    def append_committed(self, kind: str, lines) -> ChatBlock:
        """追加一个立即提交（关闭）的块：渲染缓存 + 块列表。"""
        block = self.append_block(kind, lines)
        block.closed = True
        self.commit_block(len(self.blocks) - 1)
        return block

    def commit_open_block(self, block: ChatBlock) -> None:
        """增量提交开放块的已闭合行（流式内容随段落闭合提交）。

        开放块（content/reasoning/tool）的闭段行立即进入缓存，块内只留
        未闭合尾（当前段落）→ 每帧渲染成本 O(live+当前段落)，不随响应增长。

        BUG-4（方向4 修复）：**仅允许「连续提交窗口」内的开放块增量提交**——
        若该块前面尚有未关闭/未提交的块（``committed_count != 块索引``），
        增量提交会打乱 committed_lines 的**块顺序**（content 流式期间被提前
        写入 committed_lines，其后 reasoning 关闭提交时被插到 content 之后，
        形成 content 前半 + reasoning + content 后半的内容交错）。修复后：
        块索引 == committed_count 才增量提交；否则等待前面块关闭后随
        ``commit_block`` 一并提交（行保留在块内，live 渲染正常显示）。
        """
        if block.committed_line_count >= len(block.lines):
            return
        # ★ BUG-4：块索引 == committed_count 才允许增量提交（连续窗口检查）。
        #   ★ BUG-11 修复：使用**身份查找**（``b is block``）——ChatBlock 为
        #   dataclass，默认生成的 ``__eq__`` 是**值比较**：字段完全相同的块
        #   （如两个空 content 块：连续两轮 reopen_content 均无输出）会互相
        #   相等，``list.index(block)`` 恒返回第一个匹配位置 → 第二个块增量
        #   提交时拿到错误索引，连续窗口判断失效（可能错误提交/错误阻断）。
        #   遍历 + is 比较不受值相等影响，O(n) 成本仅发生在增量提交时（低频）。
        idx = None
        for i, b in enumerate(self.blocks):
            if b is block:
                idx = i
                break
        if idx is None:
            return  # 块已不在列表（防御）
        if idx != self.committed_count:
            # 前面尚有未关闭/未提交块：不增量提交（等待 commit_block 随连续
            # 已关闭窗口一并提交；开放期间行保留在块内 live 渲染）。
            return
        # ★ 1.6：块首次提交（committed_line_count==0）记录卡片首行（角色头）
        #   在 committed_lines 中的偏移（committed_lines 只增不删，偏移稳定），
        #   供 close_tool_box 关闭时更新其后一行（正文标题）状态图标。
        #   open 块不加卡片尾空行（_append_card_trailer 仅关闭提交时追加）。
        if block.committed_line_count == 0:
            block.extra.setdefault("_first_committed_offset", len(self.committed_lines))
        self.committed_lines.extend(
            self._card_lines(block, block.committed_line_count)
        )
        block.committed_line_count = len(block.lines)

    def commit_block(self, index: int) -> None:
        """提交 blocks[committed_count..index] 到增量渲染缓存。

        仅提交**连续的已关闭**块——前面若有未关闭块（如流式内容块）则停止，
        避免跳过开放块导致其后续行丢失。已增量提交的行（committed_line_count）
        不再重复渲染。卡片结构：本次有新增内容提交时经 ``_card_lines`` 发射
        （首次提交带头行）并追加卡片尾空行 ``_append_card_trailer``。

        ★ 方向5（append_committed 冻结）：全块提交完成（closed 且
        committed_line_count == len(lines)）且尚未冻结时建立 ``_cached_ink_lines``
        ——append_committed 创建的立即关闭块自动冻结（免每帧重渲染）；
        被开放块夹住的已关闭块提交后同样冻结。仅 ``is None`` 时冻结：
        close_reasoning/close_content/close_tool_box 已在关闭时冻结（内容
        可能不同——如 close_tool_box 冻结未提交尾），不覆盖。

        方向3（流式块关闭后缺尾空行修复）：open 块（content/reasoning）在
        流式期间经 ``commit_open_block`` 全量提交（``committed_line_count ==
        len(lines)`` 恒成立），关闭时若 ``renderer.close()`` 无残差（``take_lines``
        返回空）→ 本循环走「无新增内容」分支。修复前该分支不补 trailer → 同一
        会话内部分回答后有空白分隔、部分没有（取决于流式块边界）。现记录
        ``_trailer_appended`` 标志，无新增内容但已全量提交过且未加过 trailer 的
        关闭块补一次尾空行（``_append_card_trailer`` 内部对末行空 / 空块幂等）。
        """
        while self.committed_count <= index and self.committed_count < len(self.blocks):
            block = self.blocks[self.committed_count]
            if not block.closed:
                break
            if block.committed_line_count < len(block.lines):
                # ★ 1.6：块首次提交（committed_line_count==0）记录卡片首行偏移
                #   （与 commit_open_block 一致——增量提交路径也须记录）。
                if block.committed_line_count == 0:
                    block.extra.setdefault("_first_committed_offset", len(self.committed_lines))
                self.committed_lines.extend(
                    self._card_lines(block, block.committed_line_count)
                )
                block.committed_line_count = len(block.lines)
                # ★ 卡片尾空行：块关闭提交（本次有新增内容）时追加一个空行
                #   分隔卡片——幂等重入（committed_line_count >= len(lines)
                #   提前返回）时不再追加，空行恰好一次。
                self._append_card_trailer(block)
                block.extra["_trailer_appended"] = True
            elif block.committed_line_count > 0 and not block.extra.get("_trailer_appended"):
                # 无新增内容但块已在 open 期间全量提交过（流式块关闭无残差）——
                # 关闭时补齐卡片尾空行（方向3 修复；_append_card_trailer 幂等）。
                self._append_card_trailer(block)
                block.extra["_trailer_appended"] = True
            if block._cached_ink_lines is None and block.kind != "reasoning":
                block._cached_ink_lines = self._block_to_ink_lines(block, 0)
                # ★ 方向1（内存回收）：冻结后开放 styled 缓存不再被
                #   ``_block_styled_lines`` 使用（改走冻结缓存）——释放引用防
                #   大会话累积（dict 持有全部已转换行引用）。
                block._open_styled_cache = None
                block._tool_card_body_cache = None  # PERF-6：冻结后释放
                block._tool_card_frame_cache = None  # PERF-6：冻结后释放
                block._tool_card_body_lines_cache = None  # PERF-6b：冻结后释放
            elif block.kind == "reasoning":
                # ★ BUG-62：reasoning 不冻结（冻结缓存无消费方）——仅释放
                #   open 缓存防大会话内存累积（_block_styled_lines 对
                #   reasoning 保持即时 fg=242 路径）。
                block._open_styled_cache = None
            self.committed_count += 1

    def _block_to_ink_lines(self, block, start: int = 0, stop=None):
        """将块内 AnsiLine（从 start 起到 stop）转为 ink Line（块级样式叠加）。

        ★ 方向1 P0-1（超宽行 wrap）：committed 发射前按 ``self.width`` wrap——
        任一 AnsiLine 显示宽度超过终端宽度时，经 ``renderer.ansi.helpers.wrap_line``
        拆为多行（保持 run 样式），避免超宽行破坏行级 diff 模型（committed_lines
        每行 ink Line 宽度须 <= width）。仅超宽行走 wrap（普通行零额外成本）；
        ``self.width <= 0`` 时跳过 wrap 保持原样（防御）。

        分支（先于通用 wrap）：
          - tool：卡片化行（标题行 + 内容行 + 状态行，**无边框**——2026-08-06
            去边框）——渲染期变换，不改动 ``block.lines`` 原文；builder 统一
            管理宽度约束，不走通用 wrap。
          - user：每行 ``> `` 标记（顶格列 0，对齐 Claude Code）——即使不超宽
            也要重前缀。

        stop: 可选结束下标（不含）——``reflow_committed`` 重建块**已提交部分**
        （``lines[:committed_line_count]``）时限定范围，避免未提交尾混入。
        """
        from src.tui.ink import Line, StyledRun
        from src.renderer.ansi.style import Style as _AnsiStyle
        from src.renderer.ansi.helpers import wrap_line
        slice_lines = block.lines[start:stop]
        if not slice_lines:
            return []
        # 工具卡片（渲染期边框行）：builder 统一管理宽度约束；不走通用 wrap
        if block.kind == "tool":
            return [
                Line(runs) for runs in tool_card_lines(
                    block, getattr(self, "width", 0), start, stop,
                )
            ]
        # 用户消息（每行 `> ` 标记）：即使不超宽也要重前缀 → 先于通用 wrap
        if block.kind == "user":
            return _user_marker_styled_lines(
                block, start, stop, getattr(self, "width", 0),
            )
        reasoning_style = (
            _AnsiStyle(dim=True) if block.kind == "reasoning" else None
        )
        width = getattr(self, "width", 0)
        out: list = []
        for ansi_line in slice_lines:
            # ★ 方向1 P0-1：超宽行按 width wrap（wrap 与测量使用一致的宽度工具；
            #   仅超宽行走 wrap，普通行零额外成本；width<=0 跳过 wrap 防御）
            src_lines = (
                wrap_line(ansi_line, width)
                if (width > 0 and ansi_line.width > width)
                else [ansi_line]
            )
            for wrapped in src_lines:
                runs = []
                for r in wrapped.runs:
                    if not r.text:
                        continue
                    st = r.style
                    if reasoning_style is not None:
                        st = reasoning_style if st is None else st.merge(reasoning_style)
                    runs.append(StyledRun(r.text, st))
                out.append(Line(runs))
        return out

    def _card_lines(self, block, start: int = 0):
        """块卡片行：正文 + （首次提交时）角色头。

        committed_lines 为「卡片文档」（角色头 + 正文 + 空行；tool 无角色头
        ——由卡片顶边框替代；content/reasoning 等有角色头——content
        ``▍💬 回答``、reasoning ``▍💭 思考``）。
        角色头仅在 start==0（块首次提交，committed_line_count==0）时前置一次；
        增量提交（start>0）不再重复。冻结行 ``_cached_ink_lines`` 保持正文-only
        （不改，测试锁定 ``len(_cached_ink_lines) == len(block.lines)``）。
        """
        out = self._block_to_ink_lines(block, start)
        if start == 0:
            header = _role_header_line(block, self, getattr(self, "width", 0))
            if header is not None:
                out = [header] + out
        return out

    def _append_card_trailer(self, block) -> None:
        """块完全提交后追加卡片尾空行（卡片与下一条目分隔）。

        仅当正文末行非空时追加（正文已以空行结尾则跳过，防双空行）。
        committed_lines 原地增长（引用不变），前缀缓存兼容。
        """
        if not block.lines:
            return
        if getattr(block.lines[-1], "plain", "") == "":
            return
        from src.tui.ink import Line
        self.committed_lines.append(Line())

    def _card_lines_committed(self, block, width: int) -> list:
        """重建块**已提交部分**（``block.lines[:committed_line_count]``）的卡片行。

        供 ``reflow_committed``（终端宽度变化重排）使用：按新宽度重新 wrap
        已提交行 + 角色头（首行）+ 关闭块（已完全提交）尾空行——与提交路径
        （``_card_lines`` + ``_append_card_trailer``）产出一致，保证头/空行
        重建后恰好一次。``stop`` 限定只取已提交 AnsiLine（open 块未提交尾
        不混入）。
        """
        count = block.committed_line_count
        if count <= 0:
            return []
        out = self._block_to_ink_lines(block, 0, stop=count)
        header = _role_header_line(block, self, width)
        if header is not None:
            out = [header] + out
        if block.closed and count >= len(block.lines) and block.lines:
            if getattr(block.lines[-1], "plain", "") != "":
                from src.tui.ink import Line
                out.append(Line())
        return out

    def reflow_committed(self, width: int) -> None:
        """终端宽度变化后按新宽度重建 committed_lines（重排已提交行）。

        committed_lines 在提交时按旧宽度 wrap；宽度变化后旧行可能超宽（破坏
        「行级 diff 宽度不变量」：committed 每行 ink Line 宽度须 <= width）或
        未利用新宽度。重建全部已提交行：逐块按已提交行数重新 wrap + 卡片头 +
        关闭块尾空行，产出**新列表对象** → ChatView use_memo / committed-chat
        前缀缓存（键含 ``id(lines)``）自动失效，无需额外通知。open 块未提交
        尾留在块内（live 渲染按新宽度 wrap）；关闭块未提交尾
        （``_cached_ink_lines``）同步按新宽度重冻结。

        幂等：``width <= 0`` 或与当前宽度相同 → 直接返回（零成本）。
        """
        if width <= 0 or width == self.width:
            return
        self.width = width
        committed: list = []
        for block in self.blocks:
            count = block.committed_line_count
            if count <= 0:
                continue
            block.extra["_first_committed_offset"] = len(committed)
            committed.extend(self._card_lines_committed(block, width))
            # 方向3：reflow 重建含尾空行（_card_lines_committed 对 closed 块
            # 无条件补 trailer）→ 重置 trailer 标志，防后续 commit_block 重复
            # 追加（已重建的 committed_lines 含 trailer）。
            if block.closed:
                block.extra["_trailer_appended"] = True
            # 关闭块未提交尾（增量提交后仍留尾 / 被夹住）：按新宽度重冻结
            if block._cached_ink_lines is not None and count < len(block.lines):
                block._cached_ink_lines = self._block_to_ink_lines(block, count)
        self.committed_lines = committed

    # ── 推理/内容通道 ───────────────────────────────

    def ensure_reasoning(self):
        """确保推理通道开启（返回渲染器，None 表示已关闭）。"""
        if self.reasoning_state == ReasoningState.CLOSED:
            return None
        if self.reasoning_renderer is None:
            from src.renderer.ansi import AnsiStreamRenderer
            self.reasoning_renderer = AnsiStreamRenderer(width=self.width)
            self.reasoning_state = ReasoningState.ACTIVE
            self.reasoning_block_index = len(self.blocks)
            self.append_block("reasoning")
        return self.reasoning_renderer

    def flush_reasoning_live(self) -> None:
        """兜底固化开放推理通道已渲染行（思考内容先于进度行上屏）。

        需求背景（2026-08-16 用户需求）：工具参数接收进度行（如
        ``~ Edit 2608t 8.44s``）与思考内容（reasoning 块）在渲染线程中
        同批入队处理时，确保思考内容先固化到块——避免进度行先于思考内容
        上屏。仅当推理通道处于 ACTIVE（开放且有渲染器）且块未关闭时生效；
        无思考内容（渲染器不存在/已关闭）零成本跳过。

        与 ``_do_reasoning`` 每次 flush 语义一致（AnsiStreamRenderer 实时
        渲染、``take_lines`` 消费缓冲）——此处为 ``ParseInfoCmd``（接收
        参数进度行）路径的防御性保障：即使 ReasoningCmd 与 ParseInfoCmd
        同批到达，思考内容也已固化显示。
        """
        rr = self.reasoning_renderer
        if rr is None or self.reasoning_state != ReasoningState.ACTIVE:
            return
        lines = rr.take_lines()
        if not lines:
            return
        idx = self.reasoning_block_index
        if 0 <= idx < len(self.blocks):
            block = self.blocks[idx]
            block.lines.extend(lines)
            self.commit_open_block(block)

    def close_reasoning(self) -> None:
        """关闭推理通道：固化渲染器输出（无分隔线——对齐 Claude Code 消息间仅空行）。"""
        if self.reasoning_state == ReasoningState.CLOSED:
            return
        rr = self.reasoning_renderer
        if rr is not None:
            rr.close()
            lines = rr.take_lines()
            if 0 <= self.reasoning_block_index < len(self.blocks):
                block = self.blocks[self.reasoning_block_index]
                block.lines.extend(lines)
            self.reasoning_renderer = None
        self.reasoning_state = ReasoningState.CLOSED
        # 提交到增量渲染缓存（方向D 步骤15：关闭块冻结行缓存）
        if 0 <= self.reasoning_block_index < len(self.blocks):
            block = self.blocks[self.reasoning_block_index]
            block.closed = True
            # ★ BUG-21（review 方向）：仅冻结**未提交尾**
            #   （``committed_line_count`` 起）——修复前全量冻结
            #   ``_block_to_ink_lines(block, 0)``：已增量提交过的行（已在
            #   committed_lines）被重复存为 ink Line → 大响应关闭后内存约
            #   翻倍且不回收。与 ``close_tool_box`` 的未提交尾冻结一致；
            #   ``_block_styled_lines`` 冻结缓存分支已按 ``cache[0:]``
            #   （冻结缓存即未提交部分）返回。
            # ★ BUG-62（review 方向）：reasoning 块**不创建冻结缓存**——
            #   ``_block_styled_lines`` 显式排除 reasoning（冻结 dim 样式与
            #   即时渲染 fg=242 语义不同）→ 创建即死内存；仅释放 open 缓存。
            if block.kind != "reasoning":
                block._cached_ink_lines = self._block_to_ink_lines(
                    block, block.committed_line_count,
                )
            block._open_styled_cache = None  # 冻结后开放缓存不再需要
            # ★ BUG-77（commit 范围）：提交到**块列表末尾**（而非仅本块索引）
            #   ——reasoning 关闭时其后可能存在**已关闭但未提交**的块（如
            #   上一轮遗留的工具卡 / content 流式期间先关闭的工具卡）。修复前
            #   ``commit_block(self.reasoning_block_index)`` 只提交到本块，
            #   其后的已关闭块（tool）被遗留为「未提交」状态：工具卡永远走
            #   live 渲染（ToolCard 每帧重建，无冻结缓存消费）、后续 open 块
            #   增量提交被 BUG-4 连续窗口守卫阻断。``commit_block(len-1)``
            #   从 committed_count 起连续提交**全部已关闭块**，遇未关闭块
            #   （如仍流式的 content）自然停止——语义安全。
            self.commit_block(len(self.blocks) - 1)

    def reopen_reasoning(self) -> None:
        """重新打开推理通道（CLOSED → INACTIVE）。"""
        if self.reasoning_state != ReasoningState.CLOSED:
            return
        self.reasoning_renderer = None
        self.reasoning_state = ReasoningState.INACTIVE

    def ensure_content(self):
        """确保内容通道开启（None 表示已关闭）。"""
        if self.content_closed:
            return None
        if self.content_renderer is None:
            from src.renderer.ansi import AnsiStreamRenderer
            self.content_renderer = AnsiStreamRenderer(width=self.width)
            self.content_block_index = len(self.blocks)
            self.append_block("content")
        return self.content_renderer

    def close_content(self) -> None:
        """关闭内容通道：固化渲染器输出。"""
        cr = self.content_renderer
        if cr is not None:
            cr.close()
            lines = cr.take_lines()
            if 0 <= self.content_block_index < len(self.blocks):
                self.blocks[self.content_block_index].lines.extend(lines)
            self.content_renderer = None
        self.content_closed = True
        # 提交到增量渲染缓存（方向D 步骤15：关闭块冻结行缓存）
        if 0 <= self.content_block_index < len(self.blocks):
            block = self.blocks[self.content_block_index]
            block.closed = True
            # ★ BUG-21（review 方向）：仅冻结未提交尾（同 close_reasoning）——
            #   已增量提交的行不重复存 ink Line（大响应内存不翻倍）。
            block._cached_ink_lines = self._block_to_ink_lines(
                block, block.committed_line_count,
            )
            block._open_styled_cache = None  # 冻结后开放缓存不再需要
            # ★ BUG-77（commit 范围，同 close_reasoning）：提交到**块列表末尾**
            #   ——content 关闭时其后可能存在**已关闭但未提交**的块（content
            #   流式期间打开并关闭的工具卡——``close_tool_box`` 的
            #   ``commit_block(len-1)`` 被未关闭的 content 挡住）。修复前
            #   ``commit_block(self.content_block_index)`` 只提交 content 自身，
            #   其后的工具卡遗留为「未提交」：永远走 ToolCard live 渲染
            #   （每帧重建，无冻结缓存）、后续 open 块增量提交被 BUG-4 连续
            #   窗口守卫阻断（大回答流式期间无法增量提交 → 全量 live 渲染）。
            #   ``commit_block(len-1)`` 连续提交全部已关闭块，遇未关闭块停止。
            self.commit_block(len(self.blocks) - 1)

    def reopen_content(self) -> None:
        """重新打开内容通道（多轮会话新一轮内容前调用）。"""
        self.content_closed = False
        # ★ 修复（P3）：清理 content_renderer 残留——修复前仅置
        #   content_closed=False，若 close_content 异常路径残留 renderer
        #   （close/take_lines 抛异常中断），ensure_content 因 renderer
        #   非 None 复用旧渲染器（状态错乱/旧流内容混入新一轮）。重开时
        #   统一置 None，ensure_content 按需新建（与 close_content 语义
        #   一致，不破坏正常流程）。
        self.content_renderer = None

    def flush_open_channels(self) -> None:
        """停止时固化所有开放通道。"""
        try:
            self.close_reasoning()
        except Exception:
            # 非关键降级：停止时通道固化失败不阻断（记录日志）
            _logger.debug("flush_open_channels 关闭推理通道异常", exc_info=True)
        try:
            self.close_content()
        except Exception:
            _logger.debug("flush_open_channels 关闭内容通道异常", exc_info=True)

    def reset_display(self) -> None:
        """清空显示状态（Claude TUI parity 步骤 2.2，供 Ctrl+L 清屏复用）。

        清空聊天块/增量缓存/推理内容通道/subagent 行/进行中工具/解析行/
        反向历史搜索，保留 ``status/input_text/input_cursor/completion``
        （用户输入与状态不丢）。调用方须保证非流式（status.status_active=False）
        时调用，避免丢未提交块。
        """
        self.blocks = []
        self.committed_lines = []
        self.committed_count = 0
        self.reasoning_renderer = None
        self.content_renderer = None
        self.reasoning_state = ReasoningState.INACTIVE
        self.content_closed = False
        self.reasoning_block_index = -1
        self.content_block_index = -1
        self.in_tool_group = False
        self.tool_block_index = -1
        self.tool_boxes = {}
        self._tool_id_seq = 0
        self.parse_line = None
        self.subagent_lines = []
        self.active_tool = None
        # ★ P2-2：同步重置反向历史搜索状态——Ctrl+R 搜索激活期间执行
        #   Ctrl+L 清屏//editmsg 重渲染时输入区不再渲染 (reverse-i-search)
        #   覆盖行（修复前 reset_display 未重置 history_search，残留搜索态
        #   导致输入区仍渲染搜索覆盖行）。
        self.history_search = None
        # ★ 轨迹视图状态复位（2026-08-19）：清屏后轨迹记录清空，选中回到
        #   尾部跟随（-1）——避免残留索引指向已清空的记录列表。
        self.trace_selected = -1
        # ★ 2026-08-16：清屏同时退出 subagent 轨迹（嵌套视图）——残留 label
        #   指向的 subagent 记录可能已随面板清空（无记录可显示）。
        self.trace_subagent_label = None
        # ★ 2026-08-17（review 方向 P2）：清屏同时退出模态全屏视图——残留
        #   fullscreen 会让 App 整屏渲染全屏视图组件（如 TraceView），而
        #   blocks 已清空 → 残留渲染空数据全屏界面。与 trace_subagent_label
        #   同语义（全屏态不跨清屏保留）。
        self.fullscreen = ""
        # ★ 2026-08-17（模态底部视图通用机制）：清屏同时退出模态底部视图
        #   （bottom_view 置空）——残留 bottom_view 会让 App 底部区只渲染
        #   底部视图组件（如 UserSelectPopup）而 blocks 已清空；且 user_select
        #   工具轮询 done 期间若清屏，弹窗消失但工具仍在等待（超时兜底）。
        #   与 fullscreen 同语义（底部视图态不跨清屏保留）。
        self.bottom_view = ""

    # ── trace_open 兼容别名（2026-08-17 通用化：模态全屏视图） ──
    # 轨迹视图打开 = model.fullscreen == "trace"。property 保持旧字段读写
    # 语义（旧代码/测试 ``model.trace_open = True/False`` 与
    # ``getattr(model, "trace_open", False)`` 均兼容），读写统一映射到
    # fullscreen——轨迹开关不再独立于通用全屏视图状态。

    @property
    def trace_open(self) -> bool:
        """轨迹视图是否打开（``fullscreen == "trace"`` 的兼容别名）。"""
        return self.fullscreen == "trace"

    @trace_open.setter
    def trace_open(self, value: bool) -> None:
        """设置轨迹视图开关（映射到 ``fullscreen``）。"""
        self.fullscreen = "trace" if value else ""


__all__ = [
    "AppModel",
    "ChatBlock",
    "CompletionState",
    "StatusState",
    "HistorySearchState",
    "UserSelectState",
    "ReasoningState",
]
