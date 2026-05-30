"""_BottomBar — 流式输出期间固定底部输入栏（动态拆行）。

在终端底部使用 ANSI DECSTBM 滚动区域创建固定区域：
上方内容区正常滚动，底部固定显示（分隔线 + 状态行 + 输入区）。

线程安全（分两级）：
  - 内容变更全量重绘（文本/状态/尺寸变化）→ output_lock 串行化
  - 纯光标移动轻量路径 → 无锁直写 ANSI 序列（GIL + 幂等性保证安全）
"""

from __future__ import annotations

import sys
import time
from typing import Optional

from wcwidth import wcswidth

from ._lock import _try_acquire_output_lock


def _truncate_by_width(s: str, max_width: int) -> str:
    """按终端列宽截断字符串（中文占 2 列）。"""
    w = 0
    for i, ch in enumerate(s):
        cw = wcswidth(ch) if wcswidth(ch) >= 0 else 1
        if w + cw > max_width:
            return s[:i]
        w += cw
    return s


_TAB_WIDTH = 4               # 制表符宽度（列数）


def _expand_tabs(text: str, start_col: int = 0, tab_width: int | None = None) -> str:
    """将制表符按制表位展开为空格。

    每个 \\t 跳到下一个制表位列（tab_width 的整数倍），
    用空格填充至该列。

    Args:
        text: 含制表符的文本。
        start_col: 起始列（0-based）。
        tab_width: 制表宽度，默认 _TAB_WIDTH。

    Returns:
        展开后的纯空格文本。
    """
    if tab_width is None:
        tab_width = _TAB_WIDTH
    if '\t' not in text:
        return text
    result = []
    col = start_col
    for ch in text:
        if ch == '\n':
            result.append(ch)
            col = 0  # 换行后列计数器归零
        elif ch == '\t':
            spaces = tab_width - (col % tab_width)
            result.append(' ' * spaces)
            col += spaces
        else:
            cw = wcswidth(ch)
            result.append(ch)
            col += cw if cw >= 0 else 1
    return ''.join(result)


def _tab_pos_to_expanded(text: str, pos: int,
                         tab_width: int | None = None) -> int:
    """将含制表符文本中的字符位置映射到展开后的位置。

    Args:
        text: 含制表符的原始文本。
        pos: 原始文本中的字符索引（<0 返回 -1）。
        tab_width: 制表宽度，默认 _TAB_WIDTH。

    Returns:
        展开后文本中对应的字符索引。
    """
    if pos < 0:
        return -1
    if tab_width is None:
        tab_width = _TAB_WIDTH
    expanded_pos = 0
    col = 0
    for i, ch in enumerate(text):
        if i >= pos:
            break
        if ch == '\t':
            spaces = tab_width - (col % tab_width)
            expanded_pos += spaces
            col += spaces
        elif ch == '\n':
            expanded_pos += 1
            col = 0  # 换行后列计数器归零
        else:
            cw = wcswidth(ch)
            expanded_pos += 1
            col += cw if cw >= 0 else 1
    return expanded_pos


def _wrap_by_width(s: str, max_width: int) -> list[str]:
    """按终端列宽拆分文本为多行，每行不超过 max_width 列。

    优先按 \\n 拆分（强制换行），再对每段按列宽拆行。
    调用方应先通过 _expand_tabs 展开制表符。
    """
    if max_width <= 0 or not s:
        return [s] if s else [""]
    lines: list[str] = []
    # 先按 \n 拆分为强制换行段
    for segment in s.split('\n'):
        remaining = segment
        while remaining:
            w = 0
            idx = 0
            for i, ch in enumerate(remaining):
                cw = wcswidth(ch) if wcswidth(ch) >= 0 else 1
                if w + cw > max_width:
                    break
                w += cw
                idx = i + 1
            if idx == 0:  # 单个字符超过宽度，至少取一个字符
                idx = 1
            lines.append(remaining[:idx])
            remaining = remaining[idx:]
        if not segment:
            # 空段表示连续 \n 或尾部 \n → 插入一个空行
            lines.append("")
    return lines if lines else [""]


def _compute_cursor_visual_pos(
    text: str, cursor_pos: int, max_width: int,
) -> tuple[int, int]:
    """计算光标在带 \\n 的文本中的视觉位置（行号, 列号）。

    将文本按 \\n 拆分为逻辑行，每行分别制表符展开和按列宽拆行，
    定位光标所在逻辑行，累计前面逻辑行的视觉行数得到总行号偏移。

    Args:
        text: 原始输入文本（含 \\n）。
        cursor_pos: 光标在原始文本中的字符偏移（-1=末尾）。
        max_width: 每行最大列宽。

    Returns:
        (visual_line_idx, visual_col) —— 均为 0-based。
    """
    if not text:
        return (0, 0)

    # 确定绝对光标位置
    if cursor_pos < 0:
        abs_cursor = len(text)
    else:
        abs_cursor = cursor_pos

    # 拆分为逻辑行
    lines = text.split('\n')
    cum = 0  # 累计原始字符索引
    for logical_idx, logical_line in enumerate(lines):
        line_len = len(logical_line)
        if abs_cursor <= cum + line_len:
            # 光标在此逻辑行中（或在行末的 \n 上）
            pos_in_line = abs_cursor - cum

            # 展开并拆行
            expanded = _expand_tabs(logical_line)
            wrapped = _wrap_by_width(expanded, max_width)

            # 计算此逻辑行内光标所处视觉行和列
            expanded_in_line = _tab_pos_to_expanded(logical_line, pos_in_line)
            if expanded_in_line < 0:
                # 末尾
                last_seg = wrapped[-1] if wrapped else ""
                col_in_line = wcswidth(last_seg)
                visual_line_in_logical = len(wrapped) - 1 if wrapped else 0
            else:
                cum2 = 0
                visual_line_in_logical = 0
                for i, seg in enumerate(wrapped):
                    if expanded_in_line <= cum2 + len(seg):
                        visual_line_in_logical = i
                        prefix = seg[:expanded_in_line - cum2]
                        col_in_line = wcswidth(prefix)
                        break
                    cum2 += len(seg)
                else:
                    visual_line_in_logical = len(wrapped) - 1 if wrapped else 0
                    col_in_line = wcswidth(wrapped[-1]) if wrapped else 0

            # 累计前面逻辑行的视觉行数
            total_before = 0
            for prev_line in lines[:logical_idx]:
                prev_expanded = _expand_tabs(prev_line)
                total_before += len(_wrap_by_width(prev_expanded, max_width))

            return (total_before + visual_line_in_logical, col_in_line)

        # 此逻辑行已消耗：字符数 + \n 的 1 个字符
        cum += line_len + 1

    # 超出范围 → 末尾
    # 最后一个逻辑行末尾
    last_line = lines[-1] if lines else ""
    expanded = _expand_tabs(last_line)
    wrapped = _wrap_by_width(expanded, max_width)
    last_seg = wrapped[-1] if wrapped else ""
    col = wcswidth(last_seg)
    total_before = 0
    for prev_line in lines[:-1]:
        prev_expanded = _expand_tabs(prev_line)
        total_before += len(_wrap_by_width(prev_expanded, max_width))
    visual_row = total_before + (len(wrapped) - 1 if wrapped else 0)
    return (visual_row, col)


# ── 模块级 get_token_speed_snapshot 缓存（避免内部方法每次 import） ──
_TOKEN_SPEED_SNAPSHOT: Optional[callable] = None


def _get_snapshot():
    """获取 get_token_speed_snapshot 函数引用（惰性加载，异常静默）。"""
    global _TOKEN_SPEED_SNAPSHOT
    if _TOKEN_SPEED_SNAPSHOT is None:
        try:
            from ..api.stats import get_token_speed_snapshot
            _TOKEN_SPEED_SNAPSHOT = get_token_speed_snapshot
        except ImportError:
            _TOKEN_SPEED_SNAPSHOT = False  # 标记不可用
    return _TOKEN_SPEED_SNAPSHOT if callable(_TOKEN_SPEED_SNAPSHOT) else None


# ── 底部栏配置 ──────────────────────────────────────────
_BOTTOM_LINES = 3           # 底部固定行数
_BOTTOM_MIN_HEIGHT = 10     # 终端太小时跳过底部栏
_BOTTOM_REFRESH_MS = 0.05   # 底部栏刷新节流（50ms）

# ── ANSI 颜色常量 ──────────────────────────────────────
_COLOR_PROMPT = "\033[38;5;39m"       # 青色 ◆ 提示符
_COLOR_PLACEHOLDER = "\033[38;5;244m" # 灰色占位提示
_COLOR_DIVIDER = "\033[38;5;236m"     # 暗灰分隔线
_COLOR_RESET = "\033[0m"
_COLOR_TOOL = "\033[38;5;208m"        # 橙色工具计数
_COLOR_TIME = "\033[38;5;81m"         # 亮蓝耗时
_COLOR_TOK = "\033[38;5;154m"         # 亮绿 token
_COLOR_SPEED = "\033[38;5;141m"       # 紫色 tok/s
# ── 进度条 & 补全弹窗颜色 ──────────────────────────────
_COLOR_PROGRESS_BG = "\033[38;5;238m"   # 进度条背景（深灰）
_COLOR_PROGRESS_FG = "\033[38;5;39m"    # 进度条前景（青色）
_COLOR_PROGRESS_DONE = "\033[38;5;40m"  # 进度条完成（绿色）
_COLOR_COMP_BORDER = "\033[38;5;240m"   # 补全弹窗边框（暗灰）
_COLOR_COMP_SELECTED_BG = "\033[48;5;236m"  # 选中项背景
_COLOR_COMP_DIM = "\033[38;5;245m"      # 补全辅助信息
_PLACEHOLDER_TEXT = "输入消息...  /help 查看命令"


def _visual_len(s: str) -> int:
    """计算不含 ANSI 转义序列的视觉宽度。

    识别所有 CSI 序列（\\033[...终止字母），正确跳过；
    同时也处理 OSC/APC 等其他 ANSI 序列类型。
    已知限制：不处理多码点组合字符（grapheme cluster）。
    """
    w = 0
    i = 0
    while i < len(s):
        if s[i] == '\033':
            j = i + 1
            if j < len(s) and s[j] == '[':
                # CSI 序列: \033[...终止字母(A-Za-z)
                j += 1
                while j < len(s) and s[j] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                    j += 1
                i = j + 1 if j < len(s) else len(s)
            elif j < len(s) and s[j] in ']PX^_':
                # OSC/APC/DCS/PM/SOS: \033]...\a 或 \033]...\033\\
                j += 1
                while j < len(s):
                    if s[j] == '\033':
                        if j + 1 < len(s) and s[j + 1] == '\\':
                            i = j + 2
                            break
                    elif s[j] == '\a':
                        i = j + 1
                        break
                    j += 1
                else:
                    i = len(s)
            else:
                # 非 CSI 控制序列（如 \033[无参数]），跳过
                i = j + 1
        else:
            cw = wcswidth(s[i])
            w += cw if cw >= 0 else 1
            i += 1
    return w


class _BottomBar:
    """终端底部固定输入栏，流式输出期间始终可见。

    使用 ANSI DECSTBM 滚动区域：上方内容区（1 至 H-底部行数）正常滚动，
    底部行（分隔线 + 状态行 + 动态输入区）位于滚动区域之外，
    通过手动定位绘制保持固定。

    视觉风格：
      - 分隔线：暗灰色 `─` 做内容区与输入区边界
      - 状态行：彩色分段（橙色工具计数 · 亮蓝耗时 · 亮绿令牌 · 紫色速率）
      - 输入区：青色 `◆` 提示符，空输入时显示灰色占位提示
                多行续行以青色 `│` 缩进，维持视觉连贯性

    线程安全（分两级）：
      - 内容变更全量重绘（文本/状态/尺寸变化）→ output_lock 串行化
      - 纯光标移动轻量路径 → 无锁直写 ANSI 序列（GIL + 幂等性保证安全）
    """

    _BOTTOM_LINES = _BOTTOM_LINES
    _MIN_HEIGHT = _BOTTOM_MIN_HEIGHT

    # ── 补全弹窗配置 ──────────────────────────────────────
    _COMPLETION_MAX_ITEMS = 10      # 单屏最多显示多少条

    def __init__(self):
        self._active = False
        self._last_text = ""
        self._last_status = ""
        self._last_refresh = 0.0
        self._setup_height = 0
        self._status_active = False  # 事件驱动：流式期间激活，结束后冻结
        self._tool_count = 0         # 本轮工具调用次数（仅主 Agent）
        self._tool_fail_count = 0    # 本轮失败工具数
        self._last_bottom_lines = _BOTTOM_LINES  # 上次绘制的底部总行数
        # ★ 光标位置（与 _last_text 在 output_lock 下原子更新）
        self._input_cursor_pos: int = -1  # echo 回调的 cursor_pos，-1=末尾
        self._last_cursor_pos: int = -1   # 上次光标位置，用于检测光标移动
        # ★ 展开/拆行缓存：轻量路径复用，避免每次光标移动都重算 _expand_tabs + _wrap_by_width
        self._cached_wrapped_for: str = ""   # 缓存对应的原始文本（缓存键，在 _draw_input_lines_locked 中更新）
        self._cached_wrapped_lines: list[str] | None = None  # 缓存拆行结果
        self._cached_input_rows: int = 1      # 缓存输入区视觉行数（不含分隔线/状态行）
        # ★ 上次渲染到终端的输入文本（显式标记，仅在 _draw_input_lines_locked 中更新，
        #    供 force_redraw 快速路径使用，避免 _cached_wrapped_for 承担双重语义）
        self._last_rendered_text: str = ""
        # ── 当前模型名字（供状态行显示） ──
        self._model_name = ""
        # ── 补全弹窗状态 ──
        self._completion_visible = False
        self._completion_items: list[str] = []     # 显示文本
        self._completion_texts: list[str] = []      # 替换文本（可能与显示不同）
        self._completion_start_pos: int = 0         # 从光标前多少字符开始替换
        self._completion_orig_prefix: str = ""      # 原始前缀（用于重建替换）
        self._completion_idx = 0

    # ── 动态行数计算 ──────────────────────────────────────

    @property
    def _bottom_lines(self) -> int:
        """当前底部栏总行数（分隔线 + 状态行 + 输入行），根据输入内容动态计算。"""
        return 2 + self._compute_input_rows()

    def _compute_input_rows(self) -> int:
        """根据当前输入文本计算所需的输入行数（最少 1 行）。"""
        text = self._last_text or ""
        if not text:
            return 1
        max_input = max(1, self._term_width() - 4)
        expanded = _expand_tabs(text)
        wrapped = _wrap_by_width(expanded, max_input)
        return max(1, len(wrapped))

    # ── 终端尺寸查询 ──────────────────────────────────────

    @staticmethod
    def _term_height() -> int:
        try:
            import shutil
            return shutil.get_terminal_size().lines
        except Exception:
            return 24

    @staticmethod
    def _term_width() -> int:
        try:
            import shutil
            return shutil.get_terminal_size().columns
        except Exception:
            return 80

    # ── 生命周期 ──────────────────────────────────────────

    def enable_status(self) -> None:
        """激活状态行刷新（流式输出期间调用）。"""
        self._status_active = True
        self._last_status = ""  # 强制下次刷新

    def disable_status(self) -> None:
        """冻结状态行（流式结束后调用），定格最终数值。"""
        self._status_active = False

    @property
    def is_status_active(self) -> bool:
        """状态行是否处于活跃刷新中（流式输出期间）。

        供 ChatUIConsumer 等外部调用方读取状态行刷新开关，
        避免直接访问私有属性 _status_active。
        """
        return self._status_active

    def get_cursor_info(self) -> tuple[str, int, int, int]:
        """获取光标定位所需数据：文本、光标位置、终端高度、终端宽度。

        供 ChatUIConsumer._position_cursor 使用，避免直接访问私有属性。
        返回值: (last_text, cursor_pos, term_height, term_width)
        """
        return (
            self._last_text,
            self._input_cursor_pos,
            self._term_height(),
            self._term_width(),
        )

    def _cursor_visual_pos_from_cache(
        self, text: str, cursor_pos: int, max_width: int,
    ) -> tuple[int, int]:
        """从缓存的拆行结果计算光标视觉位置。

        复用 _cached_wrapped_lines（_draw_input_lines_locked 中更新），
        避免在轻量路径中重算 _wrap_by_width。当缓存失效（文本变化后
        尚未重绘）时回退到完整 _compute_cursor_visual_pos。

        Returns:
            (visual_line_idx, visual_col) —— 均为 0-based。
        """
        # 缓存无效时回退到完整计算
        if self._cached_wrapped_for != text or self._cached_wrapped_lines is None:
            return _compute_cursor_visual_pos(text, cursor_pos, max_width)

        abs_cursor = len(text) if cursor_pos < 0 else cursor_pos
        # 将光标位置从原始文本映射到展开后文本
        expanded_pos = _tab_pos_to_expanded(text, abs_cursor)
        if expanded_pos < 0:
            expanded_pos = len(self._cached_wrapped_for)  # 末尾
        # ★ 减去光标前的 \n 数量：_tab_pos_to_expanded 将每个 \n 计为 1 位置，
        #   但 _wrap_by_width 已通过 split('\n') 剥离了 \n，缓存中不含它们。
        newlines_before = text[:abs_cursor].count('\n')
        adjusted_pos = expanded_pos - newlines_before
        # 在缓存的拆行结果中查找 adjusted_pos 所在的视觉行
        wrapped = self._cached_wrapped_lines
        cum = 0
        for i, seg in enumerate(wrapped):
            seg_len = len(seg)
            if adjusted_pos <= cum + seg_len:
                # ★ 当光标落在段尾且有下一段时，返回下一段起始
                if adjusted_pos == cum + seg_len and i + 1 < len(wrapped):
                    return (i + 1, 0)
                prefix = seg[:adjusted_pos - cum]
                col = wcswidth(prefix)
                return (i, col)
            cum += seg_len
        # 末尾
        last_idx = len(wrapped) - 1 if wrapped else 0
        last_col = wcswidth(wrapped[-1]) if wrapped else 0
        return (last_idx, last_col)

    def get_status_elapsed(self) -> float:
        """获取状态行最后一次记录的耗时（秒），用于通知等场景。"""
        snap_func = _get_snapshot()
        if snap_func is None:
            return 0.0
        try:
            return snap_func().get("elapsed_seconds", 0.0)
        except Exception:
            return 0.0

    def increment_tool(self) -> None:
        """递增工具调用计数。"""
        self._tool_count += 1

    def increment_tool_fail(self) -> None:
        """递增失败工具计数（工具完成且 success=False 时调用）。"""
        self._tool_fail_count += 1

    def reset_tool_count(self) -> None:
        """重置工具计数（新轮开始时清零）。"""
        self._tool_count = 0
        self._tool_fail_count = 0

    def set_model_name(self, name: str) -> None:
        """设置当前模型名字，状态行实时更新。

        跨线程安全：由 monitor 线程（Ctrl+N/Ctrl+R 回调）和 asyncio 线程
        （_handle_round / 命令处理）两条路径写入。CPython GIL 保证
        简单 str 属性赋值原子安全。读取方 _format_status() 始终在
        output_lock 保护下调用，不存在 torn read。
        """
        self._model_name = name

    def check_resize(self) -> bool:
        """检测终端尺寸变化并自动重设滚动区域（公开方法）。

        代理 _check_resize()，供 ChatUIConsumer 等外部调用方使用，
        避免直接访问私有方法。
        """
        return self._check_resize()

    def _check_resize(self) -> bool:
        """检测终端尺寸变化，自动重设滚动区域。

        在 refresh/redraw 及 reader 线程 drain 循环中调用。
        终端高度不足 _MIN_HEIGHT 时不做任何操作。

        setup() 内部将 _last_text 重置为 "" 并在终端上绘制占位符，
        因此调用方必须在 resize 发生后重新绘制底部栏（force_redraw
        或 refresh 的完整渲染），否则终端视觉状态会停留在占位符上
        而内存中 _last_text 已恢复——用户将看到输入内容被"清空"。

        _last_text 的保存/恢复在 output_lock 保护下完成，
        与 refresh()/force_redraw() 串行化，消除跨线程竞争。

        使用单个 output_lock 作用域包裹全程（output_lock 为 RLock，
        setup() 内部的可重入获取安全），消除双线程并发进入
        _check_resize 时 _last_text 被陈旧值覆盖的竞态。

        Returns:
            True 表示检测到尺寸变化并已处理（setup 重建），调用方
            应随后重绘底部栏以恢复正确的输入文本显示。
            False 表示无变化或未激活。
        """
        if not self._active:
            return False
        height = self._term_height()
        if height != self._setup_height and height >= self._MIN_HEIGHT:
            # 单锁作用域：output_lock 是 RLock，setup() 内部的可重入获取安全
            with _try_acquire_output_lock(name="bottom_bar.check_resize", timeout=1.0) as locked:
                if not locked:
                    return True  # 锁超时仍标记 resize 以触发调用方重绘
                saved_text = self._last_text
                self._active = False
                self.setup()  # RLock 允许可重入嵌套
                self._last_text = saved_text
            return True
        return False

    # ── 光标定位（渲染时在上屏/下屏间切换） ───────────────

    def ensure_cursor_in_upper(self) -> None:
        """将光标移到上屏内容区底部（滚动区域内），准备渲染内容。

        渲染内容前调用：确保 renderer 写入内容时光标在正确区域，
        避免内容误写入底部固定栏（下屏）。
        """
        if not self._active:
            return
        height = self._term_height()
        scroll_end = height - self._bottom_lines  # 滚动区域最后一行（动态）
        sys.__stdout__.write(f"\033[{scroll_end};1H")

    def ensure_cursor_in_lower(self) -> None:
        """渲染完成后将光标移回下屏输入行末尾。

        只做光标跳转，不重绘输入行（避免覆盖用户通过
        左右键移动光标后的位置）。光标停在输入文本末尾。
        超长文本会自动拆行，光标位于最后一行末尾。
        制表符按 _TAB_WIDTH 展开为空格。
        """
        if not self._active:
            return
        height = self._term_height()
        term_w = self._term_width()
        input_row = height  # 输入最后一行 = 终端最后一行
        text = self._last_text or ""
        safe_text = text.replace('\r', '')
        expanded = _expand_tabs(safe_text)
        max_input = max(1, term_w - 4)
        wrapped = _wrap_by_width(expanded, max_input)
        last_seg = wrapped[-1] if wrapped else ""
        col = 3 + wcswidth(last_seg)
        col = min(col, term_w)
        sys.__stdout__.write(f"\033[{input_row};{col}H")

    def setup(self) -> None:
        """启用底部栏：设置滚动区域 + 绘制初始底部栏。

        终端高度不足 _MIN_HEIGHT 时静默跳过，不做任何操作。
        幂等：已激活时重复调用无效果。
        """
        if self._active:
            return
        height = self._term_height()
        if height < self._MIN_HEIGHT:
            return
        self._active = True
        self._last_text = ""
        self._setup_height = height
        self._last_bottom_lines = self._bottom_lines  # 缓存当前底部行数

        scroll_end = height - self._bottom_lines  # 动态滚动区域
        with _try_acquire_output_lock(name="bottom_bar.setup", timeout=1.0) as locked:
            if locked:
                out = sys.__stdout__
                out.write("\0337")                        # 保存光标
                out.write(f"\033[1;{scroll_end}r")       # 设置滚动区域
                self._draw_all_locked(out, height)
                out.write("\0338")                        # 恢复光标
                # ★ 重新保存 SCOSC，供 ParallelDisplay.render_frame 下一帧使用
                out.write(f"\033[{scroll_end};1H\033[s")
                # ★ 光标默认放在下屏（输入行），有渲染时才移到上屏
                out.write(f"\033[{height};1H")
                out.flush()
            else:
                # 降级：无法获取锁时写入简单分隔线
                sys.__stdout__.write("\n" + "─" * 40 + "\n")
                sys.__stdout__.flush()

    def teardown(self) -> None:
        """停用底部栏：重置滚动区域为全屏 + 清理底部残留。

        使用 \0337/\0338 保存/恢复光标，确保不干扰内容区光标位置。
        幂等：未激活时重复调用无效果。
        """
        if not self._active:
            return
        self._active = False

        with _try_acquire_output_lock(name="bottom_bar.teardown", timeout=1.0) as locked:
            if locked:
                out = sys.__stdout__
                out.write("\033[r")                     # 重置滚动区域为全屏
                out.write("\0337")                      # 保存光标
                height = self._term_height()
                # 用 _last_bottom_lines 确保清除所有旧底部行（含动态多行）
                for r in range(height - self._last_bottom_lines + 1, height + 1):
                    out.write(f"\033[{r};1H\033[K")    # 清除底部残留行
                out.write("\0338")                      # 恢复光标
                # ★ 重新保存 SCOSC（\0337 覆盖了保存槽），供 render_frame 使用
                out.write("\033[s")
                out.flush()
        self._last_bottom_lines = _BOTTOM_LINES  # 恢复默认

    # ── 刷新 ──────────────────────────────────────────────

    def redraw(self) -> None:
        """重绘全部底部栏（不改变滚动区域），超长文本自动拆行。

        用于 prompt_toolkit 等外部组件覆盖底部栏后的恢复。
        仅在已激活时有效。
        """
        resized = self._check_resize()
        if not self._active:
            return

        with _try_acquire_output_lock(name="bottom_bar.redraw", timeout=1.0) as locked:
            if not locked:
                return
            # 所有共享状态读写及终端尺寸查询均在 output_lock 保护下，与 refresh()/force_redraw() 串行化
            height = self._term_height()
            if not resized:
                self._last_text = ""
            total = self._bottom_lines
            scroll_end = height - total
            self._last_bottom_lines = total
            out = sys.__stdout__
            out.write("\0337")
            out.write("\033[r")                    # 临时退出滚动区域

            # 清除所有底部行
            for r in range(scroll_end + 1, height + 1):
                out.write(f"\033[{r};1H\033[K")

            self._draw_all_locked(out, height)

            out.write(f"\033[1;{scroll_end}r")   # 恢复滚动区域
            out.write("\0338")
            # ★ 重新保存 SCOSC，供 ParallelDisplay.render_frame 下一帧使用
            #   \0337 (DECSC) 与 \033[s (SCOSC) 在绝大多数终端中共享同一保存槽。
            #   若不重新保存，render_frame 的 \033[u 会恢复到 \0337 保存的位置（输入区），
            #   而非正确的面板结束位置，导致 SubAgent TUI 面板向下偏移。
            out.write(f"\033[{scroll_end};1H\033[s")
            out.flush()

    def force_redraw(self) -> None:
        """无条件重绘全部底部栏（绕过节流和变更检测），超长文本自动拆行。

        所有共享可变状态在 output_lock 保护下更新，与 refresh()
        串行化，消除跨线程竞争。可被任何线程安全调用。

        内置快速路径：状态行文本、输入文本、底部行数三者均未变化时
        跳过全量重绘，仅更新 _last_refresh 时间戳。避免流式输出期间
        高频 CONTENT chunk 触发的冗余终端 I/O（~5 行写入→0 行）。
        """
        self._check_resize()
        if not self._active:
            return

        height = self._term_height()

        with _try_acquire_output_lock(name="bottom_bar.force_redraw", timeout=1.0) as locked:
            if not locked:
                return
            # ★ total/scroll_end 在锁内计算，与 _last_text 原子一致
            text = self._last_text
            total = self._bottom_lines
            new_status = self._format_status()

            # ★ 快速路径：状态、输入文本、布局均未变化 → 跳过
            if (new_status == self._last_status
                    and text == self._last_rendered_text
                    and total == self._last_bottom_lines):
                self._last_refresh = time.monotonic()
                self._last_cursor_pos = self._input_cursor_pos
                return

            scroll_end = height - total
            self._last_refresh = time.monotonic()
            self._last_status = new_status
            old_bottom_lines = self._last_bottom_lines
            self._last_bottom_lines = total

            out = sys.__stdout__
            out.write("\0337")                       # 保存光标
            out.write("\033[r")                      # 临时退出滚动区域

            # 清除之前所有底部行（用 old_bottom_lines 确保清干净）
            old_end = height - old_bottom_lines
            for r in range(old_end + 1, height + 1):
                out.write(f"\033[{r};1H\033[K")

            r1 = height - total + 1                  # 分隔线
            r2 = r1 + 1                              # 状态行

            sep = "─" * min(self._term_width(), 80)
            out.write(f"\033[{r1};1H{_COLOR_DIVIDER}{sep}{_COLOR_RESET}")
            out.write(f"\033[{r2};1H\033[K{self._last_status}")

            # ── 动态拆行输入区 ──
            self._draw_input_lines_locked(out, text, r2 + 1)
            # ★ 清除多余行（复用缓存，避免重算 _wrap_by_width）
            input_rows = self._cached_input_rows if text else 1
            for r in range(r2 + 1 + input_rows, height + 1):
                out.write(f"\033[{r};1H\033[K")

            out.write(f"\033[1;{scroll_end}r")      # 恢复滚动区域
            out.write("\0338")                       # 恢复光标
            # ★ 重新保存 SCOSC，供 ParallelDisplay.render_frame 下一帧使用
            #   （同 redraw 的 SCOSC 保存，见上帧保护注释）
            out.write(f"\033[{scroll_end};1H\033[s")
            out.flush()
            # ★ 同步 _last_cursor_pos，使下次 refresh() 能正确检测光标变化
            self._last_cursor_pos = self._input_cursor_pos

    def refresh(self, text: str, cursor_pos: int = -1) -> None:
        """刷新底部栏全部行（分隔线/状态行/输入行一起刷新），超长文本自动拆行。

        节流 50ms：高频键入时合并刷新，减少锁竞争。

        Args:
            text: 当前输入文本（空字符串则只显示 ◆ 提示符）。
            cursor_pos: 光标在输入文本中的偏移（0=第一个字符后），
                        -1=不定位光标（放在文本末尾）。
        """
        resized = self._check_resize()
        if not self._active:
            return

        # 输入文本变化检查（节流仅在文本变化时生效；状态行始终检查）
        # ★ resize 后 memory 中 _last_text 已恢复但终端视觉仍为占位符，
        #   必须跳过节流，强制重绘修复视觉状态
        now = time.monotonic()
        text_changed = text != self._last_text
        cursor_changed = cursor_pos >= 0 and cursor_pos != self._last_cursor_pos
        if not text_changed and not cursor_changed and not resized and now - self._last_refresh < _BOTTOM_REFRESH_MS:
            return

        # ★ 状态行始终计算（模型名字 / 流式统计），不再依赖 _status_active
        status_changed = False
        new_status = self._format_status()
        status_changed = new_status != self._last_status

        if not text_changed and not status_changed and not cursor_changed and not resized:
            return

        # ── 如果只有光标移动（文本/状态/尺寸均未变），走轻量路径 ──
        if not text_changed and not status_changed and not resized and cursor_changed:
            # ★ 不获取 output_lock：仅写 ANSI 光标定位序列 + 更新 int 状态。
            #   CPython GIL 保证 int 赋值原子性；ANSI 光标定位幂等，即使与
            #   force_redraw() 的 DECSTBM 序列交错，同一次 drain 末尾的
            #   _position_cursor() 会即时纠正。移除 output_lock 避免与 Reader
            #   线程的 _drain_queue 阶段1（上屏渲染）竞争锁，消除方向键影响
            #   上屏渲染的 Bug。
            self._last_cursor_pos = cursor_pos
            self._input_cursor_pos = cursor_pos
            # ★ 一次性获取终端尺寸复用，避免多次 shutil.get_terminal_size()
            term_w = self._term_width()
            term_h = self._term_height()
            max_input = max(1, term_w - 4)
            # ★ 复用缓存的拆行结果，避免重算 _wrap_by_width（O(n·wcswidth)）
            vis_row, vis_col = self._cursor_visual_pos_from_cache(text, cursor_pos, max_input)
            # ★ 复用缓存输入行数（resized=False 时宽度未变，缓存有效）
            total = (2 + self._cached_input_rows
                     if self._cached_wrapped_for == text
                     else self._bottom_lines)
            r_cursor = term_h - total + 3 + vis_row
            cursor_col = min(3 + vis_col, term_w)
            sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
            sys.__stdout__.flush()
            return

        height = self._term_height()

        with _try_acquire_output_lock(name="bottom_bar.refresh", timeout=1.0) as locked:
            if not locked:
                return
            # ★ 在 output_lock 保护下原子更新所有共享可变状态，
            #    与 force_redraw() / _position_cursor() 串行化，消除跨线程竞争
            if text_changed:
                self._last_text = text                  # ★ 先更新 _last_text
                self._input_cursor_pos = cursor_pos
                self._last_refresh = now
            # ★ total/scroll_end 在锁内计算，确保 _last_text 已是最新
            total = self._bottom_lines
            scroll_end = height - total
            old_bottom_lines = self._last_bottom_lines  # 锁内读取，避免竞态
            self._last_bottom_lines = total              # 锁内写入，避免竞态
            if status_changed:
                self._last_status = new_status           # 锁内写入，避免竞态
            elif cursor_pos >= 0:
                # 光标移动（全量重绘路径中同步更新光标位置）
                self._input_cursor_pos = cursor_pos
            self._last_cursor_pos = self._input_cursor_pos
            out = sys.__stdout__
            out.write("\0337")                       # 保存光标（内容区位置）

            # 临时退出滚动区域，以便写入底部行
            out.write("\033[r")

            # 清除旧的底部行
            old_end = height - old_bottom_lines
            for r in range(old_end + 1, height + 1):
                out.write(f"\033[{r};1H\033[K")

            # ── 分隔线 ──
            r1 = height - total + 1
            sep = "─" * min(self._term_width(), 80)
            out.write(f"\033[{r1};1H{_COLOR_DIVIDER}{sep}{_COLOR_RESET}")

            # ── 状态行 ──
            r2 = r1 + 1
            out.write(f"\033[{r2};1H\033[K{new_status}")

            # ── 拆行输入区 ──
            max_input = max(1, self._term_width() - 4)
            self._draw_input_lines_locked(out, text, r2 + 1)
            # ★ 清除多余行（复用缓存，避免重算 _wrap_by_width）
            input_rows = self._cached_input_rows if text else 1
            for r in range(r2 + 1 + input_rows, height + 1):
                out.write(f"\033[{r};1H\033[K")

            # ── 光标定位（使用 _compute_cursor_visual_pos 准确处理 \\n） ──
            vis_row, vis_col = _compute_cursor_visual_pos(
                text, cursor_pos, max_input,
            )
            r_cursor = r2 + 1 + vis_row
            cursor_col = 3 + vis_col
            cursor_col = min(cursor_col, self._term_width())

            # 恢复滚动区域 + 光标
            out.write(f"\033[1;{scroll_end}r")
            out.write("\0338")                       # 恢复光标（内容区位置）
            # ★ 重新保存 SCOSC，供 ParallelDisplay.render_frame 下一帧使用
            #   （同 redraw 的 SCOSC 保存，见上帧保护注释）
            out.write(f"\033[{scroll_end};1H\033[s")
            # ★ 显式移到输入行光标位置（拆行场景正确行号）
            out.write(f"\033[{r_cursor};{cursor_col}H")
            out.flush()

    # ── 内部绘制 ──────────────────────────────────────────

    def _draw_input_lines_locked(self, out, text: str, r_start: int) -> None:
        """绘制输入行（需持有 output_lock），超长文本自动拆行。

        Args:
            out: stdout 文件对象。
            text: 输入文本（空字符串显示占位提示）。
            r_start: 第一行输入区的行号（分隔线+状态行之后）。
        """
        max_input = max(1, self._term_width() - 4)
        expanded = _expand_tabs(text)
        wrapped = _wrap_by_width(expanded, max_input)
        # ★ 更新展开/拆行缓存，供轻量路径复用（text_changed=False 时有效）
        self._cached_wrapped_for = text
        self._cached_wrapped_lines = wrapped
        self._cached_input_rows = max(1, len(wrapped))
        # ★ 同步"上次渲染文本"标记，供 force_redraw 快速路径使用
        self._last_rendered_text = text
        for i, segment in enumerate(wrapped):
            r = r_start + i
            if i == 0:
                if text:
                    out.write(f"\033[{r};1H\033[K{_COLOR_PROMPT}◆{_COLOR_RESET} {segment}")
                else:
                    out.write(f"\033[{r};1H\033[K{_COLOR_PROMPT}◆{_COLOR_RESET} {_COLOR_PLACEHOLDER}{_PLACEHOLDER_TEXT}{_COLOR_RESET}")
            else:
                out.write(f"\033[{r};1H\033[K{_COLOR_PROMPT}│{_COLOR_RESET} {segment}")

    def _draw_all_locked(self, out, height: int) -> None:
        """绘制全部底部行（需持有 output_lock），超长文本自动拆行。

        布局：
          第 1 行：暗灰分隔线（内容区与输入区的视觉边界）
          第 2 行：彩色状态行（橙T│蓝耗时│绿tok│紫t/s）
          第 3 行起：青◆ <text>   （输入提示符 + 实时键入文本，超长拆行）
                    青│ <text>   （续行缩进，维持视觉连贯性）
                    （空输入时显示灰色占位提示）
        """
        total = self._bottom_lines
        r1 = height - total + 1                  # 分隔线
        r2 = r1 + 1                              # 状态行
        sep = "─" * min(self._term_width(), 80)
        out.write(f"\033[{r1};1H{_COLOR_DIVIDER}{sep}{_COLOR_RESET}")
        out.write(f"\033[{r2};1H\033[K")
        # 输入区
        text = self._last_text or ""
        self._draw_input_lines_locked(out, text, r2 + 1)
        # 绘制状态行（在输入区之后写入，覆盖上面以防无状态）
        status = self._format_status()
        if status:
            out.write(f"\033[{r2};1H\033[K{status}")
        self._last_status = status  # 同步缓存，避免下次 refresh() 冗余重绘

    def _format_status(self) -> str:
        """构建状态行文本：模型名 │ T │ 进度条 │ 耗时 │ 总tok │ 实时tok/s。

        始终显示模型名字（无论是否流式），流式期间追加统计信息。

        进度条用法：
          - 有工具调用时：显示工具完成进度（▰▱）
          - 无工具但在流式输出时：显示脉冲动画（| / - \\）
          - 无流式输出时：仅显示模型名字
        """
        # ── 模型名字（始终显示） ──
        model_part = f"{_COLOR_TIME}{self._model_name}{_COLOR_RESET}" if self._model_name else ""

        snap_func = _get_snapshot()
        if snap_func is None:
            return model_part

        try:
            snap = snap_func()
        except Exception:
            return model_part

        total = snap.get("total_tokens", 0)           # 历史累计总tok（永不清空）
        speed = snap.get("per_second_speed", 0.0)     # 每秒实时速度（总tok差值法）
        elapsed = snap.get("elapsed_seconds", 0.0)    # 当轮耗时

        if total <= 0 and elapsed <= 0:
            return model_part

        parts = []

        # 工具调用次数 + 进度条
        if self._tool_count > 0:
            done = max(0, self._tool_count - self._tool_fail_count)
            bar_w = min(8, self._tool_count)
            filled = int(bar_w * done / self._tool_count) if self._tool_count else 0
            empty = bar_w - filled
            if self._tool_fail_count > 0:
                bar = (
                    _COLOR_PROGRESS_DONE + "▰" * filled
                    + _COLOR_TOOL + "▰" * (bar_w - filled)
                    + _COLOR_RESET
                )
                parts.append(
                    f"{_COLOR_TOOL}T{done}{_COLOR_RESET}"
                    f"{_COLOR_TOOL}/{self._tool_count}{_COLOR_RESET}"
                    f" {bar}"
                )
            else:
                bar = (
                    _COLOR_PROGRESS_DONE + "▰" * filled
                    + _COLOR_PROGRESS_BG + "▱" * empty
                    + _COLOR_RESET
                )
                parts.append(
                    f"{_COLOR_TOOL}T{self._tool_count}{_COLOR_RESET}"
                    f" {bar}"
                )
        elif self._status_active and elapsed > 0:
            # 无工具但在流式输出 → 脉冲动画指示器
            spinner_frames = ["|", "/", "-", "\\"]
            idx = int(time.monotonic() * 3) % len(spinner_frames)
            parts.append(f"{_COLOR_SPEED}{spinner_frames[idx]}{_COLOR_RESET}")

        # 耗时
        if elapsed >= 60:
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            if mins < 60:
                dur = f"{mins}:{secs:02d}"
            else:
                hours = mins // 60
                mins %= 60
                dur = f"{hours}:{mins:02d}:{secs:02d}"
        else:
            dur = f"{elapsed:.1f}s"

        if total >= 1000:
            tok_str = f"{total / 1000:.1f}k"
        else:
            tok_str = str(total)

        if speed >= 10:
            spd_str = f"{speed:.0f}"
        elif speed >= 1:
            spd_str = f"{speed:.1f}"
        else:
            spd_str = f"{speed:.2f}"

        parts.append(f"{_COLOR_TIME}{dur}{_COLOR_RESET}")
        parts.append(f"{_COLOR_TOK}{tok_str}t{_COLOR_RESET}")
        parts.append(f"{_COLOR_SPEED}{spd_str}t/s{_COLOR_RESET}")

        status = " │ ".join(parts) if parts else ""
        if model_part and status:
            return f"{model_part} │ {status}"
        return model_part or status

    def _draw_status_locked(self, out, height: int) -> None:
        """仅重绘状态行（需持有 output_lock，在 \033[r 之后调用）。"""
        status = self._format_status()
        if status == self._last_status:
            return
        self._last_status = status
        total = self._bottom_lines
        status_row = height - total + 2  # 分隔线（1行）下方
        out.write(f"\033[{status_row};1H\033[K{status}")

    def refresh_status_only(self) -> None:
        """仅刷新状态行（reader 线程 drain 后调用，10Hz）。

        始终刷新（包含模型名），不再以 _status_active 为门控。
        """
        if not self._active:
            return
        height = self._term_height()

        with _try_acquire_output_lock(name="bottom_bar.status", timeout=1.0) as locked:
            if not locked:
                return
            # ★ scroll_end 在锁内计算，避免与 refresh/force_redraw 的 _last_text 更新竞争
            scroll_end = height - self._bottom_lines
            out = sys.__stdout__
            out.write("\0337")
            out.write("\033[r")
            self._draw_status_locked(out, height)
            out.write(f"\033[1;{scroll_end}r")
            out.write("\0338")
            # ★ 重新保存 SCOSC，供 ParallelDisplay.render_frame 下一帧使用
            out.write(f"\033[{scroll_end};1H\033[s")
            out.flush()
            # ★ 更新时间戳，使 refresh() 的节流检查使用最新时间
            self._last_refresh = time.monotonic()

    # ── 补全弹窗 ──────────────────────────────────────────

    @property
    def is_completion_visible(self) -> bool:
        """补全弹窗是否可见。"""
        return self._completion_visible

    def show_completions(self, items: list[str], selected_idx: int,
                         texts: list[str] | None = None,
                         start_pos: int = 0, orig_prefix: str = "") -> None:
        """在底部栏上方绘制带边框的补全弹窗。

        弹窗视觉：
          ┌ 补全 (N项) ──────────┐
          │ ▸ 选中项              │  ← 反显高亮 + ▸ 指示器
          │   普通项              │
          │ ··· 3/10 ···         │  ← 底部信息行
          └ Tab/↑↓/Esc ──────────┘  ← 快捷键提示

        覆盖在终端底部栏上方（分隔线之上），不修改滚动区域。
        """
        if not items or not self._active:
            return

        total_items = len(items)
        h_items = min(total_items, self._COMPLETION_MAX_ITEMS)
        # 弹窗 = 顶边框 + N 项 + 底边框 = N+2 行
        popup_height = h_items + 2
        max_avail = self._term_height() - self._bottom_lines - 1
        if max_avail <= 0:
            return
        # 空间不够时缩减项数
        if popup_height > max_avail:
            h_items = max(1, max_avail - 2)
            popup_height = h_items + 2
        visible_items = items[:h_items]
        # ★ 防御：截断后 selected_idx 可能越界
        selected_idx = min(selected_idx, h_items - 1)

        with _try_acquire_output_lock(name="bottom_bar.comp_show", timeout=1.0) as locked:
            if not locked:
                return
            out = sys.__stdout__
            out.write("\0337")
            height = self._term_height()
            popup_start = height - self._bottom_lines - popup_height + 1
            tw = self._term_width()
            popup_w = min(tw - 2, 50)

            # ── 顶边框 ──
            header = f" 补全 ({total_items}项) "
            header_vw = _visual_len(header)
            # 边框字符各占 1 列：┌ + ┐ = 2，中间全填 ─
            pad_w = max(1, popup_w - header_vw - 2)
            top_border = (_COLOR_COMP_BORDER + "\u250c"
                          + header
                          + "\u2500" * pad_w
                          + "\u2510" + _COLOR_RESET)
            out.write(f"\033[{popup_start};1H\033[K{top_border}\033[K")

            # ── 选项行 ──
            for i, item in enumerate(visible_items):
                r = popup_start + 1 + i
                cell_w = popup_w - 4  # │ + 空格 + 内容 + 空格 + │
                display = _truncate_by_width(item, cell_w - 2)  # -2 for "▸ "
                pad = " " * max(0, cell_w - 2 - _visual_len(display))
                if i == selected_idx:
                    out.write(f"\033[{r};1H\033[K"
                              f"{_COLOR_COMP_BORDER}\u2502{_COLOR_RESET} "
                              f"{_COLOR_COMP_SELECTED_BG}{_COLOR_PROMPT}\u25b8{_COLOR_RESET}"
                              f"{_COLOR_COMP_SELECTED_BG} {display}{pad}{_COLOR_RESET}"
                              f"{_COLOR_COMP_BORDER}\u2502{_COLOR_RESET}")
                else:
                    out.write(f"\033[{r};1H\033[K"
                              f"{_COLOR_COMP_BORDER}\u2502{_COLOR_RESET}  "
                              f" {display}{pad}"
                              f"{_COLOR_COMP_BORDER}\u2502{_COLOR_RESET}")

            # ── 底边框 ──
            footer_start = popup_start + 1 + h_items
            truncated = total_items > h_items
            if truncated:
                hint = f" {selected_idx + 1}/{h_items} (\u524d{h_items}/{total_items})  Tab/\u2191\u2193/Esc "
            else:
                hint = " Tab/\u2191\u2193/Esc "
            hint_vw = _visual_len(hint)
            pad_w = max(1, popup_w - hint_vw - 2)
            bottom_border = (_COLOR_COMP_BORDER + "\u2514" + hint
                             + "\u2500" * pad_w + "\u2518" + _COLOR_RESET)
            out.write(f"\033[{footer_start};1H\033[K{bottom_border}\033[K")

            out.write("\0338")
            # ★ 重新保存 SCOSC，供 ParallelDisplay.render_frame 下一帧使用
            scroll_end = height - self._bottom_lines
            out.write(f"\033[{scroll_end};1H\033[s")
            # ★ 修复：恢复光标到 DECSC 位置（输入行），防止后续 _echo 锁超时导致光标跳到上屏
            out.write("\0338")
            out.flush()

            # ★ 状态写入移入锁内
            self._completion_visible = True
            self._completion_items = list(visible_items)
            self._completion_texts = list(texts) if texts is not None else list(visible_items)
            self._completion_idx = selected_idx
            self._completion_start_pos = start_pos
            self._completion_orig_prefix = orig_prefix

    def hide_completions(self) -> None:
        """清除补全弹窗（含边框），恢复底部栏上方的终端行。

        幂等：弹窗未显示时无效果。
        """
        if not self._completion_visible or not self._active:
            return

        m_height = len(self._completion_items) + 2  # +2 for borders
        m_height = min(m_height, self._COMPLETION_MAX_ITEMS + 2)

        with _try_acquire_output_lock(name="bottom_bar.comp_hide", timeout=1.0) as locked:
            if not locked:
                return
            out = sys.__stdout__
            out.write("\0337")
            height = self._term_height()
            popup_start = height - self._bottom_lines - m_height + 1
            for i in range(m_height):
                r = popup_start + i
                out.write(f"\033[{r};1H\033[K")
            out.write("\0338")
            # ★ 重新保存 SCOSC，供 ParallelDisplay.render_frame 下一帧使用
            scroll_end = height - self._bottom_lines
            out.write(f"\033[{scroll_end};1H\033[s")
            # ★ 修复：恢复光标到 DECSC 位置（输入行），与 show_completions/cycle_completion 一致
            out.write("\0338")
            out.flush()

            # ★ 状态归零移入锁内
            self._completion_visible = False
            self._completion_items = []
            self._completion_texts = []
            self._completion_idx = 0
            self._completion_start_pos = 0
            self._completion_orig_prefix = ""

    def cycle_completion(self, delta: int = 1) -> int:
        """循环切换补全选中项，更新弹窗高亮和 footer 位置信息。

        Args:
            delta: +1 下一项，-1 上一项。

        Returns:
            新的选中索引。
        """
        if not self._completion_visible or not self._completion_items:
            return 0

        n = len(self._completion_items)
        old_idx = self._completion_idx
        self._completion_idx = (old_idx + delta) % n

        # 仅重绘选项行 + footer（不重绘边框）
        popup_height = n + 2
        with _try_acquire_output_lock(name="bottom_bar.comp_cycle", timeout=1.0) as locked:
            if not locked:
                return self._completion_idx
            out = sys.__stdout__
            out.write("\0337")
            height = self._term_height()
            popup_start = height - self._bottom_lines - popup_height + 1
            tw = self._term_width()
            popup_w = min(tw - 2, 50)
            cell_w = popup_w - 4

            for i, item in enumerate(self._completion_items):
                r = popup_start + 1 + i
                display = _truncate_by_width(item, cell_w - 2)
                pad = " " * max(0, cell_w - 2 - _visual_len(display))
                if i == self._completion_idx:
                    out.write(f"\033[{r};1H\033[K"
                              f"{_COLOR_COMP_BORDER}\u2502{_COLOR_RESET} "
                              f"{_COLOR_COMP_SELECTED_BG}{_COLOR_PROMPT}\u25b8{_COLOR_RESET}"
                              f"{_COLOR_COMP_SELECTED_BG} {display}{pad}{_COLOR_RESET}"
                              f"{_COLOR_COMP_BORDER}\u2502{_COLOR_RESET}")
                else:
                    out.write(f"\033[{r};1H\033[K"
                              f"{_COLOR_COMP_BORDER}\u2502{_COLOR_RESET}  "
                              f" {display}{pad}"
                              f"{_COLOR_COMP_BORDER}\u2502{_COLOR_RESET}")

            # ★ 更新 footer 位置信息（仅显示可见范围）
            total_items = len(self._completion_texts) if self._completion_texts else n
            footer_start = popup_start + 1 + n
            truncated = total_items > n
            if truncated:
                hint = f" {self._completion_idx + 1}/{n} (\u524d{n}/{total_items})  Tab/\u2191\u2193/Esc "
            else:
                hint = " Tab/\u2191\u2193/Esc "
            hint_vw = _visual_len(hint)
            pad_w = max(1, popup_w - hint_vw - 2)
            bottom_border = (_COLOR_COMP_BORDER + "\u2514" + hint
                             + "\u2500" * pad_w + "\u2518" + _COLOR_RESET)
            out.write(f"\033[{footer_start};1H\033[K{bottom_border}\033[K")

            out.write("\0338")
            # ★ 重新保存 SCOSC，供 ParallelDisplay.render_frame 下一帧使用
            scroll_end = height - self._bottom_lines
            out.write(f"\033[{scroll_end};1H\033[s")
            # ★ 修复：恢复光标到 DECSC 位置（输入行），防止后续 _echo 锁超时导致光标跳到上屏
            out.write("\0338")
            out.flush()

        return self._completion_idx

    def get_selected_completion(self) -> tuple[str, int, str]:
        """获取当前选中补全项的数据。

        Returns:
            (replacement_text, start_pos, orig_prefix) 三元组。
        """
        if not self._completion_visible or not self._completion_texts:
            return ("", 0, "")
        idx = min(self._completion_idx, len(self._completion_texts) - 1)
        return (
            self._completion_texts[idx],
            self._completion_start_pos,
            self._completion_orig_prefix,
        )
