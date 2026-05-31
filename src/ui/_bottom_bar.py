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

# ── ANSI 颜色常量（优雅视觉风） ─────────────────────
_COLOR_ACCENT = "\033[38;5;39m"       # 青色强调（提示符/模型名/状态）
_COLOR_BRIGHT_ACCENT = "\033[1;96m"   # 亮青加粗（输入提示符）
_COLOR_CYAN = "\033[38;5;44m"         # 青（输入提示符中间色）
_COLOR_DEEP_CYAN = "\033[38;5;30m"    # 深青（输入提示符最暗色）
_COLOR_DIM = "\033[38;5;245m"         # 灰色次要（分隔线/占位/统计）
_COLOR_RESET = "\033[0m"              # 重置
_COLOR_SELECT_BG = "\033[48;5;238m"   # 选中项高亮背景（深灰背景，#238 比 #236 略亮，改善 light 主题可见性）
_COLOR_SELECT_FG = "\033[38;5;15m"    # 选中项前景色（亮白，确保反显高对比度）
_COLOR_ERR = "\033[38;5;1m"           # 红色错误
_COLOR_SEP = "\033[38;5;237m"         # 分隔线深灰
_COLOR_COMPLETE_TITLE = "\033[1;38;5;45m"   # 补全弹窗标题色（亮青加粗）
_COLOR_BRIGHT_GREEN = "\033[38;5;40m" # 亮绿（状态用/工具成功）
_COLOR_TOOL_OK = "\033[38;5;40m"      # 工具成功计数
_COLOR_TOOL_FAIL = "\033[38;5;9m"     # 工具失败计数
_COLOR_TIME = "\033[38;5;110m"        # 蓝灰（耗时/时间戳）
_COLOR_TOKEN = "\033[38;5;68m"        # 靛蓝（Token 计数）
_COLOR_SPEED = "\033[38;5;214m"       # 琥珀色（速率）
_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"  # 补全弹窗可见时使用
_PLACEHOLDER_STREAMING = "AI 生成中..."   # 流式输出期间使用


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

    视觉风格（优雅信息风）：
      - 分隔线：蓝灰 `━` 实线做内容区与输入区边界
      - 状态行：多色分层（◉ 模型名·耗时·令牌数·工具计数）
                使用亮青/蓝灰/灰色三层颜色，信息密度高但易读
      - 输入区：亮青 `❯` 提示符，空输入时显示灰色占位提示
                多行续行以灰色 `·` 前缀连接，视觉连贯
      - 补全弹窗：无边框扁平样式（标题行 + ▶ 指示器高亮 + 快捷键提示）

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
        self._cached_input_rows: int = 3      # 缓存输入区视觉行数（不含分隔线/状态行，最少 3 行）
        # ★ 上次渲染到终端的输入文本（显式标记，仅在 _draw_input_lines_locked 中更新，
        #    供 force_redraw 快速路径使用，避免 _cached_wrapped_for 承担双重语义）
        self._last_rendered_text: str = ""
        # ── 当前模型名字（供状态行显示） ──
        self._model_name = ""
        # ── 补全弹窗状态 ──
        self._completion_visible = False
        self._completion_title = "补全"              # 弹窗标题前缀
        self._completion_items: list[str] = []     # 显示文本
        self._completion_texts: list[str] = []      # 替换文本（可能与显示不同）
        self._completion_start_pos: int = 0         # 从光标前多少字符开始替换
        self._completion_orig_prefix: str = ""      # 原始前缀（用于重建替换）
        self._completion_idx = 0
        # ★ 补全弹窗所占行数（弹窗可见时 > 0，用于扩展输入区）
        self._completion_popup_height: int = 0

    # ── 动态行数计算 ──────────────────────────────────────

    @property
    def _bottom_lines(self) -> int:
        """当前底部栏总行数（分隔线 + 状态行 + 输入行），根据输入内容动态计算。"""
        return 2 + self._compute_input_rows()

    def _compute_input_rows(self) -> int:
        """根据当前输入文本计算所需的输入行数（最少 3 行 + 补全弹窗高度）。"""
        text = self._last_text or ""
        if not text:
            base = 3
        else:
            max_input = max(1, self._term_width() - 4)
            expanded = _expand_tabs(text)
            wrapped = _wrap_by_width(expanded, max_input)
            base = max(3, len(wrapped))
        return base + self._completion_popup_height

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

    # ── 滚动上屏内容（防止底部栏扩大时覆盖内容） ────────────

    @staticmethod
    def _scroll_up_upper(delta: int, out, height: int) -> None:
        """输入区扩大时，将上屏内容向上滚动 delta 行，防止被新底部栏覆盖。

        须在 ``\\033[r``（全屏滚动区）之后、清除旧底部行之前调用。
        仅在 delta > 0 时执行，delta ≤ 0 时静默跳过。

        ANSI 机制：
        - ``\\033[{height};1H`` 定位光标到终端最后一行
        - ``\\n``（LF）在最后一行触发向上滚动，内容整体上移 1 行
        - 重复 delta 次，顶端 delta 行滚动消失，底部 delta 行变为空行
        - 原在"死区"（新滚动区与旧滚动区之间）的内容被移入新滚动区内

        Args:
            delta: 底部栏扩大行数（= new_bottom_lines - old_bottom_lines）。
            out: stdout 文件对象。
            height: 终端总行数。
        """
        if delta > 0:
            out.write(f"\033[{height};1H")
            out.write("\n" * delta)

    @staticmethod
    def _scroll_down_for_shrink(delta: int, out) -> None:
        """缩小底部栏后，将上屏内容向下滚动以消除空隙。

        须在 \\033[r（全屏滚动区）之后、清除旧的底部行之后、
        绘制新的底部栏之前调用。仅在 delta < 0 时执行。

        ANSI 机制：
        - ``\\033[{|delta|}T``（SD — Scroll Down）将滚动区内全部内容
          向下滚动 |delta| 行，滚动区顶部出现 |delta| 行空白，
          底部 |delta| 行滚出（清除掉的旧底部行区域），
          内容区底部与新的较小底部栏顶部接合，消除空隙。

        Args:
            delta: 底部栏变化行数（负=缩小）。
            out: stdout 文件对象。
        """
        if delta < 0:
            out.write(f"\033[{-delta}T")

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
                saved_bottom = self._last_bottom_lines  # ★ 保存 resize 前的底部行数
                self._active = False
                self.setup()  # RLock 允许可重入嵌套
                self._last_text = saved_text
                self._last_bottom_lines = saved_bottom  # ★ 恢复旧值，供 force_redraw 正确计算 old_end/delta
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
        """渲染完成后将光标移回下屏输入行末尾（含动态拆行，最少3行输入区）。

        只做光标跳转，不重绘输入行（避免覆盖用户通过
        左右键移动光标后的位置）。光标停在输入文本末尾。
        超长文本会自动拆行，光标位于最后一行末尾。
        空输入时光标位于输入区第一行（> 提示符行）。
        制表符按 _TAB_WIDTH 展开为空格。
        """
        if not self._active:
            return
        height = self._term_height()
        term_w = self._term_width()
        text = self._last_text or ""
        cursor_pos = self._input_cursor_pos
        max_input = max(1, term_w - 4)
        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        total = self._bottom_lines
        r_cursor = height - total + 3 + vis_row
        col = min(3 + vis_col, term_w)
        sys.__stdout__.write(f"\033[{r_cursor};{col}H")

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
                sys.__stdout__.write("\n" + "\u2501" * 40 + "\n")
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

        ★ 性能优化：先检查文本和布局是否变化，确认需要重绘后再
        调用 _format_status()（含 shutil 系统调用），避免不必要开销。
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

            # ★ 快速路径：先检查输入文本和布局（轻量比较），
            #   确认需要重绘后再调用 _format_status()（含 shutil 系统调用）
            layout_unchanged = text == self._last_rendered_text and total == self._last_bottom_lines
            if layout_unchanged:
                new_status = self._format_status()
                if new_status == self._last_status:
                    self._last_refresh = time.monotonic()
                    self._last_cursor_pos = self._input_cursor_pos
                    return
            else:
                new_status = self._format_status()

            scroll_end = height - total
            self._last_refresh = time.monotonic()
            self._last_status = new_status
            old_bottom_lines = self._last_bottom_lines
            self._last_bottom_lines = total

            out = sys.__stdout__
            out.write("\0337")                       # 保存光标
            out.write("\033[r")                      # 临时退出滚动区域

            # ★ 输入区扩大时，上屏底部内容会被新底部栏覆盖。
            #   先将上屏内容向上滚动 delta 行，保护"死区"内容移入新滚动区。
            delta = total - old_bottom_lines
            self._scroll_up_upper(delta, out, height)

            # 清除之前所有底部行（用 old_bottom_lines 确保清干净）
            old_end = height - old_bottom_lines
            for r in range(old_end + 1, height + 1):
                out.write(f"\033[{r};1H\033[K")

            # ★ 底部栏缩小时，内容区底部与新的较小底部栏之间出现空隙。
            #   将内容向下滚动以消除间隙。
            self._scroll_down_for_shrink(delta, out)

            r1 = height - total + 1                  # 分隔线
            r2 = r1 + 1                              # 状态行

            # ★ 灰色分隔线
            tw = self._term_width()
            sep_len = min(tw - 2, 40)
            sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
            out.write(f"\033[{r1};1H  {sep}")
            out.write(f"\033[{r2};1H\033[K{self._last_status}")

            # ── 动态拆行输入区 ──
            self._draw_input_lines_locked(out, text, r2 + 1)
            # ★ 清除多余行（复用缓存，避免重算 _wrap_by_width）
            input_rows = self._cached_input_rows
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
            text: 当前输入文本（空字符串则只显示 > 提示符）。
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

            # ★ 输入区扩大时，上屏底部内容会被新底部栏覆盖。
            #   先将上屏内容向上滚动 delta 行，保护"死区"内容移入新滚动区。
            delta = total - old_bottom_lines
            self._scroll_up_upper(delta, out, height)

            # 清除旧的底部行
            old_end = height - old_bottom_lines
            for r in range(old_end + 1, height + 1):
                out.write(f"\033[{r};1H\033[K")

            # ★ 底部栏缩小时，内容区底部与新的较小底部栏之间出现空隙。
            #   将内容向下滚动以消除间隙。
            self._scroll_down_for_shrink(delta, out)

            # ── 分隔线（灰色） ──
            r1 = height - total + 1
            tw = self._term_width()
            sep_len = min(tw - 2, 40)
            sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
            out.write(f"\033[{r1};1H  {sep}")

            # ── 状态行 ──
            r2 = r1 + 1
            out.write(f"\033[{r2};1H\033[K{new_status}")

            # ── 拆行输入区 ──
            max_input = max(1, self._term_width() - 4)
            self._draw_input_lines_locked(out, text, r2 + 1)
            # ★ 清除多余行（复用缓存，避免重算 _wrap_by_width）
            input_rows = self._cached_input_rows
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
        base_rows = max(3, len(wrapped))
        self._cached_input_rows = base_rows + self._completion_popup_height
        # ★ 同步"上次渲染文本"标记，供 force_redraw 快速路径使用
        self._last_rendered_text = text

        popup_height = self._completion_popup_height

        # ── 补全弹窗（在输入区顶部绘制，弹出时自动扩大输入行数） ──
        if popup_height > 0 and self._completion_items:
            popup_r_start = r_start
            tw = self._term_width()
            popup_w = min(tw - 2, 50)
            n = len(self._completion_items)

            # ★ 无边框扁平样式：标题行 + 选项列表 + 快捷键提示
            total_items = len(self._completion_texts)
            header = f" {_COLOR_COMPLETE_TITLE}{self._completion_title}{_COLOR_RESET} {_COLOR_DIM}({total_items}项){_COLOR_RESET}"
            out.write(f"\033[{popup_r_start};1H\033[K{header}")

            # 选项行
            cell_w = popup_w - 3  # 无边框，减去左侧 ▶/空格 占用
            for i, item in enumerate(self._completion_items):
                r = popup_r_start + 1 + i
                display = _truncate_by_width(item, cell_w)
                pad = " " * max(0, cell_w - _visual_len(display))
                if i == self._completion_idx:
                    out.write(f"\033[{r};1H\033[K"
                              f" {_COLOR_SELECT_BG}{_COLOR_SELECT_FG}\u25b6{_COLOR_RESET}"
                              f"{_COLOR_SELECT_BG}{_COLOR_SELECT_FG} {display}{pad}{_COLOR_RESET}")
                else:
                    out.write(f"\033[{r};1H\033[K"
                              f"  {display}{pad}")

            # ★ 快捷键提示行
            footer_r = popup_r_start + 1 + n
            truncated = total_items > n
            is_selection = (self._completion_title != "补全")
            if is_selection:
                hint_prefix = "\u2191\u2193 Enter Esc"
            else:
                hint_prefix = "Tab \u2191\u2193 Esc"
            if truncated:
                hint = f" {_COLOR_TIME}{self._completion_idx + 1}/{n}{_COLOR_RESET} {_COLOR_DIM}(\u524d{n}/{total_items}){_COLOR_RESET}  {hint_prefix} "
            else:
                hint = f" {hint_prefix} "
            out.write(f"\033[{footer_r};1H\033[K{_COLOR_DIM}{hint}{_COLOR_RESET}")

        # ── 输入文本行（在弹窗下方） ──
        text_start = r_start + popup_height
        for i, segment in enumerate(wrapped):
            r = text_start + i
            if i == 0:
                if text:
                    # ★ 输入提示符
                    out.write(f"\033[{r};1H\033[K"
                              f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                              f" {segment}")
                else:
                    # ★ 流式输出期间显示状态提示，让用户明确知道 AI 正在生成回复
                    if self._status_active:
                        ph = _PLACEHOLDER_STREAMING
                        out.write(f"\033[{r};1H\033[K"
                                  f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                                  f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
                    else:
                        # ★ 补全弹窗可见时使用紧凑占位符，避免占位符与弹窗视觉重叠
                        ph = _PLACEHOLDER_COMPACT if self._completion_visible else _PLACEHOLDER_TEXT
                        out.write(f"\033[{r};1H\033[K"
                                  f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                                  f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
            else:
                out.write(f"\033[{r};1H\033[K{_COLOR_DIM}\u00b7 {segment}{_COLOR_RESET}")
        # ★ 填充剩余空白行，确保输入区至少 3 行
        for r in range(text_start + len(wrapped), text_start + 3):
            out.write(f"\033[{r};1H\033[K  ")

    def _draw_all_locked(self, out, height: int) -> None:
        """绘制全部底部行（需持有 output_lock），超长文本自动拆行。

        布局（简约风）：
          第 1 行：左青右灰渐变分隔线（内容区与输入区的视觉边界）
          第 2 行：状态行（模型名·耗时·令牌数，青/灰两色）
          第 3 行起：青 ❯ <text>   （输入提示符 + 实时键入文本，超长拆行）
                     灰 · <text>    （续行，· 前缀）
                     （空输入时显示灰色占位提示）
        """
        total = self._bottom_lines
        r1 = height - total + 1                  # 分隔线
        r2 = r1 + 1                              # 状态行
        # ★ 灰色分隔线
        tw = self._term_width()
        sep_len = min(tw - 2, 40)
        sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
        out.write(f"\033[{r1};1H  {sep}")
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
        """构建状态行文本（优雅信息风）：模型名 · 耗时 · 令牌数 · 实时速率。

        始终显示模型名字和可用的统计信息（耗时、令牌数、实时速率）。
        使用多色分层：模型名高亮（带 ◉）、耗时蓝灰色、令牌数灰色。
        工具计数值得高亮区分成功/失败（成功绿/失败红）。
        """
        # ── 模型名字（始终显示，带 ◉ 图标） ──
        model_part = (
            f"{_COLOR_ACCENT}\u25c9{_COLOR_RESET} {_COLOR_ACCENT}{self._model_name}{_COLOR_RESET}"
            if self._model_name else ""
        )

        snap_func = _get_snapshot()
        if snap_func is None:
            return model_part

        try:
            snap = snap_func()
        except Exception:
            return model_part

        total = snap.get("total_tokens", 0)           # 历史累计总tok
        elapsed = snap.get("elapsed_seconds", 0.0)    # 当轮耗时
        per_second_speed = snap.get("per_second_speed", 0.0)  # 实时 tok/s

        if total <= 0 and elapsed <= 0 and per_second_speed <= 0 and self._tool_count <= 0:
            return model_part

        parts = []

        # 工具调用计数（带 ⚙ 图标，成功/失败分色）
        if self._tool_count > 0:
            done = max(0, self._tool_count - self._tool_fail_count)
            if self._tool_fail_count > 0:
                parts.append(
                    f"{_COLOR_TOOL_OK}{done}{_COLOR_RESET}"
                    f"{_COLOR_DIM}/{_COLOR_RESET}"
                    f"{_COLOR_TOOL_FAIL}{self._tool_count}{_COLOR_RESET}"
                )
            else:
                parts.append(f"{_COLOR_TOOL_OK}{self._tool_count}{_COLOR_RESET}")

        # 耗时（蓝灰高亮）
        if elapsed > 0:
            if elapsed >= 60:
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                dur = f"{mins}:{secs:02d}" if mins < 60 else f"{mins // 60}:{mins % 60:02d}:{secs:02d}"
            else:
                dur = f"{elapsed:.1f}s"
            parts.append(f"{_COLOR_TIME}{dur}{_COLOR_RESET}")

        # 令牌数（靛蓝色，更醒目）
        if total > 0:
            tok_str = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
            parts.append(f"{_COLOR_TOKEN}{tok_str}t{_COLOR_RESET}")

        # 实时 token 速度（tok/s，琥珀色高亮）
        if per_second_speed > 0:
            if per_second_speed >= 1:
                speed_str = f"{per_second_speed:.1f}"
            else:
                speed_str = f"{per_second_speed:.2f}"
            parts.append(f"{_COLOR_SPEED}{speed_str}t/s{_COLOR_RESET}")

        sep = f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        status = sep.join(parts) if parts else ""
        if model_part and status:
            return f"{model_part}  {status}"
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
                         start_pos: int = 0, orig_prefix: str = "",
                         title: str = "补全") -> None:
        """在输入区内部绘制无边框扁平补全弹窗，自动扩大输入区域。

        弹窗视觉（无边框扁平样式）：
          {title} (N项)            ← 标题行
            ▶ 选中项              ← ▶ 指示器 + 高亮背景
              普通项              ← 缩进对齐
          ↑↓/Enter/Esc            ← 快捷键提示

        弹窗作为输入区的一部分绘制，弹出时输入区域自动扩大。

        Args:
            items: 显示文本列表。
            selected_idx: 初始选中索引。
            texts: 替换文本列表（与 items 一一对应），默认同 items。
            start_pos: 替换起始位置（相对光标）。
            orig_prefix: 原始前缀。
            title: 弹窗标题前缀（如"补全"、"选择"），显示在标题行。
        """
        if not items or not self._active:
            return

        total_items = len(items)
        h_items = min(total_items, self._COMPLETION_MAX_ITEMS)
        # 弹窗 = 顶边框 + N 项 + 底边框 = N+2 行
        popup_height = h_items + 2
        # ★ 空间不够时缩减项数（基于终端总高度，输入区会扩展）
        # 至少留 5 行：分隔线(1) + 状态行(1) + 最少 3 行输入区
        max_avail = self._term_height() - 5
        if max_avail <= 0:
            return
        if popup_height > max_avail:
            h_items = max(1, max_avail - 2)
            popup_height = h_items + 2
        visible_items = items[:h_items]
        # ★ 防御：截断后 selected_idx 可能越界
        selected_idx = min(selected_idx, h_items - 1)

        with _try_acquire_output_lock(name="bottom_bar.comp_show", timeout=1.0) as locked:
            if not locked:
                return

            # ★ 先设置弹窗高度，使 _bottom_lines / _compute_input_rows 返回扩展后的值
            self._completion_popup_height = popup_height
            self._completion_visible = True
            self._completion_title = title
            self._completion_items = list(visible_items)
            self._completion_texts = list(texts) if texts is not None else list(visible_items)
            self._completion_idx = selected_idx
            self._completion_start_pos = start_pos
            self._completion_orig_prefix = orig_prefix

            out = sys.__stdout__
            out.write("\0337")
            height = self._term_height()
            total = self._bottom_lines  # 已包含 popup 高度
            scroll_end = height - total
            old_bottom_lines = self._last_bottom_lines
            self._last_bottom_lines = total

            # 临时退出滚动区域
            out.write("\033[r")

            # ★ 输入区扩大时，上屏底部内容会被新底部栏覆盖。
            #   先将上屏内容向上滚动 delta 行，保护"死区"内容移入新滚动区。
            delta = total - old_bottom_lines
            self._scroll_up_upper(delta, out, height)

            # 清除旧的底部行
            old_end = height - old_bottom_lines
            for r in range(old_end + 1, height + 1):
                out.write(f"\033[{r};1H\033[K")

            # ★ 底部栏缩小时，内容区底部与新的较小底部栏之间出现空隙。
            #   将内容向下滚动以消除间隙。
            self._scroll_down_for_shrink(delta, out)

            # 全量重绘底部栏（含输入区内的补全弹窗）
            r1 = height - total + 1
            r2 = r1 + 1
            tw_s = self._term_width()
            sep_len_s = min(tw_s - 2, 40)
            sep_s = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len_s
            out.write(f"\033[{r1};1H  {sep_s}")
            out.write(f"\033[{r2};1H\033[K")
            text = self._last_text or ""
            self._draw_input_lines_locked(out, text, r2 + 1)
            status = self._format_status()
            if status:
                out.write(f"\033[{r2};1H\033[K{status}")
            self._last_status = status

            out.write(f"\033[1;{scroll_end}r")
            out.write("\0338")
            # ★ 重新保存 SCOSC
            out.write(f"\033[{scroll_end};1H\033[s")
            # ★ 显式定位光标到输入行
            vis_row, vis_col = _compute_cursor_visual_pos(
                text, -1, max(1, self._term_width() - 4),
            )
            r_cursor = r2 + 1 + vis_row
            cursor_col = min(3 + vis_col, self._term_width())
            out.write(f"\033[{r_cursor};{cursor_col}H")
            out.flush()

    def hide_completions(self) -> None:
        """清除补全弹窗，缩小输入区域恢复原状。

        幂等：弹窗未显示时无效果。
        """
        if not self._completion_visible or not self._active:
            return

        with _try_acquire_output_lock(name="bottom_bar.comp_hide", timeout=1.0) as locked:
            if not locked:
                return

            # ★ 先置零弹窗高度，使 _bottom_lines 恢复为不含弹窗的值
            self._completion_popup_height = 0
            self._completion_visible = False
            self._completion_title = "补全"
            self._completion_items = []
            self._completion_texts = []
            self._completion_idx = 0
            self._completion_start_pos = 0
            self._completion_orig_prefix = ""

            out = sys.__stdout__
            out.write("\0337")
            height = self._term_height()
            total = self._bottom_lines  # 已不含 popup 高度
            scroll_end = height - total
            old_bottom_lines = self._last_bottom_lines  # 旧的含弹窗的底部行数
            self._last_bottom_lines = total

            # 临时退出滚动区域
            out.write("\033[r")

            # 清除旧的底部行（用 old_bottom_lines 确保清干净所有旧行）
            old_end = height - old_bottom_lines
            for r in range(old_end + 1, height + 1):
                out.write(f"\033[{r};1H\033[K")

            # ★ 底部栏缩小后，内容区底部与新的较小底部栏之间出现空隙。
            #   将内容向下滚动以消除间隙。
            self._scroll_down_for_shrink(total - old_bottom_lines, out)

            # 全量重绘底部栏（缩小后的区域）
            r1 = height - total + 1
            r2 = r1 + 1
            tw_h = self._term_width()
            sep_len_h = min(tw_h - 2, 40)
            sep_h = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len_h
            out.write(f"\033[{r1};1H  {sep_h}")
            out.write(f"\033[{r2};1H\033[K")
            text = self._last_text or ""
            self._draw_input_lines_locked(out, text, r2 + 1)
            status = self._format_status()
            if status:
                out.write(f"\033[{r2};1H\033[K{status}")
            self._last_status = status

            out.write(f"\033[1;{scroll_end}r")
            out.write("\0338")
            # ★ 重新保存 SCOSC
            out.write(f"\033[{scroll_end};1H\033[s")
            # ★ 显式定位光标到输入行
            vis_row, vis_col = _compute_cursor_visual_pos(
                text, -1, max(1, self._term_width() - 4),
            )
            r_cursor = r2 + 1 + vis_row
            cursor_col = min(3 + vis_col, self._term_width())
            out.write(f"\033[{r_cursor};{cursor_col}H")
            out.flush()

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

        # 仅重绘选项行 + footer（无边框扁平样式，无需绘制边框字符）
        with _try_acquire_output_lock(name="bottom_bar.comp_cycle", timeout=1.0) as locked:
            if not locked:
                return self._completion_idx
            out = sys.__stdout__
            out.write("\0337")
            height = self._term_height()
            total = self._bottom_lines  # 已含 popup 高度
            # 弹窗在输入区顶部：分隔线+状态行之后的第一行
            popup_start = height - total + 3
            tw = self._term_width()
            popup_w = min(tw - 2, 50)
            cell_w = popup_w - 3

            for i, item in enumerate(self._completion_items):
                r = popup_start + 1 + i
                display = _truncate_by_width(item, cell_w)
                pad = " " * max(0, cell_w - _visual_len(display))
                if i == self._completion_idx:
                    out.write(f"\033[{r};1H\033[K"
                              f" {_COLOR_SELECT_BG}{_COLOR_SELECT_FG}\u25b6{_COLOR_RESET}"
                              f"{_COLOR_SELECT_BG}{_COLOR_SELECT_FG} {display}{pad}{_COLOR_RESET}")
                else:
                    out.write(f"\033[{r};1H\033[K"
                              f"  {display}{pad}")

            # ★ 快捷键提示行
            total_items = len(self._completion_texts) if self._completion_texts else n
            footer_start = popup_start + 1 + n
            truncated = total_items > n
            is_selection = (self._completion_title != "补全")
            hint_prefix = "\u2191\u2193 Enter Esc" if is_selection else "Tab \u2191\u2193 Esc"
            if truncated:
                hint = (f" {_COLOR_TIME}{self._completion_idx + 1}/{n}{_COLOR_RESET}"
                        f" {_COLOR_DIM}(\u524d{n}/{total_items}){_COLOR_RESET}  {hint_prefix} ")
            else:
                hint = f" {hint_prefix} "
            out.write(f"\033[{footer_start};1H\033[K{_COLOR_DIM}{hint}{_COLOR_RESET}")

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


# ── 底部栏交互选择（纯标准库，无外部依赖） ───────────────

def run_bottom_bar_selection(
    items: list[str],
    display_items: list[str],
    initial_idx: int = 0,
    title: str = "选择",
) -> dict:
    """在底部栏补全弹窗中运行交互式选择，返回选中结果。

    纯标准库实现（termios/tty/os/select），无外部库依赖。
    同时处理 CSI（\\x1b[A/B）和 SS3（\\x1bOA/B）两种箭头序列。

    Args:
        items: 原始选项列表（作为替换文本）。应为纯文本，不含 ANSI 码。
        display_items: 显示文本列表（与 items 一一对应）。建议纯文本。
        initial_idx: 初始光标位置。
        title: 弹窗标题。

    Returns:
        {"action": "confirmed"|"cancel"|"error",
         "index": int | None}
    """
    import os
    import select
    import termios
    import tty
    from ..chat_ui import get_active_chat_ui

    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return {"action": "error", "index": None}

    chat_ui = get_active_chat_ui()
    if chat_ui is None:
        return {"action": "error", "index": None}
    bb = chat_ui._bottom_bar
    if bb is None:
        return {"action": "error", "index": None}

    if not bb._active:
        try:
            bb.setup()
        except Exception:
            return {"action": "error", "index": None}

    bb.show_completions(display_items, initial_idx, texts=items, title=title)

    old_settings = None
    try:
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        termios.tcflush(fd, termios.TCIFLUSH)

        while True:
            try:
                ready, _, _ = select.select([fd], [], [], None)
            except (ValueError, OSError):
                continue
            if not ready:
                continue

            try:
                raw = os.read(fd, 1)
                if not raw:
                    continue
            except (ValueError, OSError):
                continue

            b = raw[0]

            # ── ESC / ANSI 序列 ──
            if b == 0x1b:
                try:
                    has_more, _, _ = select.select([fd], [], [], 0.3)
                    if has_more:
                        nxt = os.read(fd, 1)
                        if nxt == b'[':
                            # CSI: \x1b[A ↑, \x1b[B ↓, \x1b[C →, \x1b[D ←
                            has_term, _, _ = select.select([fd], [], [], 0.1)
                            if has_term:
                                term = os.read(fd, 1)
                                if term == b'A':
                                    bb.cycle_completion(-1)
                                elif term == b'B':
                                    bb.cycle_completion(1)
                            continue
                        elif nxt == b'O':
                            # SS3: \x1bOA ↑, \x1bOB ↓
                            has_term, _, _ = select.select([fd], [], [], 0.1)
                            if has_term:
                                term = os.read(fd, 1)
                                if term == b'A':
                                    bb.cycle_completion(-1)
                                elif term == b'B':
                                    bb.cycle_completion(1)
                            continue
                except (ValueError, OSError):
                    pass
                return {"action": "cancel", "index": None}

            # ── Enter → 确认 ──
            elif b in (0x0d, 0x0a):
                idx = bb._completion_idx
                if 0 <= idx < len(items):
                    return {"action": "confirmed", "index": idx}

    except Exception:
        return {"action": "error", "index": None}
    finally:
        if old_settings is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass
        try:
            bb.hide_completions()
        except Exception:
            pass
        try:
            while select.select([sys.stdin], [], [], 0)[0]:
                sys.stdin.read(1)
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except Exception:
            pass
