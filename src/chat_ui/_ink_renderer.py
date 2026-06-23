"""InkRenderer — React Ink 声明式渲染器。

Layer 2 — 依赖 _vdom / _box / _components / _ink_state / OutputAdapter。
将 InkState 转换为组件树 → VDOM diff → 终端 patches 写入。

核心流水线：
  Event → State → Component Tree → VDOM Diff → Terminal Patches

设计原则：
  - 纯 Python，使用 ANSI 转义序列控制光标
  - threading.Lock 保护 render_frame/apply_frame 互斥
  - 与 InkState 配合：render_frame 读取 state，apply_frame 写终端
  - 底部栏、SubAgent 帧、parse_info 不经过本渲染器
  - 终端宽度每帧从 shutil.get_terminal_size 刷新
"""

from __future__ import annotations

import logging
import shutil
import threading
from typing import TYPE_CHECKING

from ._vdom import CVNode, CVPatch, CVPatchType, build_vnode, diff
from ._ink_state import InkState
from ._box import Box, FlexDirection, Static, Text
from ._components import (
    ErrorBlock,
    NotificationBlock,
    ToolOutputBlock,
    ToolSummaryBlock,
    UserMsgBlock,
    WriteLineBlock,
)
if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# InkRenderer — React Ink 声明式渲染器
# ═══════════════════════════════════════════════════════════

class InkRenderer:
    """React Ink 声明式渲染器。

    将 InkState 渲染为组件树 → VDOM diff → 终端增量更新。
    持有上一帧的 VDOM 树和行缓冲区，实现帧间增量渲染。

    Attributes:
        _adapter: 终端输出适配器（OutputAdapter 实例）
        _old_root: 上一帧的 VDOM 树根（None 表示首次渲染）
        _terminal_width: 当前终端宽度（字符数）
        _lock: 渲染锁（保护 render_frame/apply_frame 互斥）
        _line_count: 上一帧写入的终端行数
        _last_lines: 上一帧的行内容列表（用于增量对比）
    """

    def __init__(self, adapter: OutputAdapter) -> None:
        """初始化 InkRenderer。

        Args:
            adapter: 终端输出适配器，统一终端 I/O 接口。
        """
        self._adapter = adapter
        self._old_root: CVNode | None = None
        self._terminal_width: int = 80
        self._lock = threading.Lock()
        self._line_count: int = 0
        self._last_lines: list[str] = []
        # _pending_lines 在 render_frame 中设置，apply_frame 中消费
        self._pending_lines: list[str] = []

    # ── 公共接口 ──────────────────────────────────────

    def render_frame(self, state: InkState) -> list[CVPatch]:
        """将 InkState 渲染为一组 VDOM patches。

        执行步骤：
        1. 从 InkState 构建 TuiComponent 组件树
        2. 调用 build_vnode(root) → new_root (CVNode)
        3. diff(old_root, new_root) → patches
        4. 更新 old_root = new_root
        5. 刷新终端宽度
        6. 预渲染组件树为行列表（供 apply_frame 消费）

        Args:
            state: InkState 实例，包含当前渲染状态的所有字段。

        Returns:
            VDOM 补丁列表（INSERT/DELETE/UPDATE/REORDER）。
            首次渲染时 old_root 为 None，diff 返回全量 INSERT。
        """
        with self._lock:
            # 1. 构建组件树
            root = self._build_component_tree(state)

            # 2. 构建 VDOM
            new_root = build_vnode(root)

            # 3. Diff
            patches = diff(self._old_root, new_root)

            # 4. 更新 old_root
            self._old_root = new_root

            # 5. 刷新终端宽度
            self._terminal_width = self._get_terminal_width()

            # 6. 预渲染组件为行列表
            self._pending_lines = self._render_component_to_lines(root)

            return patches

    def apply_frame(self, patches: list[CVPatch]) -> None:
        """将 VDOM patches 应用到终端。

        策略：
        - 首次渲染（_line_count == 0）：从当前光标位置逐行写入全部内容
        - 增量渲染：移动光标到内容区起始位置，清屏至末尾，重写全部行
        - 内容未变化时跳过（与 _last_lines 逐行对比）

        使用 ANSI 转义序列：
        - \\033[{n}A：上移 n 行
        - \\033[J：从光标清至屏幕末尾
        - \\033[K：清除当前行

        Args:
            patches: render_frame() 返回的补丁列表。
        """
        with self._lock:
            new_lines = self._pending_lines
            if not new_lines:
                return

            # 内容未变化 → 跳过
            if new_lines == self._last_lines:
                return

            if self._line_count == 0:
                # 首次渲染：从头开始写入
                self._write_lines_at_cursor(new_lines)
            else:
                # 增量渲染：定位到内容区起始位置，清屏重写
                self._apply_incremental(new_lines)

            self._line_count = len(new_lines)
            self._last_lines = new_lines

    def reset(self) -> None:
        """重置渲染器状态。

        将 old_root 置为 None，清空行缓冲区。
        下次 render_frame 将触发全量首次渲染。
        """
        with self._lock:
            self._old_root = None
            self._line_count = 0
            self._last_lines = []
            self._pending_lines = []

    # ── 组件树构建 ────────────────────────────────────

    def _build_component_tree(self, state: InkState) -> Box:
        """从 InkState 构建 TuiComponent 组件树。

        将 InkState 中各字段映射为对应的声明式子组件，
        以 Box(flex_direction=COLUMN) 作为根容器。

        组件映射：
        - errors → ErrorBlock（最多 3 条）
        - user_message → UserMsgBlock
        - reasoning_text → Box[Static(思考头), Text(推理内容)]
        - content_text → Box[Static(分隔线, 有推理时), Text(回答内容)]
        - tool_outputs → ToolOutputBlock 列表
        - tool_summary → ToolSummaryBlock
        - notifications → NotificationBlock（最多 3 条）
        - write_lines → WriteLineBlock（最多 5 条）

        Args:
            state: InkState 实例。

        Returns:
            Box 根容器组件。
        """
        children: list = []

        # ── 错误（最多显示最近 3 条） ──
        if state.errors:
            for err in state.errors[-3:]:
                children.append(ErrorBlock(message=err))

        # ── 用户消息 ──
        if state.user_message:
            children.append(UserMsgBlock(text=state.user_message))

        # ── 推理内容（思考块） ──
        if state.reasoning_text:
            thinking_children: list = [
                Static(children=[Text(content="  ─ 思考 ─")]),
                Text(content=state.reasoning_text),
            ]
            children.append(Box(
                flex_direction=FlexDirection.COLUMN,
                children=thinking_children,
            ))

        # ── 回答内容 ──
        if state.content_text:
            answer_children: list = []
            if state.reasoning_text:
                # 有推理内容时添加分隔线
                answer_children.append(Static(children=[
                    Text(content="  " + "─" * 25),
                ]))
            answer_children.append(Text(content=state.content_text))
            children.append(Box(
                flex_direction=FlexDirection.COLUMN,
                children=answer_children,
            ))

        # ── 工具输出（tool_outputs 用 insert(0,...) 所以最新在最前，reversed 恢复正序） ──
        for tool_text in reversed(state.tool_outputs):
            children.append(ToolOutputBlock(text=tool_text))

        # ── 工具汇总 ──
        if state.tool_summary_successful or state.tool_summary_failed:
            children.append(ToolSummaryBlock(
                successful=state.tool_summary_successful,
                failed=state.tool_summary_failed,
            ))

        # ── 通知（最多 3 条） ──
        if state.notifications:
            for notification in state.notifications[-3:]:
                children.append(NotificationBlock(text=notification))

        # ── 单行输出 ──
        for line in state.write_lines[-5:]:
            children.append(WriteLineBlock(text=line))

        return Box(flex_direction=FlexDirection.COLUMN, children=children)

    # ── 组件渲染辅助 ──────────────────────────────────

    def _render_component_to_lines(self, component) -> list[str]:
        """将 TuiComponent 组件树渲染为文本行列表。

        调用 component.render() 获取输出（str 或 Rich Text），
        转纯文本后按换行符拆分为行列表。

        支持：
        - str 输出：直接按 \\n 拆分
        - Rich Text 输出：取 .plain 属性后拆分
        - None 输出：返回空列表
        - 空字符串：返回空列表

        Args:
            component: TuiComponent 根组件。

        Returns:
            文本行列表（不含换行符）。
        """
        try:
            output = component.render()
        except Exception:
            _logger.debug(
                "_render_component_to_lines: render() 异常", exc_info=True,
            )
            return []

        if output is None:
            return []

        # Rich Text → 纯文本
        if hasattr(output, 'plain'):
            text = output.plain
        else:
            text = str(output)

        if not text:
            return []

        return text.split('\n')

    def _write_lines_at_cursor(self, lines: list[str]) -> None:
        """在光标当前位置逐行写入文本。

        每行后追加换行符，通过 OutputAdapter.write_raw() 输出。

        Args:
            lines: 文本行列表。
        """
        for line in lines:
            self._adapter.write_raw(line + "\n")

    def _clear_lines_at_cursor(self, count: int) -> None:
        """清除光标位置起 N 行。

        使用 ANSI \\033[K 逐行清除。清除 count-1 行后在行间移动，
        第 count 行清除后不换行（光标留在该行开头）。

        Args:
            count: 要清除的行数。
        """
        for i in range(count):
            self._adapter.write_raw("\033[K")
            if i < count - 1:
                self._adapter.write_raw("\n")

    def _apply_incremental(self, new_lines: list[str]) -> None:
        """增量渲染：定位到内容区起始，清屏后重写全部行。

        步骤：
        1. 上移光标到内容区第一行（\\033[{n}A）
        2. 从光标清至屏幕末尾（\\033[J）
        3. 逐行写入新内容

        此策略比逐行 diff 更简单可靠：
        - 避免逐行对比的边界条件（增减行、滚动等）
        - 终端清屏 + 重写开销极小（通常 < 1ms）
        - 底部栏由 _phase_redraw_bottom 随后重绘，不受 \\033[J 影响

        Args:
            new_lines: 新的文本行列表。
        """
        # 1. 上移光标到内容区起始位置
        self._adapter.write_raw(f"\033[{self._line_count}A")

        # 2. 从光标清至屏幕末尾（清除旧内容 + 旧底部栏残留）
        self._adapter.write_raw("\033[J")

        # 3. 写入全部新行
        self._write_lines_at_cursor(new_lines)

    @staticmethod
    def _get_terminal_width() -> int:
        """获取当前终端宽度。

        Returns:
            终端列数，获取失败时返回默认值 80。
        """
        try:
            return shutil.get_terminal_size().columns
        except Exception:
            return 80
