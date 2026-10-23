"""InkRenderer — 非全屏（随内容流动）帧差异渲染器。

核心算法（Ink 默认模型，非 DECSTBM）：
  1. 渲染组件树 → 全量 Frame（行列表，每行 = StyledRun 序列）。
  2. 行级 diff：收集 prev 与 new 的**差异区间**（[start, end) 行号，
     连续差异行合并；身份短路——同 Line 对象恒相等）。
  3. 应用：从当前光标定位到各差异区间逐区间重写；仅重写变化的行区间，
     静态/未变行（含 committed 前缀）**永不重写**。高度差（新增/删除行）
     单独处理：增长追加 delta 新行、缩短清除残留行。
  4. 输入光标：写帧前隐藏（``_hide_cursor_for_write``）；帧写完后由会话
     ``_position_cursor`` 先 ``place_cursor`` 定位到输入区、再 ``set_cursor_visible
     (True)`` 显示——无输入区（全屏视图）保持隐藏（2026-10-22 用户需求）。

PERF-4 / 增量细化：
  - 平移快路径：``new_h > prev_h`` 且尾部内容整体下移且相同（仅新增 delta 行
    位于 ``prev_h`` 起始）时，跳过重写相同尾部，仅写新增 delta 行。
  - **差异区间重写**（取代旧的"首差异行→末尾全重写"）：头部动画（如标题栏
    呼吸色时间桶变化）只改动首行时仅重写首行，不再引发整帧重写——大文档下
    每帧输出从 O(文档) 降为 O(变更行)。
  - **底部对齐滚动快路径**（``_scroll_grow``，2026-10-23）：文档底部贴屏幕
    底部时增长，先在屏幕底部写 ``\\n`` 让终端原生滚动 delta 行，再只重写
    差异行 + 位移区——修复前 ``_grow_drifted`` 在漂移/矮文档场景逐行比较，
    映射整体位移导致**整屏重写**（流式输出/输入区换行时闪）。
  - **底栏固定局部滚动**（``_scroll_grow_reserved``，2026-10-23）：会话每帧
    注入文档末尾固定底栏行数（状态栏/输入区），增长时用 DECSTBM 把滚动
    区域限制在内容区——底栏不随滚动上移、无需每帧写回底部，消除「增长时
    底栏上下跳一下」；底栏内容变化的行仍单独重写。底栏高度变化/未知时
    回退整屏滚动（正确性优先）。
  - **底部对齐插入行快路径**（``_insert_shrink``，2026-10-23）：缩短时用
    ``\\033[nL``（IL）让终端原生下移内容、丢弃底部 n 行，只重写插入空行区、
    被丢弃行与内容真正变化的行——修复前 ``_rewrite_drifted`` 同样整屏重写。
  - **同步输出**（DECSET 2026，2026-10-23）：每帧写终端的缓冲用
    ``\\033[?2026h`` / ``\\033[?2026l`` 包裹，支持的终端把整帧（滚动 + 底栏/
    变化行重写）作为一次原子刷新，消除逐字节渲染的中间态闪烁；不支持的
    终端忽略该私有模式（无副作用）。空帧零写入（不发序列）。
  - 单帧重写行数告警阈值 ``_LARGE_REWRITE_WARN_ROWS``：实际待重写行数
    （差异区间行数 + 高度差行数）超过该值时**仅记录限频告警日志**，渲染
    路径仍然一律增量（**不清屏、不重写整篇文档**）。修复前超限会降级为
    ``clear_screen`` + ``_write_full``：大输出帧（工具一次刷数百行）在
    Termux 上表现为整片瞬时重绘/跳动，并把整篇历史重复写进 scrollback；
    而增量路径此时正是「写必须显示的新增行」，重写量本就与输出量同阶，
    清屏全量只会更慢、更闪。

不切换备用屏幕、不用 DECSTBM——内容自然流入 scrollback。

**非 resize 均增量（需求）**：全量写入（``_write_full``）**仅**出现在首帧
（``_prev is None``，无前一帧可 diff）与 ``reset(full=True)``（resize 后
``_render_frame`` 消费 ``_resize_pending`` 置位）；
**其余所有帧一律走行级 diff 增量路径**——包括 Ctrl+L 清屏后、suspend/resume
（交互工具独占终端）后、文档高于屏幕的缩短/增长/等高
（``_rewrite_drifted``/``_grow_drifted`` 物理映射）与缩短/增长/等高**进入屏幕内**
（文档底部对齐可见区底部，负偏移模型 ``_effective_offset`` 供 place_cursor）。
说明：
  1. ``_LARGE_REWRITE_WARN_ROWS`` 超限**只记限频告警**（见上），渲染仍走
     增量路径——不清屏、不整篇重写（清屏全量反而放大闪烁与 scrollback 冗余）；
  2. ``reset(full=False)`` / ``suspend()`` / ``full_clear()`` 使用空帧作为 prev
     （Frame([]), height=0）——与空帧 diff 等价于逐行写入但不触发 clear_screen，
     保持增量路径一致性；
  3. 终端无 delete-line/DECSTBM 语义，缩短后物理缓冲长度保持（清行不删行）——
     渲染器用 ``_buf_h``（物理缓冲行数）精确跟踪漂移：物理行 q 显示新文档行
     q-drift（drift = _buf_h - new_h），自底向上重写可见区变化行 + 清残留
     （不写 ``\n`` 不触发滚动），偏移不漂移错位；``doc_idx < 0``（文档上方空行
     区）清空——进入屏幕内也增量。文档坐标→屏幕坐标用理想偏移
     （``_screen_offset = max(0, doc_h-height)``），place_cursor 用
     ``_effective_offset``（可为负，文档偏下）。
  ★ 无末尾空行模型（2026-08-15）：物理缓冲 = 文档行数（``_buf_h = doc_h``，
    无 doc_h+1 末尾空行）——最后一行内容写完后不写 ``\n``（光标停在文档最后
    一行），满屏/超屏时最后一行内容显示在屏幕最后一行（修复「满屏时模式行
    下方多一行空行」：``doc_h == height`` 时首行不再被滚动挤出）。

Args:
    stream: 输出流（默认 sys.__stdout__；测试传 Mock/StringIO）。
"""

from __future__ import annotations

import io
import logging
import sys
import time
from typing import cast

from src.tui._screen import (
    cursor_up, cursor_down, clear_line, cursor_forward, clear_screen,
    cursor_hide, cursor_show, cursor_goto, insert_lines,
    set_scroll_region, reset_scroll_region,
)
from .output import Frame
from .diff import first_diff_line
# ★ 巨型文件拆分（2026-10-07 架构优化）：内容行发射职责（committed 内容行 →
#   输出历史回调）独立为 Mixin 模块；InkRenderer 经继承组合（行为零变化）。
from ._renderer_content import _RendererContentMixin
# ★ P3（review）：帧 diff 纯函数模块级导入——修复前 ``_diff_runs``/
#   ``_is_tail_shifted``/``_find_tail_anchor`` 三个热路径包装方法每次调用
#   执行函数内 import（渲染每帧多次 sys.modules 查找 + 属性解析）。
from . import _frame_diff as _fd

_logger = logging.getLogger(__name__)

# 行尾清除（防止旧行尾部残留）
_CLEAR_EOL = "\033[K"

# ★ 2026-10-23（满屏/底栏闪烁修复）：同步输出（DECSET 2026）包裹整帧写入——
#   支持的终端把「滚动 + 底栏/变化行重写」作为一次原子刷新，消除逐字节
#   渲染时的中间态（底栏先被滚上去、再写回底部）；不支持的终端忽略该私有
#   模式（无副作用）。协议：``ESC [ ? 2026 h`` 开始、``ESC [ ? 2026 l`` 结束。
_SYNC_BEGIN = "\033[?2026h"
_SYNC_END = "\033[?2026l"

#: 单帧重写行数告警阈值（**只告警，不降级**）。
#:
#: ★ 2026-10-23（流式输出闪烁修复）：修复前超阈值会降级为
#:   ``clear_screen()`` + ``_write_full(frame)``——一次大输出帧（工具
#:   read/bash/grep 一次刷数百行，或模型单 chunk 输出数百行）在 Termux 上
#:   表现为整片瞬时清屏/重绘（用户报障「流式输出时会闪」「内容整体跳动
#:   一行后恢复」），且会把整篇历史重复写进 scrollback（大文档单帧写
#:   700+ 行）。增量路径此时写的是「必须显示的新增行」，量级与输出本身
#:   同阶；清屏全量只会更大更闪。现超阈值仅记录限频告警（每 1s 至多一条），
#:   便于观察极端重写，不影响正确性与渲染路径。
_LARGE_REWRITE_WARN_ROWS = 200

#: 大重写告警限频间隔（秒）——流式期间大输出可能连续多帧超阈值，避免日志刷屏。
_LARGE_REWRITE_WARN_INTERVAL = 1.0

#: committed 内容行在文档中的起始行号（0-based）——App 组件树首元素
#: ``TopHeader`` 恒占文档第 0 行（单行渐变标题栏），committed 聊天历史
#: （``AppModel.committed_lines``）自第 1 行起连续排布。输出历史行回调
#: 只覆盖该区间（不含底部 live 区：状态栏/输入区/解析进度行）。
_CONTENT_LINE_OFFSET = 1

#: 状态自愈告警限频（秒）——持续损坏时每秒最多一条 warning（防日志刷屏）。
_STATE_RECOVERY_WARN_INTERVAL = 5.0
#: 上次状态自愈告警时刻（monotonic；模块级，跨实例共享即可）。
_last_state_recovery_warn_at = 0.0


class RendererStateError(RuntimeError):
    """渲染器不变量被破坏（记账状态不可信，必须重置后全量重绘）。

    与普通 ``RuntimeError`` 区分：只有本类会被 ``InkRenderer.render`` 捕获并
    触发自愈路径；帧构建/行渲染等其它异常仍原样上抛（由会话错误帧兜底），
    避免把无关异常误判为状态损坏。
    """


class InkRenderer(_RendererContentMixin):
    """非全屏帧差异渲染器。

    Args:
        stream: 输出流（默认 sys.__stdout__；测试传 Mock/StringIO）。
        line_callback: 可选回调，接收新增提交行文本（含换行），用于输出历史。
            仅在文档增长（新行）或首帧时回调，避免 live 区重写污染历史。
        height: 终端屏幕高度（行）。>0 时启用屏幕坐标跟踪——文档高于屏幕
            时相对光标移动按屏幕边界钳制、可见区上方的行跳过重写（防
            cursor_up 越出屏幕顶部导致渲染错乱）；0（默认）表示未知/无限
            （测试用），保持文档坐标行为不变。
    """

    def __init__(self, stream=None, line_callback=None, height=None):
        self._stream = stream if stream is not None else sys.__stdout__
        self._line_callback = line_callback
        self._prev: Frame | None = None
        # 当前光标行（1-based；height=0 时=文档坐标，height>0 时=屏幕坐标已钳制）
        self._cursor_row: int = 0
        # 终端屏幕高度（行）；0 = 未知/无限（测试用，文档坐标即屏幕坐标）
        self._height: int = int(height) if height else 0
        # ★ P3（review 2026-08-18）：终端屏幕宽度（列）；0 = 未知（测试用，
        #   place_cursor 列上限不钳制）。set_width 由 session 渲染帧传播
        #   （与 set_height 同模式，宽度变化时同步）。
        self._width: int = 0
        # ★ 2026-10-22（启动期渲染错位修复）：本会话写过的最高物理行
        #   （0-based）。「文档上方空行区」在屏幕上可能保留两类内容：
        #   ① 首帧之前用户终端里已有的历史输出（**不得清除**）；② 本会话
        #   更早帧绘制过的 UI（偏移变化后必须清除，否则残留上一版 banner/
        #   边框）。两者在可见区内无法用 prev 帧区分，故用本字段界定会话
        #   写入范围——清理时只清 ``q >= _painted_top`` 的物理行。首帧全量
        #   写入按 ``max(0, _buf_h - n)`` 初始化（底部锚定时 = 文档起始物理
        #   行），``reset(full=True)`` 清屏路径重置为 0。
        self._painted_top: int = 0
        # ★ 2026-10-23（空闲不写终端）：最近一次 place_cursor 的
        #   ``(target_row, col)``。帧内容未变（``render`` 无写入）且光标目标
        #   相同 → 跳过 ``\r``/cursor 移动 + flush（原实现每帧至少一次 write
        #   + flush，60Hz 空闲下持续产生无效终端 IO）。
        self._placed_cursor: tuple[int, int] | None = None
        # ★ 物理缓冲行数（用户需求「除 resize 外均增量」）：
        #   - ``_write_full``（首帧/reset(full=True) 后） = doc_h（无末尾空行，
        #     最后一行不写 \n）；★ 无末尾空行模型（2026-08-15）；
        #   - 增量增长按实际滚动扩展（``grow_rows = max(0, new_h-_buf_h)``）
        #     增加——``_buf_h`` 精确跟踪终端缓冲（清行不删行，缩短后缓冲保持
        #     旧值，偏移漂移由本字段表达）；
        #   - 增量缩短/等高保持（自底向上重写不写 ``\n``，不触发滚动）；
        #   - reset(full=False)/suspend/full_clear 软重置为 0；空帧置 0。
        #   屏幕坐标换算分工：文档坐标→屏幕坐标用**理想偏移**
        #   （``_screen_offset = max(0, doc_h-height)``，place_cursor 用
        #   ``_effective_offset`` 可为负）；物理偏移（``_buf_h - height``）仅
        #   用于漂移方法（``_rewrite_drifted``/``_grow_drifted``）内部可见区定位。
        self._buf_h: int = 0
        # ★ 顶部对齐状态（补全弹窗闪烁修复）：
        #   - True（默认）：物理行 q 显示 doc 行 q（doc 0 固定在物理行 0）——
        #     文档仍高于屏幕时缩短/等高/增长走「顶部对齐局部重写」：弹窗/尾
        #     部区域变化只重写变化行 + 清/补残留，弹窗上方（历史消息）永不
        #     重写（消除补全弹窗 items 数量变化时的全可见区重写闪烁）。
        #   - False：文档进入屏幕内（doc_h+1 <= height）后切换为「底部对齐」
        #     （物理行 q → doc q-drift，文档底部对齐可见区底部，负偏移模型）
        #     ——保证缩短进入屏幕内时完整文档可见（用户需求既有契约）。
        #   切换时机：``_rewrite_drifted`` 检测 doc 进入屏幕内置 False；
        #   首帧/重置后置 True；``_grow_drifted`` 增长时若 doc 仍高于屏幕保持
        #   True、doc 进入屏幕内转 False。
        self._top_aligned: bool = True
        # ★ 输出历史基线（committed 内容行数）：line_callback 只回调
        #   **新增的已提交内容行**（文档自 ``_CONTENT_LINE_OFFSET`` 行起的
        #   committed 区）——修复前每帧增长都把文档末尾的 live 行（状态栏/
        #   输入区/解析进度行/边框/时间线）当作新增行回调：输出历史被 UI 行
        #   污染且反复重复（同一底部行内容随行号增长被反复写入），真正新增的
        #   内容行反而大多漏记（内容行插入在文档中部，不落在「末尾行区间」）。
        #   本字段 = 上一次回调时的 committed 行数；软重置
        #   （reset(full=False)/suspend/full_clear）不清零——历史已记录的行
        #   不因 TUI 内部重绘重复回调；内容区缩短（清屏/重排）时同步跟随
        #   （新文档行号空间已变，见 ``_emit_content_lines``）。
        self._content_line_count: int = 0
        #: committed 内容区在文档中的起始行号（0-based）——由会话每帧经
        #: ``set_content_line_count(..., start=...)`` **显式注入**（修复前
        #: 硬编码 ``_CONTENT_LINE_OFFSET`` 常量：把「App 树第 0 行恒为
        #: TopHeader」的布局知识写死在渲染器内，App 顶部增删元素即错位）。
        #: 未注入时保持默认常量（独立使用 InkRenderer 的场景）。
        self._content_start: int = _CONTENT_LINE_OFFSET
        #: 本帧 committed 内容行数（会话每帧经 ``set_content_line_count``
        #: 注入；未注入时保持 0 → 不回调，安全）。
        self._frame_content_count: int = 0
        # ★ 光标可见性状态（2026-08-17 用户需求：轨迹 Trace 不显示光标）：
        #   None=未知（终端默认可见；首次 set 显式对齐实际状态）；True/False
        #   = 上次设置值。``set_cursor_visible`` 仅变化时输出 DECTCEM 序列
        #   （每帧 _position_cursor 调用——状态跟踪避免每帧重复写转义码）。
        self._cursor_visible: bool | None = None
        # ★ 2026-10-06（用户需求：启动时按「当前光标行 + 1」锚定）：
        #   终端光标行查询函数（``() -> int | None``，见
        #   ``src.tui._screen.query_cursor_row``）——仅在**首个全量帧**
        #   （会话启动首帧）消费一次；未注入 / 查询失败 / 非 TTY 时回退
        #   「文档底部贴屏幕底部」锚定（保证渲染正确）。
        self._cursor_row_provider = None
        #: 待消费的起始行锚定资格（``set_cursor_row_provider`` 置位，
        #: 首个 ``_write_full`` 消费后清除——resize 全量重写不得复用）。
        self._start_anchor_pending: bool = False
        #: 文档起始屏幕行（1-based）——起始行锚定生效时非 None；文档触及
        #: 屏幕底部（或 resize 全量重写）后置回 None（转底部锚定，物理
        #: 缓冲此后不收缩）。
        self._start_row: int | None = None
        # ★ 2026-10-20（resize 残留修复）：下一次全量写入前先清屏。
        #   ``reset(full=True)``（仅 resize 使用）置位；``_write_full`` 消费。
        #   修复前 resize 全量重写只从「新文档起始行」开始逐行覆盖：
        #   - 终端对旧帧（旧宽度）的 reflow 会让旧内容占更多物理行，
        #   - 新文档比屏幕矮时旧帧上半部分完全不被覆盖，
        #   屏幕上因此残留半份旧 UI / 行内左右两段来自不同帧（resize 后
        #   必现）。清屏（ED 2，不清 scrollback）后从底部锚定重写即干净。
        #   首帧（``_prev is None`` 且未经 reset）不置位——不得清掉用户
        #   终端里已有的历史输出。
        self._clear_before_full: bool = False
        #: 上次「单帧重写行数超告警阈值」告警时刻（monotonic）——流式大输出
        #: 可能连续多帧超阈值，按 ``_LARGE_REWRITE_WARN_INTERVAL`` 限频，
        #: 避免日志刷屏（仅观测用途，不改变渲染路径）。
        self._large_rewrite_warn_at: float = 0.0
        # ★ 2026-10-23（增长闪烁修复）：文档末尾固定底栏（状态栏/输入区）行数。
        #   会话每帧经 ``set_bottom_reserved`` 注入（下屏 fiber 起始行推导）。
        #   增长时若旧/新底栏行数一致，滚动区域限制在内容区（``1..height-
        #   bottom``）——底栏不随滚动上移，无需重写，消除「底栏上下跳」。
        #   0（无底栏/全屏视图/独立渲染器）= 整屏滚动（既有行为）。
        self._bottom_reserved: int = 0
        #: 上一帧的底栏行数（``set_bottom_reserved`` 时递推）——底栏高度变化
        #: 的帧不能使用局部滚动（滚动区域需按旧布局设置）。
        self._bottom_reserved_prev: int = 0

    # ── 启动起始行锚定（用户需求：光标行 + 1） ──────────

    def set_cursor_row_provider(self, provider) -> None:
        """注入终端光标行查询函数（启动锚定「当前光标行 + 1」用）。

        ★ 2026-10-06（用户需求）：provider 签名 ``() -> int | None``——仅在
        **首个全量帧**（会话启动首帧 ``_write_full``）消费一次：返回有效行号
        时文档起始行锚定为「光标行 + 1」（文档从光标下方开始追加，超出屏幕时
        由终端滚动、模型按 ``_buf_h`` 封顶表达贴底）；返回 None / 未注入 /
        查询异常时回退「文档底部贴屏幕底部」的既有锚定。

        Args:
            provider: 查询函数（生产传 ``_screen.query_cursor_row``）；
                None 表示禁用起始行锚定（回退底部锚定）。
        """
        self._cursor_row_provider = provider
        self._start_anchor_pending = provider is not None

    def _resolve_start_row(self, doc_h: int) -> int:
        """确定全量写入的文档起始屏幕行（1-based）。

        首个全量帧消费光标行查询：有效时起始行 = 光标行 + 1（放大到屏幕外
        由调用方钳制定位、滚动由终端承担）；否则回退底部锚定
        ``max(1, height - doc_h + 1)``。
        """
        if self._height <= 0:
            self._start_row = None
            return 1
        if self._start_anchor_pending:
            self._start_anchor_pending = False
            row = None
            provider = self._cursor_row_provider
            if provider is not None:
                try:
                    row = provider()
                except Exception:
                    _logger.debug("光标行查询异常，回退底部锚定", exc_info=True)
                    row = None
            if isinstance(row, int) and not isinstance(row, bool) and row > 0:
                self._start_row = row + 1
                return self._start_row
            self._start_row = None
        if self._start_row is not None:
            return self._start_row
        return max(1, self._height - doc_h + 1)

    def _sync_start_row_anchor(self, doc_h: int) -> None:
        """起始行锚定校准（每帧写入后收口 ``_buf_h``）。

        ★ 2026-10-06：仅当 ``_start_row`` 非 None（启动按「光标行 + 1」锚定）
        时生效——文档底部随内容增长下移、随缩短上移（起始行保持），使
        ``_effective_offset``（= ``doc_h - _buf_h``）恒为 ``1 - _start_row``；
        一旦文档触及屏幕底部，终端滚动不可逆，置回 None 转底部锚定
        （``_buf_h`` 此后不收缩）。
        """
        if self._start_row is None or self._height <= 0:
            return
        bottom = self._start_row + doc_h - 1
        if bottom < self._height:
            self._buf_h = bottom
        else:
            self._start_row = None
            self._buf_h = max(self._height, doc_h)

    # ── 屏幕坐标（height>0 时文档高于屏幕的滚动偏移处理） ──────────

    def set_height(self, height: int) -> None:
        """设置终端屏幕高度（resize 时更新）。

        高度变化后钳制光标行到新屏幕范围内（物理光标不会越出屏幕底部）。
        """
        self._height = int(height) if height else 0
        if self._height > 0:
            self._cursor_row = max(1, min(self._cursor_row, self._height))

    def set_width(self, width: int) -> None:
        """设置终端屏幕宽度（resize 时更新）。

        ★ P3（review 2026-08-18）：为 ``place_cursor`` 列上限提供渲染器内
        防御——修复前列上限（``min(col, width)``）完全依赖调用方钳制
        （``_cursor.position_cursor`` 已钳），未来新增直接调用方遗漏钳制时
        ``cursor_forward`` 输出越列 ANSI（光标停在 wrap 边界，pyte/Termux
        等终端行为不可预期）。宽度 0/未知（缺省）时不钳制，保持既有行为
        （测试用文档坐标模式零回归）。
        """
        self._width = int(width) if width else 0

    def set_bottom_reserved(self, rows: int) -> None:
        """设置文档末尾固定底栏行数（增长时局部滚动的保留区）。

        ★ 2026-10-23（增长闪烁修复）：会话在每帧 ``render`` 前注入
        「状态栏/输入区」占用的末尾行数（由下屏 fiber 起始行推导）。增长时
        若本帧与上一帧底栏行数一致，``_scroll_grow`` 把滚动区域限制在内容区
        （``1..height-bottom``）——终端原生滚动只作用于内容区，底栏原地不动，
        无需每帧重写底栏，消除「底栏被滚上去再写回」的上下跳动。

        0（无底栏/全屏视图/独立渲染器）或底栏高度变化时回退整屏滚动（既有
        行为，正确性不变）。

        Args:
            rows: 底栏占用的文档末尾行数（0 = 无保留区）。
        """
        self._bottom_reserved_prev = self._bottom_reserved
        try:
            self._bottom_reserved = max(0, int(rows or 0))
        except (TypeError, ValueError, OverflowError):
            self._bottom_reserved = 0

    def _screen_offset(self, doc_h: int) -> int:
        """文档高于屏幕时被滚出可见区上方的行数（**可见区起点**偏移，钳制 >= 0）。

        ★ 无末尾空行模型（2026-08-15）：按「理想物理缓冲 = doc_h」推导
        ``max(0, doc_h-height)``——用于渲染路径判断「文档第几行起进入可见
        区」（``vis_start``，避免重写屏幕外不可达行）。
        ★ 2026-10-06：仅保留给可见区起点判断；**文档行 → 屏幕行换算**
        （``_to_screen``/``_bottom_row``）改用 ``_effective_offset``（含物理
        缓冲漂移、可为负）——底部对齐且文档矮于屏幕时文档实际偏下
        ``height - doc_h`` 行，钳制偏移会把写行/光标目标定位到偏上位置。

        Args:
            doc_h: 文档总行数。

        Returns:
            可见区顶部对应的文档行偏移；height=0 时恒 0（无约束）。
        """
        if self._height <= 0:
            return 0
        if self._top_aligned and self._buf_h > 0:
            # 顶部对齐：可见区顶部 = 物理缓冲顶部（含 scrollback/残留）——
            # doc 0 固定在物理行 0，物理行 q 显示 doc 行 q。
            return max(0, self._buf_h - self._height)
        # 未渲染（buf_h=0，单元测试直接调用）或底部对齐：理想偏移推导。
        # ★ 无末尾空行模型（2026-08-15）：文档最后一行即物理缓冲末尾（无
        #   doc_h+1 末尾空行），可见区顶部 = max(0, doc_h - height)。
        return max(0, doc_h - self._height)

    def _effective_offset(self, doc_h: int) -> int:
        """文档行（1-based）→ 屏幕行的减法偏移（含物理缓冲漂移，可为负）。

        与 ``_screen_offset``（渲染路径钳制用 max(0,...)）不同，本方法返回
        **通用物理位置偏移**——文档行 ``row`` 的物理屏幕行 = ``row - offset``：
        - 无漂移（``_buf_h == doc_h``，无末尾空行模型）：文档屏幕内从顶部
          （offset 0）、高于屏幕按 doc_h-height；
        - 漂移 + 文档高于屏幕：offset = doc_h-height（>0，底部对齐）；
        - 漂移 + 文档屏幕内（缩短/增长进入屏幕内）：offset 为负——文档显示在
          可见区底部（物理缓冲无法收缩，偏移模型用负偏移表达文档偏下）。
        供 ``place_cursor`` 使用（每帧渲染后输入光标定位必须与物理位置一致）；
        ★ 2026-10-06：「命令行在底部时启动渲染错乱」修复后 ``_to_screen`` /
        ``_bottom_row`` 的文档行 → 屏幕行换算同样经本方法（三者口径统一）。

        Args:
            doc_h: 文档总行数。

        Returns:
            文档行 → 屏幕行的减法偏移（可为负）。
        """
        if self._height <= 0:
            return 0
        if self._top_aligned and self._buf_h > 0:
            # 顶部对齐：doc 行 row 在物理行 row，屏幕行 = row - (buf_h-height)
            # （物理缓冲高于屏幕时）。物理缓冲在屏幕内时 offset=0（doc 顶部
            # 对齐物理缓冲顶部，全部可见）。
            return max(0, self._buf_h - self._height)
        if self._buf_h > self._height:
            # 物理缓冲高于屏幕：可见区 = 缓冲底部，文档底部对齐缓冲末尾
            # ★ 无末尾空行模型（2026-08-15）：文档行号即物理行号（无
            #   doc_h+1 末尾空行），文档底部 = doc_h。
            return doc_h - self._height
        # 物理缓冲在屏幕内：文档底部对齐物理缓冲末尾
        # ★ 无末尾空行模型（2026-08-15）：文档行号即物理行号。
        return doc_h - self._buf_h

    def _to_screen(self, buffer_row: int, doc_h: int) -> int:
        """将文档 1-based 行号转为屏幕 1-based 行号（未钳制）。

        ★ 2026-10-06（「命令行在底部时启动渲染错乱」修复）：偏移改用
        ``_effective_offset``（含物理缓冲漂移，可为负）——修复前用
        ``_screen_offset``（钳制 >=0，隐含「文档顶部 = 屏幕第 1 行」）：文档
        矮于屏幕且底部对齐（启动时光标贴底、首帧锚定屏幕底部后即此状态）时
        文档实际偏下 ``height - doc_h`` 行，按 0 偏移换算会把写行/光标目标
        定位到偏上位置 → 相对定位逐帧错位（重复行/内容堆叠）。返回仍可能
        <1（可见区上方）或 >height（下方），调用方据此判断可达性；height=0
        时恒等返回（文档坐标即屏幕坐标）。

        Args:
            buffer_row: 文档 1-based 行号。
            doc_h: 文档总行数。

        Returns:
            屏幕 1-based 行号（未钳制）。
        """
        return buffer_row - self._effective_offset(doc_h)

    def _clamp(self, row: int) -> int:
        """将屏幕行号钳制到 [1, height]（height=0 时原样返回）。"""
        if self._height <= 0:
            return row
        return max(1, min(row, self._height))

    def _bottom_row(self, doc_h: int) -> int:
        """文档写入后物理光标所在屏幕行（1-based，已钳制）。

        文档底部（doc_h，1-based）物理屏幕位置经 ``_to_screen`` 推导
        （= ``doc_h - _effective_offset(doc_h)``）——顶部对齐时物理缓冲 =
        doc_h（缓冲末尾）；底部对齐且文档矮于屏幕时文档整体偏移到可见区
        底部（``height``）。★ 无末尾空行模型（2026-08-15）：文档最后一行
        即缓冲末尾（无 doc_h+1 末尾空行），底部 = doc_h 而非 doc_h+1。
        """
        return self._clamp(self._to_screen(doc_h, doc_h))

    def _advance_row(self, row: int) -> int:
        """写一行并 ``\n`` 后光标行的推进（屏幕底部钳制）。

        光标已在屏幕底部时 ``\n`` 触发上滚、光标停底不再下移（height=0
        时恒 +1，无约束）。
        """
        if self._height > 0 and row >= self._height:
            return self._height
        return row + 1

    def _flush_frame_buf(self, buf) -> None:
        """把整帧缓冲作为**一次同步输出**写终端（DECSET 2026）。

        ★ 2026-10-23（满屏/底栏闪烁修复）：底部对齐滚动/插入行时，一帧内会
        「滚动内容 + 重写底栏/变化行」。终端逐字节渲染会把中间态显示出来
        （底栏先被滚上去、再写回底部），表现为闪。``\\033[?2026h`` /
        ``\\033[?2026l`` 让支持的终端把整帧缓冲作为一次原子刷新；不支持的
        终端忽略该私有模式，行为与修复前一致（安全回退）。

        空缓冲直接返回（空闲帧零 IO）。写入内容本身不变。

        Args:
            buf: 整帧输出缓冲（``io.StringIO``）。
        """
        data = buf.getvalue()
        if not data:
            return
        try:
            self._stream.write(_SYNC_BEGIN)
            self._stream.write(data)
        finally:
            # 结束序列必须在任何情况下发出（否则支持的终端会一直挂起刷新）。
            self._stream.write(_SYNC_END)
            self._stream.flush()

    def _line_needs_clear(self, frame: Frame, idx: int) -> bool:
        """重写该行前是否需要 ``EL`` 清行（满宽行免清）。

        ★ 2026-10-23（满屏重写闪烁修复）：满宽行（行内容宽度 >= 终端列宽，
        如状态栏/输入区分隔线、边框、背景行）直接覆盖整行即可——修复前每帧
        重写底栏都先 ``\\033[K`` 清空再写内容，终端上表现为底栏闪一下。只有
        行宽不足（可能残留旧行尾部）时才必须清行。

        终端宽度未知（``_width <= 0``，测试/头部无尺寸）时保守清行；行宽
        获取异常时同样保守清行（正确性优先）。

        Args:
            frame: 当前帧。
            idx: 行号（0-based）。

        Returns:
            True — 需要写 ``EL``；False — 满宽行直接覆盖。
        """
        width = self._width
        if width <= 0:
            return True
        try:
            return frame.lines[idx].width < width
        except Exception:
            return True

    def _write_row(self, buf, frame, idx: int, last_idx: int) -> bool:
        """写一行（\r 归位 + EL 清行 + 内容 + \r 归位）；非段末行写 \n 换行。

        **当前语义（必须遵守）**：
          - 行写入序列恒为 ``\r`` + ``\033[K``（清行）+ 行内容 + ``\r``；
            ★ 2026-10-23：满宽行省略 EL（直接覆盖整行，避免底栏每帧「清空→
            重写」闪烁，见 ``_line_needs_clear``）；
          - ``last_idx`` 为**段末行号**（该行不写 \n，光标停在行尾，由调用方
            后续 cursor 定位衔接）——runs/位移区段末为 ``new_h-1``（文档
            最后一行，无末尾空行模型）；head_runs 段末为 ``end-1``（重写
            旧行不驱动滚动——2026-08-18 修复）；
          - **返回值必须被消费**：返回 True（写了 \n）时调用方**必须**
            ``current_row = self._advance_row(...)`` 推进光标——漏处理会
            导致渲染器光标状态与终端脱节（滚动计数错位根因同类）。

        ★ 2026-08-18（公共写行辅助重构·历史背景）：四处写行循环（delta==0
        差异区间 / delta!=0 head_runs / 位移区 / ``_grow_drifted`` 追加循环）
        行写入语义统一收敛——修复 head_runs 段末行 \n（user_select 连续弹出
        显示错乱根因）后各处段末条件不一致（``new_h-1`` vs ``end-1``），提取
        公共函数防「修一处漏四处」。

        Args:
            buf: 输出缓冲（io.StringIO）。
            frame: 新帧（``render_line(idx)`` 渲染行内容）。
            idx: 待写行号（0-based）。
            last_idx: 段末行号（不写 \n 的行，见「当前语义」）。

        Returns:
            True 写了 \n（调用方须推进 current_row）；False 未写（段末行）。
        """
        buf.write("\r")
        # ★ 历史修复背景（2026-08-06 行尾宽字符）：EL 0（\033[K）从内容后移
        #   到内容前——修复前写满宽行（内容恰好 = 终端列宽）后光标停在 wrap
        #   边界（x==width），此时 EL 0 在 Termux 等终端会清除行尾刚写入的
        #   宽字符（CJK/emoji 占 2 列，第二列处于行尾）→ 行尾中文字符显示
        #   不出来。EL 前移：先清整行旧残留再写新内容，行尾宽字符不被清除。
        #   ★ 2026-10-23：满宽行无需 EL（整行被新内容覆盖），见上。
        if self._line_needs_clear(frame, idx):
            buf.write(_CLEAR_EOL)
        buf.write(frame.render_line(idx))
        # ★ 历史修复背景（2026-08-05 满宽行 wrap）：行内容恰好填满终端宽度时
        #   光标停在 wrap 边界（x==width），直接 ``\n`` 在 pyte/Termux 等终端
        #   会先触发 wraparound 再 LF → 光标额外下移 1 行 → 后续光标定位逐次
        #   偏移。写行前 ``\r`` 归位（清除 wrap 待触发态），``\n`` 只下移 1 行。
        #   ★ 历史修复背景（2026-08-15 无末尾空行模型）：文档最后一行
        #   （idx == new_h-1）不写 \n；★ 2026-08-18 扩展：head_runs 段末行
        #   （非文档末行）同样不写 \n——重写旧行不驱动滚动（滚动只应由位移区
        #   新增行承担，次数 = delta）。
        buf.write("\r")
        if idx < last_idx:
            buf.write("\n")
            return True
        return False

    # ── 渲染 ─────────────────────────────────────────

    def _assert_renderer_invariants(self) -> None:
        """运行期不变量校验：物理缓冲状态三元组一致性。

        ★ 用显式 ``raise`` 而非 ``assert``：``assert`` 在 ``python -O`` 下被
        剥离，而这些不变量是帧缓冲写入的正确性根基（破坏后会造成屏幕错位/
        残留），必须真实生效。常规运行每帧 O(1) 布尔比较，开销可忽略。

        不变量：
          - ``_buf_h >= 0``（物理缓冲行数非负）；
          - ``_top_aligned == False``（底部对齐）仅在**已渲染**状态合法——
            ``_prev`` 非 None 且 ``_buf_h > 0``（底部对齐仅由
            ``_grow_drifted``/``_rewrite_drifted`` 渲染路径置位，二者均以
            ``_prev = frame`` 收尾；首帧/软重置/全量写入恒置 True）；
          - ``_prev is None`` ⇔ 全量写入待触发（首帧 / reset(full=True)），
            此时 ``_buf_h == 0`` 且 ``_top_aligned == True``；
          - 起始行锚定（``_start_row`` 非 None）仅在已知屏幕高度时有效，与
            顶部对齐互斥（锚定由 ``_write_full`` 的底部对齐路径置位），
            起始行与缓冲均不越屏幕高度（越界由 ``_sync_start_row_anchor``
            解除锚定）。
        """
        if self._buf_h < 0:
            raise RendererStateError(f"_buf_h 非负不变量被破坏: {self._buf_h}")
        if not self._top_aligned and not (self._prev is not None and self._buf_h > 0):
            raise RendererStateError(
                "底部对齐（_top_aligned=False）仅在已渲染状态合法: "
                f"prev={self._prev is not None} buf_h={self._buf_h}"
            )
        if self._prev is None and not (self._buf_h == 0 and self._top_aligned):
            raise RendererStateError(
                "未渲染状态（_prev is None）应满足 buf_h==0 且 top_aligned: "
                f"buf_h={self._buf_h} top_aligned={self._top_aligned}"
            )
        if self._start_row is not None:
            if self._height <= 0:
                raise RendererStateError("_start_row 锚定仅在已知屏幕高度时有效")
            if self._top_aligned:
                raise RendererStateError("_start_row 锚定与顶部对齐互斥")
            if not 1 <= self._start_row <= self._height:
                raise RendererStateError(
                    f"_start_row 越界: {self._start_row}（屏幕高度 {self._height}）"
                )
            if self._buf_h > self._height:
                raise RendererStateError(
                    f"锚定期间物理缓冲不应越过屏幕高度: buf_h={self._buf_h} "
                    f"height={self._height}"
                )

    def _recover_corrupted_state(self) -> None:
        """不变量损坏后的**纯内存**重置（不做 I/O）。

        ★ 2026-10-23（渲染故障自愈）：记账状态已不可信（``_prev`` / ``_buf_h``
        / ``_top_aligned`` 相互矛盾）时，任何增量写入都只会把错误写进终端。
        重置为「未渲染」状态并置 ``_clear_before_full``——下一次 ``_write_full``
        先清可见区（ED2，不清 scrollback）再全量重绘，从任意记账损坏中恢复。

        ``_content_line_count`` / ``_frame_content_count`` **故意保留**：输出
        历史已记录的行不能回退，否则归零后下一帧会把全部 committed 行当作
        新增行**重复写入历史**。
        """
        self._prev = None
        self._cursor_row = 0
        self._buf_h = 0
        self._top_aligned = True
        self._start_row = None
        self._start_anchor_pending = False
        self._painted_top = 0
        self._placed_cursor = None
        self._clear_before_full = True

    def mark_corrupted(self) -> None:
        """标记渲染器状态不可信（外部错误帧路径调用）。

        仅重置内存记账、不写终端；下一次成功帧走全量重绘。会话在
        ``_render_failure_frame`` 中调用（错误帧本身经 ``emit_error_line``
        旁路直写，不进入帧/光标模型）。
        """
        self._recover_corrupted_state()

    def emit_error_line(self, text: str) -> None:
        """旁路直写一行错误提示（**不经过帧/光标记账**）。

        渲染帧构建异常时，终端内容与渲染器记账已经不一致——再调用
        ``render()`` 写错误帧会进一步破坏 ``_prev``/``_buf_h``（修复前实现
        正是如此：错误帧把整篇文档替换成 1 行，恢复时依赖漂移路径重新对齐）。
        这里只在当前光标行覆盖写一行提示，不改任何帧状态；随后由
        ``mark_corrupted`` 让下一次成功帧全量重绘。
        """
        if not text:
            return
        try:
            self._stream.write("\r" + _CLEAR_EOL + text)
            self._stream.flush()
        except (OSError, ValueError):
            _logger.debug("错误提示旁路写入失败", exc_info=True)

    def _handle_state_error(self, exc: RendererStateError) -> None:
        """状态不变量错误：限频告警 + 重置（供 ``render`` 自愈路径调用）。"""
        global _last_state_recovery_warn_at
        now = time.monotonic()
        if now - _last_state_recovery_warn_at >= _STATE_RECOVERY_WARN_INTERVAL:
            _last_state_recovery_warn_at = now
            _logger.warning(
                "渲染器状态不变量被破坏，已重置并全量重绘: %s", exc,
            )
        self._recover_corrupted_state()

    def render(self, frame: Frame) -> None:
        """渲染新帧（最小差异写入）。

        输出历史（``line_callback``）统一在帧写入后由 ``_emit_content_lines``
        回调——只回调**新增的已提交内容行**（committed 区），与「文档末尾
        行区间」无关：修复前各渲染分支用 ``[prev_h, new_h)`` 推断新增行，
        把文档末尾的 live 行（状态栏/输入区/时间线/边框）当作新增内容反复
        写入输出历史，且真正新增的内容行（插入在文档中部）大量漏记。

        ★ 2026-10-23（渲染故障自愈）：不变量校验失败（``RendererStateError``）
        时重置记账并用**同一帧重试一次**（重置后走全量写入）；单帧最多自愈
        一次，二次失败原样上抛，由会话错误帧旁路兜底。
        """
        prev_before = self._prev
        try:
            try:
                self._render_frame_impl(frame)
            except RendererStateError as exc:
                self._handle_state_error(exc)
                self._render_frame_impl(frame)
        finally:
            self._sync_start_row_anchor(frame.height)
            self._emit_content_lines(frame)
            # ★ 2026-10-23（空闲不写终端）：本帧有实际写入（_prev 被替换为新
            #   帧对象）时失效 place_cursor 缓存；无变化帧（_prev 保持原对象）
            #   保留缓存，使下一次 place_cursor 可跳过重复写入。
            if self._prev is not prev_before:
                self._placed_cursor = None

    def _render_frame_impl(self, frame: Frame) -> None:
        """渲染实现（组件树 diff 写入终端；输出历史回调由 ``render`` 统一收口）。

        ★ 2026-10-22（用户需求：渲染期间不显示光标）：**所有实际写终端的
        分支**在写入前经 ``_hide_cursor_for_write`` 隐藏光标；帧内容完全一致
        （``first_diff_line < 0``，本方法不写任何字节）时不碰显隐状态，避免
        空闲帧每帧写 DECTCEM 造成光标抖动。帧写完后由会话
        ``_position_cursor`` 决定是否显示（有输入区才定位并显示）。
        """
        self._assert_renderer_invariants()
        if self._prev is None:
            # 首帧 / resize 全量重写：写前隐藏（清屏 + 逐行写入期间光标不可见）
            self._hide_cursor_for_write()
            self._write_full(frame)
            self._prev = frame
            self._stream.flush()
            return

        prev_h = self._prev.height
        new_h = frame.height
        i = first_diff_line(self._prev, frame)
        if i < 0:
            # 帧完全一致：仅刷新光标位置（调用方自行 place_cursor）
            return

        # 本帧将写终端（平移/增长/漂移重写/差异区间重写）：
        # 写入期间大量 cursor_up/down/forward + 重写，先隐藏光标避免跳动闪烁。
        self._hide_cursor_for_write()

        delta = new_h - prev_h

        # ★ 2026-10-23（整屏重写闪烁修复 / 满屏滚动中间态修复）：
        #   底部对齐且文档底部贴屏幕底部时，**所有增长**（含无漂移满屏）统一走
        #   「先滚动 delta 行，再重写差异行/位移区」：
        #   - 修复前漂移/矮文档走 `_grow_drifted`：新旧物理映射整体位移，逐行
        #     比较使**每一行都判定为变化**（屏幕 30 行 → 每帧重写 30 行），
        #     流式输出/输入区换行时整屏闪一下；
        #   - 无漂移满屏走下方 `_apply_frame_writes`：滚动由位移区写行时的
        #     ``\\n`` 触发——终端逐字节渲染时会先看到位移区行写到「旧位置」、
        #     随后再被整体滚上去（中间态）；满屏流式输出时表现为「闪」。
        #   先滚动再写：滚动只发生一次，随后写入的行直接落在最终位置。
        if delta > 0 and self._height > 0 and self._scroll_grow_ok(prev_h):
            self._scroll_grow(frame, prev_h, new_h, i)
            return

        # ★ 有漂移增长（缩短后物理缓冲漂移 `_buf_h > prev_h`）：offset 不再随
        #   doc_h 变化，平移快路径/head_runs/位移区均假设「物理行号 = 文档行号」
        #   （无漂移）会写错偏移移动行 → 走 `_grow_drifted`（物理映射重写 +
        #   追加新行）。须放在平移快路径之前（有漂移时纯追加也会偏移错位）。
        #   增长进入屏幕内（new_h <= height）同样走 `_grow_drifted`（可见区
        #   顶部 doc_idx<0 空行区清空）——不重建、不清屏。
        #   ★ 无末尾空行模型（2026-08-15）：无漂移时 ``_buf_h == prev_h``
        #   （物理缓冲 = 文档行数），判定条件从 ``_buf_h > prev_h+1`` 收紧
        #   为 ``_buf_h > prev_h``。
        #   ★ 仅当滚动快路径不可用（起始行锚定/顶部对齐/底部未贴底）时到达。
        if (
            delta > 0
            and self._height > 0
            and self._buf_h > prev_h
        ):
            self._grow_drifted(frame, prev_h, new_h)
            return

        # ★ PERF-4 平移快路径：仅新增 delta 行导致尾部整体下移且内容相同
        #   （delta 新行位于 prev_h 起始，尾部相同内容跳过重写）。
        #   安全条件：i >= prev_h（首差异恰在文档末尾，无需要平移的尾部）——
        #   中间插入场景（i < prev_h）尾部必须重写（终端无 insert-line 语义），
        #   走下方常规路径。
        #   方向1 步骤3（越底守卫）：追加 ``self._cursor_row >= prev 文档底部
        #   屏幕行``——place_cursor 已把光标移到输入行下方（文档底部之上）时
        #   从下方写 delta 新行会越过屏幕底部触发滚动 → 放弃平移快路径走常规
        #   差异路径（安全侧，无正确性损失；罕见场景，性能可接受）。
        if (
            new_h > prev_h
            and i >= prev_h
            and self._cursor_row >= self._bottom_row(prev_h)
            and self._is_tail_shifted(self._prev, frame, i, delta)
        ):
            buf = io.StringIO()  # ★ 整帧缓冲（方向1）：多段输出先合并再单次 write+flush
            # 定位到 prev 文档最后一行（模式行）的屏幕行——无末尾空行模型下
            # 新行写在模式行下方：先 \n 换行/滚动（屏幕底部时滚动挤出旧行）
            # 再写新行内容。
            current_row = self._cursor_row
            target_row = self._clamp(self._to_screen(prev_h, prev_h))
            if current_row > target_row:
                buf.write(cursor_up(current_row - target_row))
            elif current_row < target_row:
                buf.write(cursor_down(target_row - current_row))
            current_row = target_row
            for line_idx in range(prev_h, new_h):
                # ★ 无末尾空行模型（2026-08-15）：每个新行先 \r\n（换行/滚动）
                #   再写内容；空帧（prev_h==0）后首行原地写（line_idx==0 时
                #   不换行——终端清屏后光标已在行 1）。
                if line_idx > 0:
                    buf.write("\r")
                    buf.write("\n")
                    current_row = self._advance_row(current_row)
                buf.write("\r")
                # ★ 行尾宽字符修复（2026-08-06）：EL 0（\033[K）从内容后移到
                #   内容前——修复前写满宽行（内容恰好 = 终端列宽）后光标停在
                #   wrap 边界（x==width），此时 EL 0 在 Termux 等终端会清除
                #   行尾刚写入的宽字符（CJK/emoji 占 2 列，第二列处于行尾）→
                #   行尾中文字符显示不出来。EL 前移：先清整行旧残留再写新内容，
                #   行尾宽字符不再被清除（满宽行无残留，清行无副作用）。
                #   ★ 2026-10-23：满宽行直接覆盖，省略 EL（防闪烁）。
                if self._line_needs_clear(frame, line_idx):
                    buf.write(_CLEAR_EOL)
                buf.write(frame.render_line(line_idx))
                # ★ 满宽行 wrap 修复（同 _diff_runs 写行循环）：行内容填满宽度时
                #   \r 归位避免 \n 触发 wraparound 额外下移。最后一行不写 \n
                #   （无末尾空行模型：光标停在文档最后一行）。
                buf.write("\r")
            # ★ 无末尾空行模型（2026-08-15）：物理缓冲 = 文档行数（漂移时
            #   保持 max——物理行不删行）；height=0 无约束同语义。
            self._buf_h = max(self._buf_h, new_h)
            self._cursor_row = self._bottom_row(new_h)
            self._prev = frame
            self._flush_frame_buf(buf)
            return

        # ★ 有效重写起点（delta!=0 时为首差异行钳到可见区边界——离屏部分不可达
        #   跳过；delta==0 时用首差异行）。
        # ★ 方向4 优化（delta!=0 头部动画不重写 committed）：delta!=0 时用
        #   「头部差异区间 + 位移区」替代「从有效起点连续重写整个可见区」——
        #   - 头部差异区间：共有行中内容不同的行（[start, end) 且 end <= 位移
        #     锚点），逐区间重写（与 delta==0 相同语义）；
        #   - 位移区：从位移锚点（_find_tail_anchor）连续重写到新帧末尾——
        #     新增/删除行 + 尾部整体位移行（终端无 insert/delete-line，位移
        #     行必须重写，由末尾换行驱动滚动）。
        #   头部动画（标题栏呼吸色 0.1s 桶变化）不再引发 committed 历史可见区
        #   全量重写：流式增长期间每帧重写范围从 O(可见区) 降为 O(头部差异 +
        #   位移区)。
        if delta != 0 and self._height > 0:
            vis_start = self._screen_offset(prev_h)
            anchor = self._find_tail_anchor(self._prev, frame, delta)
            # 头部差异区间（锚点之前；钳到可见区边界）
            head_runs: list[tuple[int, int]] = []
            for rs, re_ in self._diff_runs(
                self._prev, frame, min(prev_h, new_h), start=i,
            ):
                if rs >= anchor:
                    break  # 锚点之后的差异由位移区覆盖
                rs = max(rs, vis_start)
                re_ = min(re_, anchor)
                if re_ > rs:
                    head_runs.append((rs, re_))
            shift_start = max(anchor, vis_start)
        else:
            head_runs = []
            vis_start = 0
            shift_start = i

        # ★ 差异区间收集（方向3 性能）：仅 delta==0（等高帧）需要差异区间——
        #   delta!=0（流式增长/缩短帧）走下方连续重写路径，区间收集纯浪费
        #   （旧实现每帧对共有行全量 O(n) 扫描，流式期间 delta 恒 !=0）。
        #   且 delta==0 时首差异行 i 之前无差异（first_diff_line 定义），
        #   从 i 起扫（免扫描不变的 committed 前缀）。
        #   与 first_diff_line 相同比较语义（身份短路 + runs 值相等）；高度差
        #   边界由下方 delta 分支单独处理。
        if delta == 0 and self._height > 0 and self._buf_h > prev_h:
            # ★ 漂移等高（缩短后物理缓冲漂移）：差异区间按「文档行号 = 物理
            #   行号」定位会漏写偏移移动行（物理行 q 应显示 new 行 q-drift）→
            #   走物理映射重写（与缩短同逻辑，仅内容变化行重写）；等高进入
            #   屏幕内（new_h <= height）同样增量（可见区顶部 doc_idx<0 空行
            #   区清空）——不重建、不清屏。
            self._rewrite_drifted(frame, prev_h, new_h, first_diff=i)
            return

        if delta == 0:
            runs = self._diff_runs(self._prev, frame, min(prev_h, new_h), start=i)
            # ★ 单帧重写行数统计（仅用于超阈值告警，不改变渲染路径）。
            #   行数 = 实际待重写行数（delta==0 时为差异区间行数）。修复前用
            #   ``new_h - i`` 高估——头部动画场景 i=0 但仅首行差异会误报。
            rewrite_count = sum(end - start for start, end in runs)
        else:
            runs = []
            # ★ 单帧重写行数统计（delta!=0，仅用于超阈值告警）：头部差异区间
            #   + 位移区 + 高度变化（delta<0 时清除残留行）。
            rewrite_count = (
                sum(end - start for start, end in head_runs)
                + max(0, new_h - min(shift_start, new_h))
                + max(0, prev_h - new_h)
            )
        if rewrite_count > _LARGE_REWRITE_WARN_ROWS:
            # ★ 2026-10-23（流式输出闪烁修复）：**只告警，不降级**。
            #   修复前这里 clear_screen + _write_full（整篇文档重写）：
            #   - 大输出帧（工具一次刷数百行）在 Termux 上整片瞬时重绘，
            #     用户看到「闪/整体跳动一下」；
            #   - 整篇历史被重复写进 scrollback（文档 700 行时单帧写 700 行），
            #     而增量路径只写「需要显示的新增/变化行」，量级与输出同阶，
            #     清屏全量严格更慢。
            #   保留限频告警便于观察极端重写；渲染继续走下方增量路径。
            now = time.monotonic()
            last = self._large_rewrite_warn_at
            if now - last >= _LARGE_REWRITE_WARN_INTERVAL:
                self._large_rewrite_warn_at = now
                _logger.warning(
                    "单帧重写行数 %d 超过告警阈值 %d（继续增量渲染，不清屏）",
                    rewrite_count, _LARGE_REWRITE_WARN_ROWS,
                )

        # ★ 渲染策略按高度差分流：
        #   - 等高（delta==0）：逐差异区间重写（增量渲染细化）——头部动画
        #     （首行呼吸色变化）只改首行时不再引发整帧重写；committed 静态行
        #     身份短路（同 Line 对象）自动跳过。部分可见区间起点钳到可见区边界
        #     （离屏部分不可达跳过，只重写可见部分——user_select 弹窗高亮跨
        #     屏幕边界导航不再残留）。
        #   - 高度变化（delta!=0）：终端无 insert/delete-line，须从有效起点
        #     （首差异行钳到可见区边界）连续重写到新帧末尾，由末尾换行驱动滚动
        #     （增长）——逐区间无法表达位移行与新增行的塌缩重叠（user_select
        #     弹窗说明列高度变化、流式中间插入）。
        #   - 缩短 + 屏幕约束（delta<0 且 height>0 且文档高于屏幕）：
        #     **增量缩短**（新文档仍高于屏幕或进入屏幕内）——自底向上重写
        #     可见区变化行 + 清残留（不写 ``\n`` 不触发滚动，物理缓冲
        #     ``_buf_h`` 保持，偏移漂移由本字段精确跟踪；进入屏幕内时可见区
        #     顶部 doc_idx<0 空行区清空，文档底部对齐物理缓冲末尾），
        #     **不清屏重建**（用户需求「除 resize 外均增量」：全量 clear+重建
        #     已全部消除）。输入光标经 ``_effective_offset``（可为负）定位到
        #     文档物理位置。
        #   重写目标一律按 **prev 帧偏移** 换算：终端缓冲此刻仍处于 prev 布局
        #   （只能经底部写行触发滚动），按 prev 位置原位重写、由末尾换行滚动
        #   过渡到 new 布局——按 new 偏移写会覆盖未滚动区域。
        #   raw 终端模式下 \n 不归位列 1，每行须前缀 \r。
        # 方向3（缩短闪烁优化）：文档在屏幕内（``_screen_offset==0``，无滚动
        # 偏移）且当前**顶部对齐**（``_top_aligned=True``，无漂移或漂移残留
        # 位于物理缓冲末尾）时缩短走常规 diff 路径（重写 + 清残留）——删字/
        # 关闭补全弹窗不再全屏 clear 闪烁。文档高于屏幕时原全量 clear+重建
        # （防偏移漂移）已替换为增量缩短——不清屏、仅重写可见区变化行。
        # ★ BUG-66（review 方向，渲染错乱）：**底部对齐**（``_top_aligned==
        # False``，文档进入屏幕内后的负偏移模型）时物理行 q 显示 doc 行
        # ``q-drift``——常规 diff 路径假设「物理行 q = doc 行 q」会按错误位置
        # 重写（文档内容写到物理行 1-4，实际应显示在 2-6 → 内容行丢失/错位，
        # 如模糊测试 4→5→4 行序列中 frame6 'c0/x0/c2' 与残留行混叠）。修复：
        # 底部对齐的缩短无条件走 ``_rewrite_drifted``（物理映射路径）。
        if (
            delta < 0
            and self._height > 0
            and (not self._top_aligned or self._screen_offset(prev_h) > 0)
        ):
            # ★ 2026-10-23（整屏重写闪烁修复）：底部对齐且文档底部贴屏幕底部时，
            #   缩短改走「IL 插入 |delta| 空行 + 只重写差异行」。修复前
            #   ``_rewrite_drifted`` 按物理映射逐行比较：缩短使文档整体下移，
            #   新旧映射每行都错位 → **整屏重写**（屏幕 30 行 → 30 次写行），
            #   输入区退格/关闭弹窗/流式解析行消失时整屏闪一下。
            #   IL 由终端原生下移内容（上方历史不动）、丢弃底部 |delta| 行，
            #   未变化行零重写；仅在插入点/被丢弃行/内容变化行上写。
            #   不满足条件（顶部对齐/起始行锚定/文档底部未贴底）时回退原路径。
            if self._insert_shrink_ok(prev_h):
                self._insert_shrink(frame, prev_h, new_h)
                self._prev = frame
                return
            # 增量缩短（文档仍高于屏幕或进入屏幕内）：重写可见区变化行 +
            # 清残留/清空行区，不清屏重建（物理缓冲 _buf_h 保持）。
            self._rewrite_drifted(frame, prev_h, new_h, first_diff=i)
            self._prev = frame
            return

        self._apply_frame_writes(
            frame, prev_h, new_h, delta, runs, head_runs, shift_start,
        )

    def _apply_frame_writes(
        self, frame, prev_h, new_h, delta, runs, head_runs, shift_start,
    ) -> None:
        """帧写入（从 ``_render_frame_impl`` 提取——巨型方法拆分，行为逐字不变）。

        职责：
          - ``delta == 0``：逐差异区间重写（增量，头部动画只改首行时仅写首行）；
          - ``delta != 0``：头部差异区间逐区间重写 + 位移区连续重写 +
            缩短清残留行 + 增长扩张物理缓冲 ``_buf_h``；
          - 收尾：光标移回文档底部（供 ``place_cursor`` 相对移动）、提交
            ``_prev`` 并把整帧缓冲单次写入 + flush。

        重写目标一律按 **prev 帧偏移** 换算——终端缓冲此刻仍是 prev 布局，
        滚动只由底部写行的换行驱动（增量缩短自底向上重写，不写 ``\n``）。

        Args:
            frame: 新帧。
            prev_h: 旧帧高度。
            new_h: 新帧高度。
            delta: 高度差（``new_h - prev_h``）。
            runs: ``delta == 0`` 的差异区间列表（[start, end) 升序不重叠）。
            head_runs: ``delta != 0`` 的头部差异区间（锚点之前，钳到可见区）。
            shift_start: 位移区起始行（锚点钳到可见区）。
        """
        buf = io.StringIO()  # ★ 整帧缓冲（方向1）：多段输出先合并再单次 write+flush
        current_row = self._cursor_row

        if delta == 0:
            for start, end in runs:
                if self._height > 0:
                    vis_start = max(start, self._screen_offset(prev_h))
                    if vis_start >= end:
                        continue  # 整个区间在可见区上方（滚动区）→ 跳过
                    start = vis_start
                target_row = self._to_screen(start + 1, prev_h)
                if self._height > 0 and target_row < 1:
                    continue  # 防御：不应发生（起点已钳到可见区）
                target_row = self._clamp(target_row)
                if current_row > target_row:
                    buf.write(cursor_up(current_row - target_row))
                elif current_row < target_row:
                    buf.write(cursor_down(target_row - current_row))
                current_row = target_row
                for idx in range(start, end):
                    if self._write_row(buf, frame, idx, new_h - 1):
                        current_row = self._advance_row(current_row)
        else:
            # ★ 方向4 优化（delta!=0）：头部差异区间逐区间重写 + 位移区连续
            #   重写——替代旧「从 rewrite_start 连续重写整个可见区」。头部动画
            #   （标题栏呼吸）只改头部区间；committed 历史可见区（锚点之前、
            #   非差异行）零重写。
            #   重写目标一律按 **prev 帧偏移** 换算（终端缓冲此刻仍是 prev
            #   布局，只能经底部写行触发滚动）。
            #   ★ 2026-08-18（user_select 连续弹出显示错乱根因修复）：
            #   head_runs 重写的是**旧行**（位移前的 doc 行），其 \n 不应驱动
            #   滚动（滚动只应由位移区新增行承担，次数 = delta）——段末行
            #   （``end-1``）不写 \n。修复前段末行 ``idx < new_h-1`` 恒写 \n：
            #   文档超屏（prev_h >= height）时 head_runs 末尾行写到屏幕底部，
            #   其 \n 触发终端滚动（内容上移），而渲染器 ``_advance_row`` 仅
            #   钳制光标不计数滚动 → 位移区补滚动（shift_start-prev_h+1 次）
            #   叠加 head_runs 的额外滚动，总滚动比 delta 多 1 次 → 内容整体
            #   上移错位一行。复现：超屏文档（30 行历史 + 16 选项弹窗 = 49 行
            #   doc、24 行终端）中弹窗打开（增长 +13 行）后导航，标题行被写
            #   在错误位置，旧标题（如 (1/16)）残留在新标题（如 (7/16)）上方
            #   形成双标题。修复后 head_runs 零滚动，总滚动恰好 = delta。
            for start, end in head_runs:
                target_row = self._clamp(self._to_screen(start + 1, prev_h))
                if current_row > target_row:
                    buf.write(cursor_up(current_row - target_row))
                elif current_row < target_row:
                    buf.write(cursor_down(target_row - current_row))
                current_row = target_row
                for idx in range(start, end):
                    if self._write_row(buf, frame, idx, end - 1):
                        current_row = self._advance_row(current_row)
            # 位移区（锚点起连续重写到新帧末尾）
            if self._height <= 0 or shift_start < new_h:
                target_row = self._clamp(self._to_screen(shift_start + 1, prev_h))
                if current_row > target_row:
                    buf.write(cursor_up(current_row - target_row))
                elif current_row < target_row:
                    buf.write(cursor_down(target_row - current_row))
                current_row = target_row
                for idx in range(shift_start, new_h):
                    # ★ 位移区全为新增行（``idx >= prev_h``，旧帧无此位置——
                    #   如流式增长时锚点之后的纯新增段）且 prev 已满屏/超屏
                    #   （``prev_h >= height``）时滚动次数不足：位移区写 \n
                    #   次数 = m-1（m=行数，末行不写 \n），增长需 delta 次
                    #   滚动 → 缺 ``delta-(m-1) = shift_start-prev_h+1`` 次。
                    #   不补则尾部整体不位移、末行原地覆盖旧末行（review P1，
                    #   如「增长 + 末行内容变化同帧」位移区退化为仅末行）。
                    #   ★ 2026-08-18（与 head_runs 修复协同）：head_runs 段末
                    #   行不写 \n 后本补滚动精确补足缺口（不再叠加 head_runs
                    #   的额外滚动）——总滚动恰好 = delta。
                    if (
                        idx == shift_start
                        and idx >= prev_h
                        and self._height > 0
                        and prev_h >= self._height
                    ):
                        for _ in range(shift_start - prev_h + 1):
                            buf.write("\r")
                            buf.write("\n")
                            current_row = self._advance_row(current_row)
                    if self._write_row(buf, frame, idx, new_h - 1):
                        current_row = self._advance_row(current_row)
            # 缩短：清除残留行（prev 帧 rows new_h+1 .. prev_h）
            if delta < 0:
                # ★ 无末尾空行模型（2026-08-15）：残留行仍从文档 1-based
                #   行号 new_h+1（新帧最后一行之后的物理行）开始——无末尾
                #   空行不影响残留起点（文档 1-based 行号 new_h+1 即物理行
                #   new_h，为新帧最后一行 L[new_h-1] 的下方）。
                target_row = self._to_screen(new_h + 1, prev_h)
                if self._height <= 0 or target_row >= 1:
                    target_row = self._clamp(target_row)
                    if current_row > target_row:
                        buf.write(cursor_up(current_row - target_row))
                    elif current_row < target_row:
                        buf.write(cursor_down(target_row - current_row))
                    current_row = target_row
                    # ★ M3（2026-08-15）：缩短清残留越底滚动兜底——差异行数
                    #   （prev_h-new_h）超过可见区高度时 cursor_down(1) 越过
                    #   屏幕底部触发滚动（物理缓冲增长/内容错乱）。到底部
                    #   （current_row >= height）后只清当前行不再 cursor_down，
                    #   不可达残留行跳过（物理缓冲行数 ``_buf_h`` 已精确跟踪，
                    #   跳过安全）。height=0（无约束）守卫不生效，保持原循环。
                    for _ in range(prev_h - new_h):
                        buf.write(clear_line())
                        if self._height > 0 and current_row >= self._height:
                            current_row = self._advance_row(current_row)
                            continue
                        buf.write(cursor_down(1))
                        current_row = self._advance_row(current_row)
            # 增长：物理缓冲按新文档行数扩张（输出历史回调统一由
            #   ``render`` 的 ``_emit_content_lines`` 收口，只记 committed 内容行）。
            # ★ 无末尾空行模型（2026-08-15）：位移区路径无漂移时物理缓冲 =
            #   新文档行数（原公式含末尾空行 +1 语义已去除）；漂移场景由
            #   ``_grow_drifted`` 处理（本路径仅在 ``_buf_h <= prev_h`` 到达）。
            if delta > 0:
                self._buf_h = max(self._buf_h, new_h)

        # 将光标移回文档底部（保持不变量：render 后光标位于文档底部下方，
        #   供 place_cursor 相对移动；屏幕坐标已钳制）。缩短场景残留行清除后
        #   光标落在 prev 文档底部（max(new_h, prev_h)）；增长/等高落在 new 底部。
        bottom_row = self._bottom_row(max(new_h, prev_h))
        if current_row != bottom_row:
            if current_row > bottom_row:
                buf.write(cursor_up(current_row - bottom_row))
            else:
                buf.write(cursor_down(bottom_row - current_row))
        self._cursor_row = bottom_row
        self._prev = frame
        self._flush_frame_buf(buf)

    # ── 底部对齐增量（滚动/插入行：避免整屏重写） ────────────────

    def _scroll_grow_ok(self, prev_h: int) -> bool:
        """本帧增长是否可走「底部滚动 + 局部重写」快路径。

        前提（任一不满足 → 回退 ``_grow_drifted`` 的物理映射重写）：
          - 已知屏幕高度（``_height > 0``）；
          - **底部对齐**（``_top_aligned is False``）：文档底部贴屏幕底部；
          - 无起始行锚定（``_start_row is None``）：锚定期间文档底部不在
            屏幕底部，滚动会滚错位置；
          - 旧文档最后一行恰在屏幕底部（``_to_screen(prev_h, prev_h) ==
            height``）——否则 ``\\n`` 不会触发预期滚动。

        满足时文档物理行号 = 文档行号 + 常量偏移（底部对齐），滚动 delta 行
        会让未变化行**自动**落到新布局位置，只需重写差异行与位移区。

        Args:
            prev_h: 旧帧高度。

        Returns:
            True — 可走滚动增长；False — 回退原物理映射路径。
        """
        if self._height <= 0 or self._top_aligned or self._start_row is not None:
            return False
        if prev_h <= 0:
            return False
        return self._to_screen(prev_h, prev_h) == self._height

    def _scroll_grow(self, frame: Frame, prev_h: int, new_h: int, first_diff: int) -> None:
        """底部对齐增长：滚动 delta 行后只重写差异行 + 位移区。

        ★ 2026-10-23（用户报障「一次性改变原来的行太多了，会闪」）：
        修复前增长在 ``_buf_h > prev_h``（矮文档首帧 ``_buf_h = max(height,
        n)`` / 缩短后漂移）时走 ``_grow_drifted``——按新旧物理映射逐行比较，
        映射整体位移使**每一行都判定为变化**，每帧重写整个可见区（屏幕
        30 行 → 30 次 ``\\r``+EL+内容），流式输出/输入区换行时整屏闪一下。

        终端原生滚动（屏幕底部写 ``\\n``）会免费完成「已有内容整体上移
        delta 行」：滚动后旧文档行 ``idx`` 恰落在新布局中 ``idx`` 的位置
        （文档高度与物理缓冲同步 +delta），内容未变的行因此**无需重写**。
        本方法：

          1. 光标移到底行（旧文档末行），写 delta 个 ``\\r\\n`` 滚动；
          2. ``_buf_h += delta``（物理缓冲实际增长，``_effective_offset``
             保持文档底部贴底）；
          3. 重写锚点前的差异行（``_diff_runs``，头部动画等）；
          4. 连续重写位移区 ``[anchor, new_h)``（尾部整体下移段 + 新增行——
             滚动只搬移了旧内容，尾部须按新布局落位）；
          5. 光标收尾到屏幕底部、提交 ``_prev``，整帧单次 write + flush。

        Args:
            frame: 新帧（更高）。
            prev_h: 旧帧高度。
            new_h: 新帧高度（``new_h > prev_h``）。
            first_diff: ``first_diff_line`` 结果（首差异行，避免重复扫描）。
        """
        # ★ 2026-10-23（增长闪烁修复）：底栏（状态栏/输入区）固定时，把滚动
        #   区域限制在内容区——底栏不随滚动上移，无需每帧写回底部（消除
        #   「底栏上下跳一下」的闪）。底栏高度变化/未知时回退整屏滚动。
        bottom = self._reserved_bottom_for_scroll()
        if bottom > 0:
            self._scroll_grow_reserved(frame, prev_h, new_h, first_diff, bottom)
            return
        delta = new_h - prev_h
        height = self._height
        prev = cast(Frame, self._prev)
        buf = io.StringIO()
        current_row = self._cursor_row
        # 1) 定位到屏幕底部（旧文档最后一行）
        if current_row > height:
            buf.write(cursor_up(current_row - height))
        elif current_row < height:
            buf.write(cursor_down(height - current_row))
        current_row = height
        # 2) 滚动 delta 行：LF 在屏幕底部触发上滚，已有内容整体上移 delta；
        #    物理缓冲同步增长 delta（文档底部恒贴屏幕底部）。
        #    ★ 先 ``\\r`` 归位：满宽行光标停在 wrap 边界时直接 ``\\n`` 会额外
        #    下移一行（同 ``_write_row`` 的满宽行修复）。
        buf.write("\r\n" * delta)
        self._buf_h += delta
        # 3) 新布局映射（offset 与滚动前一致：buf_h/doc_h 同步 +delta）
        vis_start = self._screen_offset(new_h)
        anchor = self._find_tail_anchor(prev, frame, delta)
        shift_start = max(anchor, vis_start)
        head_runs: list[tuple[int, int]] = []
        if first_diff >= 0 and first_diff < anchor:
            for rs, re_ in self._diff_runs(
                prev, frame, min(prev_h, new_h), start=first_diff,
            ):
                if rs >= anchor:
                    break  # 锚点之后的差异由位移区覆盖
                rs = max(rs, vis_start)
                re_ = min(re_, anchor)
                if re_ > rs:
                    head_runs.append((rs, re_))
        for start, end in head_runs:
            target_row = self._clamp(self._to_screen(start + 1, new_h))
            if current_row > target_row:
                buf.write(cursor_up(current_row - target_row))
            elif current_row < target_row:
                buf.write(cursor_down(target_row - current_row))
            current_row = target_row
            for idx in range(start, end):
                # 段末行不写 \n：重写旧行不驱动滚动（滚动已由第 2 步完成）
                if self._write_row(buf, frame, idx, end - 1):
                    current_row = self._advance_row(current_row)
        # 4) 位移区 [shift_start, new_h)：尾部整体下移段 + 新增行
        if shift_start < new_h:
            target_row = self._clamp(self._to_screen(shift_start + 1, new_h))
            if current_row > target_row:
                buf.write(cursor_up(current_row - target_row))
            elif current_row < target_row:
                buf.write(cursor_down(target_row - current_row))
            current_row = target_row
            for idx in range(shift_start, new_h):
                if self._write_row(buf, frame, idx, new_h - 1):
                    current_row = self._advance_row(current_row)
        # 5) 光标收尾到文档底部（屏幕底部；末行无 \n，通常已在此）
        bottom_row = self._clamp(self._to_screen(new_h, new_h))
        if current_row != bottom_row:
            if current_row > bottom_row:
                buf.write(cursor_up(current_row - bottom_row))
            else:
                buf.write(cursor_down(bottom_row - current_row))
        self._cursor_row = bottom_row
        self._prev = frame
        self._flush_frame_buf(buf)

    def _reserved_bottom_for_scroll(self) -> int:
        """增长局部滚动可保留的底栏行数（0 = 整屏滚动）。

        条件：
          - 底栏行数 > 0 且小于屏幕高度（否则没有内容区可滚动）；
          - 本帧与上一帧底栏行数一致（滚动区域需按旧布局设置；底栏高度
            变化的帧走整屏滚动，正确性优先）。

        Returns:
            可保留的底栏行数；0 表示不使用局部滚动。
        """
        bottom = self._bottom_reserved
        if bottom <= 0 or bottom >= self._height:
            return 0
        if bottom != self._bottom_reserved_prev:
            return 0
        return bottom

    def _scroll_grow_reserved(
        self, frame: Frame, prev_h: int, new_h: int, first_diff: int, bottom: int,
    ) -> None:
        """底栏固定时的增长：滚动区域限制在内容区，底栏原地不动。

        ★ 2026-10-23（用户报障「增长的时候闪」）：整屏滚动会把固定的底栏
        （状态栏/输入区）一起滚上去，随后再写回屏幕底部——mintty 等逐字节
        渲染的终端会显示这个中间态，表现为增长时底栏上下跳一下。本方法用
        DECSTBM 把滚动区域限制在内容区 ``1..height-bottom``：

          1. 设置滚动区域并**绝对定位**到内容区底部（设置区域后光标位置
             终端实现不一，不能依赖）；
          2. 写 delta 个 ``\\r\\n``：只滚动内容区，底栏行完全不动；
          3. 重写内容区差异行 + 位移区（截断到 ``new_h - bottom``，底栏
             不参与）；
          4. 底栏行位置未变，只重写**内容真正变化**的行（时钟/动画/输入）；
          5. 重置滚动区域并绝对定位到屏幕底部，恢复整屏坐标系。

        前提由 ``_reserved_bottom_for_scroll`` 保证（底栏行数 >0、未变、小于
        屏幕高度）；映射与整屏滚动一致（文档底部仍贴屏幕底部）。

        Args:
            frame: 新帧。
            prev_h: 旧帧高度。
            new_h: 新帧高度。
            first_diff: 首差异行。
            bottom: 固定底栏行数。
        """
        delta = new_h - prev_h
        height = self._height
        prev = cast(Frame, self._prev)
        content_h = height - bottom
        content_end = new_h - bottom  # 新帧中属于内容区的文档行数
        buf = io.StringIO()
        # 1) 滚动区域 = 内容区；绝对定位到区域底部（设置 DECSTBM 后光标
        #    位置因终端而异：mintty 移到首行、pyte 保持原行）。
        buf.write(set_scroll_region(1, content_h))
        buf.write(cursor_goto(content_h, 1))
        current_row = content_h
        # 2) 内容区滚动 delta 行（底栏不在区域内，位置/内容都不变）。
        buf.write("\r\n" * delta)
        self._buf_h += delta
        # 3) 内容区差异行 + 位移区（底栏之前的文档行）
        vis_start = self._screen_offset(new_h)
        anchor = self._find_tail_anchor(prev, frame, delta)
        shift_start = max(anchor, vis_start)
        head_runs: list[tuple[int, int]] = []
        if first_diff >= 0 and first_diff < anchor:
            for rs, re_ in self._diff_runs(
                prev, frame, min(prev_h, new_h), start=first_diff,
            ):
                if rs >= anchor:
                    break  # 锚点之后的差异由位移区覆盖
                rs = max(rs, vis_start)
                re_ = min(re_, anchor, content_end)
                if re_ > rs:
                    head_runs.append((rs, re_))
        for start, end in head_runs:
            target_row = self._clamp(self._to_screen(start + 1, new_h))
            if current_row > target_row:
                buf.write(cursor_up(current_row - target_row))
            elif current_row < target_row:
                buf.write(cursor_down(target_row - current_row))
            current_row = target_row
            for idx in range(start, end):
                if self._write_row(buf, frame, idx, end - 1):
                    current_row = self._advance_row(current_row)
        if shift_start < content_end:
            target_row = self._clamp(self._to_screen(shift_start + 1, new_h))
            if current_row > target_row:
                buf.write(cursor_up(current_row - target_row))
            elif current_row < target_row:
                buf.write(cursor_down(target_row - current_row))
            current_row = target_row
            for idx in range(shift_start, content_end):
                if self._write_row(buf, frame, idx, content_end - 1):
                    current_row = self._advance_row(current_row)
        # 4) 底栏差异行：位置固定（屏幕 content_h+1..height），逐行比较，
        #    只重写内容变化的行（时间/动画/输入文字）；内容未变零写入。
        for i in range(bottom):
            old_idx = prev_h - bottom + i
            new_idx = new_h - bottom + i
            if not (0 <= old_idx < prev_h and 0 <= new_idx < new_h):
                continue
            old_line = prev.lines[old_idx]
            new_line = frame.lines[new_idx]
            if old_line is new_line or _fd._lines_equal(old_line, new_line):
                continue
            screen_row = content_h + 1 + i
            buf.write(cursor_goto(screen_row, 1))
            buf.write("\r")
            if self._line_needs_clear(frame, new_idx):
                buf.write(_CLEAR_EOL)
            buf.write(frame.render_line(new_idx))
        # 5) 重置滚动区域（光标位置因终端而异）并绝对定位到屏幕底部。
        buf.write(reset_scroll_region())
        buf.write(cursor_goto(height, 1))
        self._cursor_row = height
        self._prev = frame
        self._flush_frame_buf(buf)

    def _insert_shrink_ok(self, prev_h: int) -> bool:
        """本帧缩短是否可走「IL 插入空行 + 局部重写」快路径。

        前提（任一不满足 → 回退 ``_rewrite_drifted`` 的物理映射重写）：
          - 已知屏幕高度；
          - **底部对齐**且无起始行锚定（文档底部贴屏幕底部，缩短时整篇内容
            下移 ``|delta|`` 行）；
          - 已渲染出物理缓冲（``_buf_h > 0``）；
          - 旧文档最后一行恰在屏幕底部。

        Args:
            prev_h: 旧帧高度。

        Returns:
            True — 可走 IL 缩短；False — 回退原物理映射路径。
        """
        if self._height <= 0 or self._top_aligned or self._start_row is not None:
            return False
        if prev_h <= 0 or self._buf_h <= 0:
            return False
        return self._to_screen(prev_h, prev_h) == self._height

    def _insert_shrink(self, frame: Frame, prev_h: int, new_h: int) -> None:
        """底部对齐缩短：IL 插入 ``|delta|`` 空行后只重写变化行。

        ★ 2026-10-23（用户报障「一次性改变原来的行太多了，会闪」）：
        修复前缩短走 ``_rewrite_drifted``——缩短使文档整体下移 ``|delta|``
        行，物理映射逐行错位 → **每一行都判定为变化**，整屏重写（屏幕 30
        行 → 30 次写行）。输入区退格/关闭弹窗/流式解析行消失时整屏闪一下。

        IL（``\\033[nL``）让终端原生完成「光标行插入 n 空行 + 下方内容下移
        + 滚动区底部 n 行丢弃」：未变化行因此**无需重写**（下移后恰落在新
        布局位置），只需重写：

          - **插入的空行区**（文档首部下移后空出的行——矮文档时是文档
            首行位置，高文档时是屏幕顶部，需写入新文档对应行）；
          - **被丢弃的底部 n 行**（新文档末行须补写）；
          - **内容真正变化的行**（与下移 delta 后的旧内容逐屏比较）。

        插入点：旧文档首行在屏幕内（矮文档）→ 插在文档首行（上方终端历史
        不移动）；文档高于屏幕（首行在屏幕外上方）→ 插在屏幕第 1 行。

        Args:
            frame: 新帧（更矮）。
            prev_h: 旧帧高度。
            new_h: 新帧高度（``new_h < prev_h``）。
        """
        n = prev_h - new_h
        height = self._height
        prev = cast(Frame, self._prev)
        buf = io.StringIO()
        current_row = self._cursor_row
        # 旧文档首行的屏幕行（高文档时 <= 0，表示在屏幕外上方）
        y0 = self._to_screen(1, prev_h)
        insert_at = y0 if y0 >= 1 else 1
        # 1) 定位插入点并插入 n 个空行（IL 不移动光标）
        if current_row > insert_at:
            buf.write(cursor_up(current_row - insert_at))
        elif current_row < insert_at:
            buf.write(cursor_down(insert_at - current_row))
        current_row = insert_at
        buf.write("\r")
        buf.write(insert_lines(n))
        # 2) 逐屏幕行比较「IL 后的旧内容」与「新布局内容」，只重写差异行。
        #    IL 后屏幕行 s 的内容来源：
        #      s < insert_at          → 插入点上方未移动：新文档已整体下移，
        #                                这些行应为空；会话写入范围内
        #                                （``q >= _painted_top``）的旧 UI 残留
        #                                须清空，用户历史保留
        #      insert_at <= s < +n    → IL 插入的空行（需写新内容或保持空）
        #      s >= insert_at + n     → 原屏幕行 s-n 的内容（文档行
        #                                (s-n) - y0；越界为被丢弃/空）
        offset = self._effective_offset(new_h)
        # 屏幕行 → 物理行换算（0-based）：物理行 = 屏幕行 - 1 + 视口顶部偏移
        viewport_top = max(0, self._buf_h - height)
        rewrites: list[tuple[int, int]] = []  # (屏幕行, 新文档行索引；-1=清空)
        for s in range(1, height + 1):
            new_idx = s - 1 + offset
            new = frame.lines[new_idx] if 0 <= new_idx < new_h else None
            if s < insert_at:
                # 插入点上方：未移动。新布局此处应为空（矮文档新首行在
                # insert_at+n）；会话写过（物理行 >= _painted_top）的旧 UI
                # 残留清空，首帧之前的用户历史保留。
                if new is not None:
                    rewrites.append((s, new_idx))
                elif s - 1 + viewport_top >= self._painted_top:
                    rewrites.append((s, -1))
                continue
            if s < insert_at + n:
                old = None  # IL 插入的空行
            else:
                q = s - n
                old_idx = q - y0
                if old_idx < 0:
                    # 插入点上方不可知内容（防御；正常路径不出现）→ 保守不动
                    continue
                old = prev.lines[old_idx] if old_idx < prev_h else None
            if old is None and new is None:
                continue
            if old is not None and new is not None and _fd._lines_equal(old, new):
                continue
            rewrites.append((s, new_idx if new is not None else -1))
        # 3) 自底向上逐行重写（不写 \n：避免在屏幕底部触发滚动）
        rewrites.sort(key=lambda item: item[0], reverse=True)
        for s, new_idx in rewrites:
            if current_row > s:
                buf.write(cursor_up(current_row - s))
            elif current_row < s:
                buf.write(cursor_down(s - current_row))
            current_row = s
            buf.write("\r")
            # 清空行（new_idx == -1）必须 EL；重写内容行满宽时直接覆盖（免闪）。
            if new_idx == -1 or self._line_needs_clear(frame, new_idx):
                buf.write(_CLEAR_EOL)
            if new_idx != -1:
                buf.write(frame.render_line(new_idx))
        # 4) 光标收尾到文档底部（屏幕底部）
        bottom_row = self._clamp(self._to_screen(new_h, new_h))
        if current_row != bottom_row:
            if current_row > bottom_row:
                buf.write(cursor_up(current_row - bottom_row))
            else:
                buf.write(cursor_down(bottom_row - current_row))
        self._cursor_row = bottom_row
        self._flush_frame_buf(buf)

    def _grow_drifted(self, frame: Frame, prev_h: int, new_h: int) -> None:
        """有漂移增长（缩短后物理缓冲漂移 ``_buf_h > prev_h``）：重写可见区
        变化行 + 追加新行。

        增长时 offset 不再随 doc_h 变化（``_buf_h`` 漂移固定）——平移快路径/
        head_runs/位移区逻辑假设无漂移（``new[k+delta]==prev[k]`` 锚点前免重
        写），漂移时锚点之前的可见区行因「物理行映射位移」也需重写。本方法用
        物理映射统一处理：物理行 ``q`` 增长后显示新文档行 ``q - drift1``
        （``drift1 = _buf_h1 - new_h``），与当前 prev 行 ``q - drift0``
        逐行比较重写变化行（自底向上不写 ``\n``；``doc_idx < 0`` 文档上方空行
        区清空），再追加新行滚动/扩展物理缓冲（``grow_rows =
        max(0, new_h - _buf_h0)`` = 实际写行导致的缓冲增长）。

        **顶部对齐模式（补全弹窗闪烁修复）**：当前顶部对齐（``_top_aligned``）
        且 doc 仍高于屏幕且**无漂移**（``buf_h0 <= prev_h``——物理缓冲 = 文档
        行数）时，物理行 ``q`` 直接显示 doc 行 ``q``（drift=0）——弹窗/尾部
        增长只重写变化行 + 在残留位置追加新行（缓冲足够时不滚动，物理缓冲不
        变），弹窗上方（历史消息）永不重写。底部对齐映射（drift1 由
        ``_buf_h1`` 推导）在顶部对齐时退化为 0（无残留时 ``buf_h1 == new_h``）。
        doc 进入屏幕内（``new_h <= height``）时切换为底部对齐契约（完整文档
        可见），由本方法置 ``_top_aligned=False``。
        ★ 漂移 + 增长（2026-08-15，review P3）：缩短后物理缓冲漂移
        （``buf_h0 > prev_h``）时即使 doc 仍高于屏幕也切换底部对齐——顶部对齐
        保持会让 doc 末行停在物理缓冲中段、下方残留清空空行（用户报障的「模式
        行下方空行」在漂移增长窗口期复现）；切换底部对齐使文档末行贴物理缓冲
        底部（模式行贴底）。补全弹窗闪烁修复不受影响（弹窗增长通常无漂移）。

        Args:
            frame: 新帧（较长）。
            prev_h: 旧帧高度。
            new_h: 新帧高度（new_h > prev_h）。
        """
        buf = io.StringIO()
        height = self._height
        buf_h0 = self._buf_h
        buf_top0 = max(0, buf_h0 - height)
        # 顶部对齐：物理行 q → doc q（drift=0）；底部对齐：drift 由缓冲推导。
        # ★ BUG-64（review 方向，渲染错乱）：记录切换前顶部对齐状态——doc
        #   进入屏幕内（``new_h+1 <= height``）切换底部对齐时，**旧布局**（顶部
        #   对齐）drift 恒为 0（物理行 q 直接显示 doc 行 q）。修复前 else 分支
        #   统一用底部对齐公式 ``buf_h0 - prev_h - 1`` 推导旧行位置——对顶部
        #   对齐旧布局误判（物理行 q 显示旧 doc q-drift0，实际显示旧 doc q）→
        #   必要重写被跳过 → 内容行从屏幕丢失。触发路径：常规缩短（``_screen_
        #   offset(prev_h)==0`` 走常规 diff 路径，物理布局保持顶部对齐）后增长
        #   进入屏幕内（``_grow_drifted`` 切换底部对齐）——如 4→3→4 行序列中
        #   中间行 'x0' 消失（模糊测试锁定）。
        was_top_aligned = self._top_aligned
        if was_top_aligned:
            if new_h > height and buf_h0 <= prev_h:
                # 无漂移（物理缓冲 = 文档行数）且 doc 仍高于屏幕：保持顶部
                # 对齐（补全弹窗闪烁修复契约——弹窗上方历史永不重写）。
                top_aligned = True
            else:
                # ★ 漂移 + 增长（2026-08-15，review P3）：物理缓冲大于文档
                #   行数（缩短后保持）时切换底部对齐——顶部对齐保持会让 doc
                #   末行停在物理缓冲中段、下方残留清空空行（模式行下方空行
                #   在漂移增长窗口期复现）；底部对齐使文档末行贴物理缓冲底部。
                #   doc 进入屏幕内（new_h <= height）也切换底部对齐契约。
                self._top_aligned = False
                top_aligned = False
        else:
            top_aligned = False
        if top_aligned:
            drift0 = 0
            drift1 = 0
            grow_rows = max(0, new_h - buf_h0)
            buf_h1 = buf_h0 + grow_rows
        else:
            # ★ BUG-64：旧布局顶部对齐（was_top_aligned=True）时旧行偏移恒 0
            #   （物理行 q = doc 行 q）——仅旧布局已底部对齐时才用缓冲推导公式。
            #   ★ 无末尾空行模型（2026-08-15）：drift0/drift1 公式去掉
            #   末尾空行 +1 项（物理行号 = 文档行号，无 doc_h+1 空行）。
            drift0 = 0 if was_top_aligned else (buf_h0 - prev_h)
            if self._start_row is not None:
                # ★ 起始行锚定（2026-10-06）：文档底部随增长下移、起始行保持
                #   ——缓冲目标 = min(起始行 + 新高度 - 1, 屏幕高度)；drift
                #   恒为 ``起始行 - 1``（未贴底），仅重写新增/变化行。
                buf_h1 = min(self._start_row + new_h - 1, height)
                grow_rows = max(0, buf_h1 - buf_h0)
            else:
                grow_rows = max(0, new_h - buf_h0)
                buf_h1 = buf_h0 + grow_rows
            drift1 = buf_h1 - new_h   # 增长后漂移
        prev = cast(Frame, self._prev)
        rewrites: list[tuple[int, int]] = []
        # ★ 2026-10-22（启动期渲染错位修复）：偏移变化时文档可能上移到更靠上
        #   的物理行——同步扩大会话写入范围（清理"上方空行区"只清会话写过/
        #   映射到的行，不误清用户终端里首帧之前的历史输出）。
        self._painted_top = min(self._painted_top, max(0, drift1))
        # ★ 2026-10-22（启动期渲染错位修复）：本帧新旧偏移不同时，物理行与
        #   文档行的映射整体位移——``old_idx < 0``（旧布局同样覆盖不到）的
        #   物理行无法用 prev 帧判断内容，但屏幕上可能残留**更早帧**绘制的
        #   UI（如启动首帧回退底部锚定后、文档增长使偏移从 8 变 6，顶部
        #   0..5 行残留上一版 banner/边框）。偏移变化时对会话写入范围内的
        #   这类行保守清空；偏移不变时保持原判定（该区域上次已清过，避免
        #   每帧重复清行）。
        drift_changed = drift0 != drift1
        for q in range(buf_top0, buf_h0):
            old_idx = q - drift0
            doc_idx = q - drift1
            old_line = prev.lines[old_idx] if 0 <= old_idx < prev_h else None
            if doc_idx < 0:
                # 文档上方空行区（增长时文档进入屏幕内）：物理行须为空。
                # ★ BUG-76 同源：漂移时 old_idx 越界（残留自更早帧）→ 保守清除。
                # ★ 2026-10-22：偏移变化时 old_idx < 0 的未知行同样清除
                #   （仅限会话写入范围，用户历史行保持原样）。
                if (
                    old_line is not None
                    or old_idx >= prev_h
                    or (
                        drift_changed
                        and old_idx < 0
                        and q >= self._painted_top
                    )
                ):
                    rewrites.append((q, -1))
                continue
            if doc_idx >= new_h:
                # 残留（越界；无末尾空行模型下 doc_idx == new_h 已越界）：
                # 物理行须为空。★ BUG-76 同源。
                # ★ 2026-10-22：偏移变化时 old_idx < 0 的未知行同样清除。
                if (
                    old_line is not None
                    or old_idx >= prev_h
                    or (
                        drift_changed
                        and old_idx < 0
                        and q >= self._painted_top
                    )
                ):
                    rewrites.append((q, -1))
                continue
            new_line = frame.lines[doc_idx]
            # ★ 性能/语义统一（2026-10-23）：比较走 ``_lines_equal``（身份 /
            #   同 runs 列表 / run 数 / 已缓存宽度 O(1) 快速拒绝），修复前直接
            #   ``old_line.runs != new_line.runs``——漂移重写每帧对可见行逐 run
            #   比较，宽字符/重建行场景漏掉宽度快速拒绝。
            if old_line is not new_line and (
                old_line is None or not _fd._lines_equal(old_line, new_line)
            ):
                rewrites.append((q, doc_idx))
        current_row = self._cursor_row
        if rewrites:
            rewrites.sort(key=lambda t: t[0], reverse=True)
            for q, doc_idx in rewrites:
                target_row = max(1, min(q - buf_top0 + 1, height))
                if current_row > target_row:
                    buf.write(cursor_up(current_row - target_row))
                elif current_row < target_row:
                    buf.write(cursor_down(target_row - current_row))
                current_row = target_row
                buf.write("\r")
                # ★ 行尾宽字符修复（2026-08-06）：EL 0 前移（先清行再写内容）
                #   ——满宽行尾 CJK 不再被清除（见平移快路径注释）。doc_idx==-1
                #   仍只清行（不写内容）；★ 2026-10-23：满宽行省略 EL（直接
                #   覆盖，避免状态栏/边框每帧清空重写闪烁）。
                if doc_idx == -1 or self._line_needs_clear(frame, doc_idx):
                    buf.write(_CLEAR_EOL)
                if doc_idx != -1:
                    buf.write(frame.render_line(doc_idx))
                # 不写 \n：自底向上用 cursor_up/down 移动，旧缓冲行不触发滚动
        # 追加新行（滚动扩展物理缓冲）：漂移吸收为 0 时 buf_h1 == new_h，
        # 追加循环最后一个 doc 行不写 \n（无末尾空行）。
        if grow_rows > 0:
            append_start = max(0, buf_h0 - drift1)  # 第一个新内容行（drift1=0 ⇒ buf_h0）
            # ★ 渲染错乱（模糊测试锁定）：doc 行 append_start 的目标物理行 =
            #   buf_h0（底部对齐物理映射）——先移到物理行 buf_h0-1 再 \n 创建
            #   物理行 buf_h0。修复前直接在 bottom_row（=物理行 buf_h0-1）写
            #   doc 行，覆盖 rewrites 刚写入的 doc 行 buf_h0-1-drift1（内容行
            #   丢失/错位，如 2→6 行增长中 doc 行 3 'a' 被 doc 行 4 'c' 覆盖、
            #   2→5 行增长中 'b' 被 'status' 覆盖）。
            target_row = max(1, min(buf_h0 - buf_top0, height))
            if current_row > target_row:
                buf.write(cursor_up(current_row - target_row))
            elif current_row < target_row:
                buf.write(cursor_down(target_row - current_row))
            current_row = target_row
            buf.write("\n")
            current_row = self._advance_row(current_row)
            for doc_idx in range(append_start, new_h):
                # ★ 与 _write_row 逐字节等价（追加行 = 位移区新增行语义：
                #   last_idx = new_h-1 文档末行不写 \n）；统一走公共写行辅助
                #   防「修一处漏四处」（2026-08-18）。
                if self._write_row(buf, frame, doc_idx, new_h - 1):
                    current_row = self._advance_row(current_row)
        bottom_row = max(1, min(buf_h1, height))
        if current_row != bottom_row:
            if current_row > bottom_row:
                buf.write(cursor_up(current_row - bottom_row))
            else:
                buf.write(cursor_down(bottom_row - current_row))
        self._cursor_row = bottom_row
        self._buf_h = buf_h1
        self._prev = frame
        self._flush_frame_buf(buf)

    def _diff_runs(
        self,
        prev: Frame,
        frame: Frame,
        n: int,
        start: int = 0,
    ) -> list[tuple[int, int]]:
        """收集两帧前 n 行的差异区间（[start, end) 行号，升序、不重叠）。

        ★ 模块边界（2026-08-05）：实现已迁至 ``_frame_diff.py``（纯函数，
        不依赖实例状态）；本方法为薄包装（测试 ``r._diff_runs(...)`` 实例
        调用兼容）。★ P3（review）：经模块级 ``_fd`` 引用调用（免函数内
        import 热路径开销）。
        """
        return _fd._diff_runs(prev, frame, n, start)

    def _is_tail_shifted(self, prev: Frame, frame: Frame, i: int, delta: int) -> bool:
        """检测尾部内容是否只是整体下移（仅新增 delta 行）。

        ★ 模块边界（2026-08-05）：实现已迁至 ``_frame_diff.py``（纯函数）；
        本方法为薄包装（测试实例调用兼容）。
        """
        return _fd._is_tail_shifted(prev, frame, i, delta)

    def _find_tail_anchor(self, prev: Frame, frame: Frame, delta: int) -> int:
        """从文档末尾向前找尾部位移锚点（方向4 优化）。

        ★ 模块边界（2026-08-05）：实现已迁至 ``_frame_diff.py``（纯函数）；
        本方法为薄包装（测试实例调用兼容）。
        """
        return _fd._find_tail_anchor(prev, frame, delta)

    def _rewrite_drifted(
        self, frame: Frame, prev_h: int, new_h: int, first_diff: int | None = None,
    ) -> None:
        """漂移物理映射重写（缩短/等高）：重写可见区变化行 + 清残留。

        用户需求「除 resize 外均增量」：替代原「文档高于屏幕时缩短 → 全量
        clear + 重建」（闪烁）及「缩短/等高进入屏幕内 → 全量重建」。物理缓冲
        无法删除行（无 DECSTBM/DL），缩短后缓冲长度保持 ``_buf_h``（清行不删
        行）——偏移漂移由 ``_buf_h`` 精确跟踪，后续增长/等高重写按真实物理
        偏移定位（不漂移错位）。

        **顶部对齐模式（补全弹窗闪烁修复）**：文档仍高于屏幕
        （``new_h > height``）且当前顶部对齐（``_top_aligned``）时，物理行
        ``q`` 直接显示新文档行 ``q``（doc 0 固定在物理行 0）——弹窗/尾部区域
        变化只重写变化行 + 清残留，**弹窗上方（历史消息）永不重写**（消除
        补全弹窗 items 数量变化/弹窗缩放时整个可见区被重写的视觉闪烁）。
        区别于底部对齐（``drift = _buf_h - new_h``）：底部对齐下缩短导致
        整个文档映射位移 delta 行，弹窗上方所有物理行映射到不同 doc 行 → 全
        可见区重写。

        **底部对齐模式**：文档进入屏幕内（``new_h <= height``）或当前已处
        底部对齐（``_top_aligned=False``）时，物理行 ``q`` 显示新文档行
        ``q - drift``（``drift = _buf_h - new_h``，可为负）——文档底部对齐
        可见区底部，``doc_idx < 0``（文档上方空行区）清空（缩短进入屏幕内时
        完整文档可见，既有契约）。

        核心映射：物理行 ``q``（0-based）显示新文档行 ``q - drift``，其中
        ``drift = _buf_h - new_h``（物理偏移 - 新文档理想偏移）；当前物理
        行内容为 prev 行 ``q - drift0``（``drift0 = _buf_h - prev_h``；顶部
        对齐切换前为 0）。可见区物理行 ``[buf_top, buf_h)`` 覆盖新文档
        ``[理想偏移, new_h)``。逐物理行与 prev 帧对应内容比较
        （身份短路 + runs 值相等），仅重写变化行；``doc_idx < 0``（文档上方
        空行区）/ 越界（残留）写清行。

        自底向上重写（``cursor_up`` 定位，**不写 ``\n``**）——避免在屏幕底部
        写行触发滚动（滚动会改变物理缓冲，使 ``_buf_h`` 漂移不可控）；物理
        缓冲行数不变。写行后光标保持同列（下一行 ``\r`` 归位），末尾移回
        缓冲末行（屏幕底部）。

        Args:
            frame: 新帧（较短或等高）。
            prev_h: 旧帧高度。
            new_h: 新帧高度（new_h <= prev_h）。
        """
        buf = io.StringIO()
        height = self._height
        buf_h = self._buf_h
        buf_top = max(0, buf_h - height)  # 可见区首物理行（0-based）
        prev = cast(Frame, self._prev)
        # ★ 修复（2026-10-06 用户报障「流式输出时中间删除几行有机率渲染
        #   错误」）：一律切**底部对齐**（``drift = buf_h - new_h``）。
        #
        #   原实现（BUG-68）「首差异行 > buf_top（尾部删除）保持顶部对齐
        #   drift=0」在 ``_buf_h > new_h``（缩短/漂移等高时必然成立——物理
        #   缓冲只增不减）时，物理行 q 显示 doc 行 q，文档末尾停在物理行
        #   ``new_h-1``，而可见区底部是物理行 ``buf_h-1`` —— 文档末尾不贴
        #   屏幕底部：可见区底部 ``buf_h-new_h`` 行为清空残留空行，屏幕内容
        #   整体上移、底部留空（状态栏/输入区离开屏幕底部）。流式期间反复
        #   缩短时漂移累积（``_buf_h`` 不随文档缩短回收）：文档逐步被挤出
        #   可见区（``new_h <= buf_top`` 时可见区全在文档下方 → 屏幕空白）。
        #   复现：超屏文档（76 行）+ 中间删除 1 行（parse 进度行清除）→
        #   屏幕显示 doc[52:75]（首行滚出）+ 底部 1 空行，而非 doc 最后
        #   24 行。
        #
        #   切底部对齐后物理行 q 显示 doc 行 ``q - drift``，可见区映射
        #   ``[new_h-height, new_h)``（文档最后 height 行，不足时顶部补空行）
        #   ——文档末尾恒贴物理缓冲末尾（屏幕底部），与「底部区（状态栏/
        #   输入区）固定屏幕底部」契约一致；重写范围由下方逐物理行内容比较
        #   决定（删除点之后的行内容与旧布局相同 → 不重写，仅删除点上方
        #   可见行重写）。``first_diff`` 参数保留（既有调用/测试签名兼容）。
        if self._top_aligned:
            old_drift = 0
            self._top_aligned = False
        else:
            # ★ 无末尾空行模型（2026-08-15）：drift 公式去掉末尾空行 +1
            #   项（物理行号 = 文档行号，无 doc_h+1 空行）。
            old_drift = buf_h - prev_h
        if self._start_row is not None:
            # ★ 起始行锚定（2026-10-06）：起始行保持、文档底部随缩短上移
            #   ——drift 目标 = min(起始行 + 新高度 - 1, 屏幕高度) - 新高度
            #   （未贴底时恒为 ``起始行 - 1``，与 old_drift 相同 → 仅重写
            #   变化行；文档已触及底部时本分支不可达——``_sync_start_row_anchor``
            #   已置回 None）。
            drift = min(self._start_row + new_h - 1, height) - new_h
        else:
            drift = buf_h - new_h
        # 待重写项：(物理行, 新文档行)；doc_idx==-1 表示清除残留/空行。
        rewrites: list[tuple[int, int]] = []
        # ★ 2026-10-22（启动期渲染错位修复）：偏移变化时文档可能上移到更靠上
        #   的物理行——同步扩大会话写入范围（同 ``_grow_drifted``，避免误清
        #   用户终端里首帧之前的历史输出）。
        self._painted_top = min(self._painted_top, max(0, drift))
        # ★ 2026-10-22（启动期渲染错位修复）：偏移变化时映射整体位移——见
        #   ``_grow_drifted`` 同名变量注释（物理行旧内容无法由 prev 帧判定时
        #   保守清空，消除上一版 UI 残留在文档上方空行区）。
        drift_changed = old_drift != drift
        for q in range(buf_top, buf_h):
            doc_idx = q - drift
            old_idx = q - old_drift
            old_line = prev.lines[old_idx] if 0 <= old_idx < prev_h else None
            if doc_idx < 0:
                # 文档上方空行区（缩短进入屏幕内时可见区顶部）：物理行须为空。
                # ★ 渲染错误（BUG-76）：物理行旧内容不在 prev doc 中
                #   （``old_idx >= prev_h``——物理缓冲漂移，旧内容残留自更早
                #   帧）时无法用 ``old_line is None`` 判断空——须保守清除，
                #   否则缩短进入屏幕内后旧行残留在可见区顶部。
                # ★ 2026-10-22：偏移变化时 old_idx < 0 的未知行同样清除
                #   （仅限会话写入范围）。
                if (
                    old_line is not None
                    or old_idx >= prev_h
                    or (
                        drift_changed
                        and old_idx < 0
                        and q >= self._painted_top
                    )
                ):
                    rewrites.append((q, -1))
                continue
            if doc_idx >= new_h:
                # 残留（越界；无末尾空行模型下 doc_idx == new_h 已越界）：
                # 物理行须为空。★ BUG-76 同源：漂移时 old_idx 越界（物理行
                # 内容残留自更早帧）→ 保守清除，防缩短后旧行残留。
                # ★ 2026-10-22：偏移变化时 old_idx < 0 的未知行同样清除。
                if (
                    old_line is not None
                    or old_idx >= prev_h
                    or (
                        drift_changed
                        and old_idx < 0
                        and q >= self._painted_top
                    )
                ):
                    rewrites.append((q, -1))
                continue
            new_line = frame.lines[doc_idx]
            # ★ 性能/语义统一（2026-10-23）：比较走 ``_lines_equal``（身份 /
            #   同 runs 列表 / run 数 / 已缓存宽度 O(1) 快速拒绝），修复前直接
            #   ``old_line.runs != new_line.runs``——漂移重写每帧对可见行逐 run
            #   比较，宽字符/重建行场景漏掉宽度快速拒绝。
            if old_line is not new_line and (
                old_line is None or not _fd._lines_equal(old_line, new_line)
            ):
                rewrites.append((q, doc_idx))
        if not rewrites:
            # 可见区无变化：不写终端（实际光标未移动）——**不得**改写
            # ``_cursor_row``。修复前误设为物理缓冲末行
            # ``max(1, min(buf_h, height))``：记账与实际终端光标脱节（如
            # 实际停在输入行 22、记账变成 24），下一帧 ``place_cursor`` 按
            # 脱节值相对移动 → 光标落到 20，随后等高帧的增量写入整体下移
            # 2 行 → 补全弹窗缩小后提示行/候选项错位、旧输入行残留。
            # 保持原值（= 上一次渲染后的实际光标行）即与实际一致。
            self._prev = frame
            self._stream.flush()
            return
        rewrites.sort(key=lambda t: t[0], reverse=True)  # 自底向上（防滚动）
        current_row = self._cursor_row
        for q, doc_idx in rewrites:
            target_row = max(1, min(q - buf_top + 1, height))
            if current_row > target_row:
                buf.write(cursor_up(current_row - target_row))
            elif current_row < target_row:
                buf.write(cursor_down(target_row - current_row))
            current_row = target_row
            buf.write("\r")
            # ★ 行尾宽字符修复（2026-08-06）：EL 0 前移（先清行再写内容）——
            #   满宽行尾 CJK 不再被清除（见平移快路径注释）。doc_idx==-1 仍只
            #   清行（不写内容）；★ 2026-10-23：满宽行省略 EL（直接覆盖，防闪）。
            if doc_idx == -1 or self._line_needs_clear(frame, doc_idx):
                buf.write(_CLEAR_EOL)
            if doc_idx != -1:
                buf.write(frame.render_line(doc_idx))
            # 不写 \n：自底向上用 cursor_up/down 移动，物理缓冲不变
        # 光标移到物理缓冲末尾（屏幕底部；此分支仅在 height>0 且 _buf_h>height
        # 时进入，缓冲末行屏幕坐标 = height）。
        bottom_row = max(1, min(buf_h, height))
        if current_row > bottom_row:
            buf.write(cursor_up(current_row - bottom_row))
        elif current_row < bottom_row:
            buf.write(cursor_down(bottom_row - current_row))
        self._cursor_row = bottom_row
        self._prev = frame
        self._flush_frame_buf(buf)

    def _take_clear_before_full(self) -> bool:
        """消费「下一次全量写入前清屏」标志（一次性）。"""
        clear = self._clear_before_full
        self._clear_before_full = False
        return clear

    def _write_full(self, frame: Frame) -> None:
        """首帧/重置后：全量写入文档。

        raw 终端模式下 \n 不归位列 1，每行前缀 \r（与 OutputAdapter 的
        CRLF 语义一致）。方向1：整帧缓冲单次 write+flush（免逐行 flush
        闪烁/撕裂）。

        ★ 2026-10-20（resize 残留修复）：``reset(full=True)``（resize）后
        的首次全量写入先写 ``clear_screen()``（ED 2 + 归位，不清 scrollback）
        再按底部锚定重写——修复前只从新文档起始行逐行覆盖，旧帧（旧宽度，
        且被终端 reflow 成更多物理行）在屏幕上方/行尾残留，resize 后屏幕
        出现两份 UI / 行内左右两段来自不同帧。首帧（未经 reset）不清屏，
        不会破坏用户终端里已有的历史输出。

        Args:
            frame: 目标帧。
        """
        clear_first = self._take_clear_before_full()
        if not frame.lines:
            # 方向1 步骤3（首帧空帧光标）：空帧也更新 _cursor_row（=1）
            # ——修复前空帧不置位，下一帧 ``n_move`` 产生多余光标移动。
            # ★ 无末尾空行模型（2026-08-15）：空帧 _buf_h 置 0（无虚拟
            #   末尾空行）——空帧后增长首行原地写（终端光标在行 1）。
            if clear_first:
                try:
                    self._stream.write(clear_screen())
                    self._stream.flush()
                except (OSError, ValueError):
                    _logger.debug("空帧全量清屏写入异常", exc_info=True)
                self._start_row = None
                # ★ 2026-10-22：已清屏——重置会话写入范围（屏幕干净）。
                self._painted_top = 0
            self._buf_h = 0
            self._top_aligned = True
            self._cursor_row = self._bottom_row(frame.height)
            return
        buf = io.StringIO()
        # ★ 2026-10-23：全量重写从干净坐标系开始——上一帧局部滚动（DECSTBM）
        #   若因写入异常未重置，残留的滚动区域会让后续换行只滚动部分屏幕。
        #   无条件重置（未设置过时该序列无副作用）。
        buf.write(reset_scroll_region())
        if clear_first:
            # 清屏 + 光标归位 (1,1)；随后 cursor_goto 重新定位到起始行。
            # 清屏使旧帧残留（含终端 reflow 产生的额外物理行）全部消失。
            # 清屏后坐标系已重置：启动行锚定失效（回退底部锚定）。
            buf.write(clear_screen())
            self._cursor_row = 1
            self._start_row = None
            self._start_anchor_pending = False
        n = len(frame.lines)
        # ★ 2026-10-06（用户需求：启动锚定「当前光标行 + 1」）：首个全量帧
        #   消费注入的光标行查询——有效时起始行 = 光标行 + 1（文档从光标
        #   下方开始追加，不跳到屏幕底部）；查询失败/未注入时回退「文档
        #   底部贴屏幕底部」（保证坐标模型与实际落位一致）。定位行钳制到
        #   屏幕内（起始行 > 屏幕高度时由写入换行触发终端滚动）。
        start_row = self._resolve_start_row(n)
        if self._height > 0:
            buf.write(cursor_goto(min(start_row, self._height), 1))
        for idx, line in enumerate(frame.lines):
            buf.write("\r")
            # ★ P2（review）：全量写行补 ``_CLEAR_EOL``——修复前仅写
            #   「\r + 内容 + \r」，与增量路径（``_diff_runs``/``_grow_drifted``/
            #   ``_rewrite_drifted`` 均「\r + EL + 内容 + \r」）不一致：resize
            #   后物理行上更宽的旧内容尾部残留到行尾（EL 前置语义同增量路径，
            #   行尾宽字符不被清除）。
            buf.write(_CLEAR_EOL)
            buf.write(line.render())
            # ★ 满宽行 wrap 修复（同 _diff_runs 写行循环）：行内容填满宽度时
            #   \r 归位避免 \n 触发 wraparound 额外下移。
            buf.write("\r")
            # ★ 无末尾空行模型（2026-08-15）：最后一行不写 \n——光标停在
            #   文档最后一行（模式行），不产生末尾空行（修复「满屏时模式行
            #   下方多一行空行」：doc_h == height 时首行不再被滚动挤出）。
            if idx < n - 1:
                buf.write("\n")
        # ★ 无末尾空行模型（2026-08-15 + 2026-10-06 锚定）：物理缓冲 =
        #   起始行锚定时「文档底部所在物理行数」``min(start_row+n-1, height)``
        #   （未贴底时 ``_effective_offset`` 恒为 ``1-start_row``；触及屏幕
        #   底部后由 ``_sync_start_row_anchor`` 转底部锚定）；回退底部锚定时
        #   ``max(height, n)``（文档底部贴屏幕底部）。height=0 时保持原文档
        #   坐标行为。
        if self._height > 0:
            if self._start_row is not None:
                self._buf_h = min(self._start_row + n - 1, self._height)
            else:
                self._buf_h = max(self._height, n)
            self._top_aligned = False
            # ★ 2026-10-22（启动期渲染错位修复）：记录本会话写入范围顶部
            #   （0-based）——文档起始物理行 = ``_buf_h - n``（底部锚定贴底时
            #   为 height-n；起始行锚定未滚动时为 start_row-1）。清屏路径
            #   （resize）屏幕已干净，重置为 0，允许清理整屏残留。
            self._painted_top = 0 if clear_first else max(0, self._buf_h - n)
        else:
            self._buf_h = n
            self._top_aligned = True
            # height=0（文档坐标模型）：无屏幕约束，整个文档都属会话区域。
            self._painted_top = 0
        self._cursor_row = self._bottom_row(n)
        self._flush_frame_buf(buf)

    # ── 光标 ─────────────────────────────────────────

    def _hide_cursor_for_write(self) -> None:
        """写帧到终端前隐藏光标（DECTCEM）。

        ★ 2026-10-22（用户需求：TUI 渲染时先隐藏光标，渲染完再按是否有输入
        界面决定是否显示）：帧写入过程会大量移动光标（``cursor_up`` /
        ``cursor_down`` / ``cursor_forward``、逐区间重写、滚屏），可见光标会
        随写入在屏幕上跳动/闪烁。所有**实际写终端**的渲染路径在写前调用本
        方法；帧写完后由会话 ``_position_cursor`` 收口：
          - 有输入区 → 先 ``place_cursor`` 移到输入位置，再 ``set_cursor_visible
            (True)``；
          - 无输入区（模态全屏视图）→ 保持隐藏。

        状态跟踪（``_cursor_visible``）保证已隐藏时零输出；帧内容完全一致
        （diff 为空、本帧不写终端）时调用方不调用本方法，避免空闲帧每帧写
        显隐序列造成光标抖动。
        """
        self.set_cursor_visible(False)

    def set_cursor_visible(self, visible: bool) -> None:
        """设置终端光标可见性（DECTCEM；状态跟踪，仅变化时输出序列）。

        ★ 2026-08-17（用户需求：轨迹 Trace 不显示光标；2026-08-17 通用化）：
        **模态全屏视图**（``model.fullscreen`` 非空——App 整屏渲染全屏视图
        组件、无输入区）下 ``_position_cursor`` 找不到 input fiber → 隐藏
        光标（避免光标停留在残留位置闪烁）；正常模式（找到 input fiber）定位
        到输入光标后显示。渲染循环每帧调用本方法——内部 ``_cursor_visible``
        状态跟踪保证**仅在显隐切换时**写转义序列（不变帧零输出，防每帧重复写）。

        ★ 2026-10-22（用户需求）：帧写入前由 ``_hide_cursor_for_write`` 统一
        隐藏；显示只在会话定位到输入光标之后发生（不再提前显示）。

        Args:
            visible: True=显示光标；False=隐藏光标。
        """
        visible = bool(visible)
        if self._cursor_visible == visible:
            return
        self._cursor_visible = visible
        try:
            self._stream.write(cursor_show() if visible else cursor_hide())
            self._stream.flush()
        except (OSError, ValueError):
            # ★ P3（review）：光标序列写失败留痕（不裸吞）——仅记 debug。
            _logger.debug(
                "set_cursor_visible 写入失败 visible=%s", visible, exc_info=True,
            )

    def place_cursor(self, row: int, col: int) -> None:
        """将光标放置到文档坐标 (row, col)（1-based）。

        从当前光标位置（_cursor_row）相对移动，避免绝对坐标在滚动终端中
        失效。raw 模式下先 \r 归位列 1 再前进到 col。

        height>0 时 row 为文档坐标，先换算为屏幕坐标（钳制到可见区）——
        目标行位于可见区上方（滚动区）时钳制到屏幕顶部（不可达时无法
        放置，钳制安全侧）。
        """
        # ★ P3-1（review 方向）：col 防御钳制下限——修复前对 col 无钳制，
        #   col<=0 时 ``cursor_forward(col-1)`` 输出非法 ANSI（``\033[0C`` /
        #   负数列）污染终端。钳制 ``col >= 1`` 保证归位后至少原地（无前进
        #   序列）。
        #   ★ P3（review 2026-08-18）：col 防御钳制上限——``set_width`` 传播
        #   终端宽度后渲染器内钳 ``col <= width``，未来直接调用方遗漏钳制
        #   时光标不越出终端右边界（宽度未知（0）时不钳制，既有行为不变；
        #   ``_cursor.position_cursor`` 调用方的 ``min(..., width)`` 钳制
        #   幂等保留）。
        col = max(1, col)
        if self._width > 0:
            col = min(col, self._width)
        doc_h = self._prev.height if self._prev is not None else row
        # ★ 用 `_effective_offset`（含物理缓冲漂移，可为负）而非 `_screen_offset`
        #   （max(0,...)）——漂移时文档物理位置可能偏下，max 偏移会把光标放偏上。
        offset = self._effective_offset(doc_h)
        target = row - offset
        # ★ M2（2026-08-15）：负 offset 目标行按物理缓冲边界钳制——底部对齐
        #   + 文档偏下（``_buf_h <= _height`` 且 ``doc_h+1 < _buf_h``，offset
        #   为负）时 ``target = row + |offset|`` 偏大：输入行（row 可达
        #   doc_h+1）target 恰好 = ``_buf_h``（物理缓冲末行）正确；但 row >
        #   doc_h+1（补全弹窗/多行输入高度与帧行数时序不一致等状态组合）时
        #   target > ``_buf_h``，直接 ``_clamp`` 会把可达输入行钳到屏幕底部
        #   空白区——越界方向确认：offset 负 → target 偏大 → >height 钳到底
        #   （<1 钳到顶不发生）。按物理缓冲上界钳制：文档行物理位置不可能
        #   超过物理缓冲末行（``_buf_h``），目标行不超物理缓冲；随后
        #   ``_clamp`` 保证 [1, height]。正常路径（row <= doc_h+1）target <=
        #   ``_buf_h``，本钳制不生效（零回归）。仅限目标行计算，不触碰
        #   ``_effective_offset`` 漂移模型本身（BUG-64/65/66/68/76 语义保留）。
        if offset < 0 and self._buf_h > 0:
            target = max(1, min(target, self._buf_h))
        target = self._clamp(target)
        # ★ 2026-10-23（空闲不写终端）：目标位置与上次相同且其后没有帧写入
        #   （render() 已失效缓存）→ 直接返回，不写 \r/flush。
        if self._placed_cursor == (target, col):
            self._cursor_row = target
            return
        current_row = self._cursor_row
        n_up = current_row - target
        if n_up > 0:
            self._stream.write(cursor_up(n_up))
        elif n_up < 0:
            self._stream.write(cursor_down(-n_up))
        self._stream.write("\r")
        if col > 1:
            self._stream.write(cursor_forward(col - 1))
        self._stream.flush()
        self._cursor_row = target
        self._placed_cursor = (target, col)

    def set_line_callback(self, callback) -> None:
        """设置新增行回调（输出历史跟踪）。"""
        self._line_callback = callback

    # ── 公开访问器 ───────────────────────────────────

    @property
    def stream(self):
        """底层输出流（useStdout 等外部消费方的公开访问入口）。

        ★ P3（review）：暴露公开 property——修复前 ``session.py`` 等跨对象
        直读私有成员 ``renderer._stream``。
        """
        return self._stream

    def goto_bottom(self, rows: int) -> None:
        """将物理光标绝对定位到第 rows 行第 1 列（suspend 后弹窗起点）。

        ★ P3（review）：公开 API——修复前 ``session.suspend`` 直写私有成员
        ``renderer._stream``（跨对象私有访问）。

        ★ 2026-10-20：同步光标记账——本方法移动了**真实**物理光标，若
        ``_cursor_row`` 不跟随，后续帧的相对移动（cursor_up/down）会从
        错误行起算，整帧写入位置偏移。修复前 ``suspend()`` 把记账重置为 1，
        与 ``goto_bottom(h)`` 的实际位置（屏幕底部）不一致，是 resume 后
        重复渲染/错位的直接原因之一。

        Args:
            rows: 终端行号（1-based；内部钳制 >= 1）。
        """
        target = max(1, int(rows))
        self._stream.write(cursor_goto(target, 1))
        self._stream.flush()
        self._cursor_row = max(1, min(target, self._height)) if self._height > 0 else target

    def full_clear(self) -> None:
        """全帧清屏（Claude TUI parity 步骤 3.1，Ctrl+L 清屏）。

        写入 ``clear_screen()``（``\\033[2J\\033[H``）并软重置 prev/光标——
        下一帧走增量 diff（与空帧比较 = 所有行变化 → 逐行写入，不清屏）。
        scrollback 历史保留（终端自身行为）。

        ★ 2026-10-22（用户需求）：清屏也是写屏——写 ED 前先隐藏光标（见
        ``_hide_cursor_for_write``），清屏后光标不残留在 (1,1) 闪烁；下一帧
        写完后由会话按「是否有输入区」定位并显示。
        """
        self._hide_cursor_for_write()
        try:
            # ★ 2026-10-23：清屏同时重置滚动区域（局部滚动异常残留时，后续
            #   换行不被残留区域限制；未设置过时无副作用）。
            self._stream.write(reset_scroll_region())
            self._stream.write(clear_screen())
            self._stream.flush()
        except Exception:
            _logger.debug("full_clear 写入异常", exc_info=True)
        # 软重置：空帧 → 增量 diff（仅 resize 走全量 _write_full）
        self._prev = Frame([])
        self._cursor_row = 1  # clear_screen 后光标在 (1,1)
        # ★ 无末尾空行模型（2026-08-15）：软重置后物理缓冲 = 0（无虚拟空行）。
        self._buf_h = 0
        self._top_aligned = True
        # ★ 2026-10-22：clear_screen 已清屏——重置会话写入范围（屏幕干净，
        #   下一帧可安全清理整屏）。
        self._painted_top = 0
        # ★ 2026-10-06：clear_screen 归位光标到 (1,1)、坐标系重置——起始行
        #   锚定失效（下一帧文档自屏幕第 1 行重建，既有「顶部对齐 + buf_h=0」
        #   软重置语义即此）。
        self._start_row = None

    def emit_exit_note(self, text: str) -> None:
        """在文档最后一行下方输出一行退出提示（保留 TUI、不清屏）。

        ★ 2026-10-20（用户需求：退出时在所有 TUI 内容下面一行打印
        「再见 / 恢复: python chat.py --load …」，且不再清屏）：修复前
        ``InkSession.prepare_exit`` 调 ``full_clear()``（ED 2）清屏——整屏
        TUI 被抹掉，「再见 / 恢复」提示随之消失，只剩 ``Goodbye!`` 落在
        屏幕左上角。

        实现：
          - 最终帧由 ``place_cursor`` 停在输入行（文档中部），先按渲染器
            坐标模型移动到**文档最后一行的屏幕行**（``_bottom_row``，含
            屏幕/物理缓冲偏移换算与钳制），再写 ``\\r\\n``——提示行因此恰好
            位于全部 TUI 内容的下方一行；文档贴屏幕底部时由该换行触发终端
            自然上滚（历史进 scrollback，不清屏）；
          - 提示文本后再写一个 ``\\r\\n``，让随后打印的 shell 提示符另起
            一行（不与本行粘连）；
          - 全程不写清屏序列、不重建帧；调用方须保证渲染线程已停止
            （``InkSession.stop`` 在 join 之后调用）。

        Args:
            text: 提示文本（可含 ANSI 样式；本方法不添加前缀换行）。
        """
        if not text:
            return
        buf = io.StringIO()
        doc_h = self._prev.height if self._prev is not None else 0
        if doc_h > 0 and self._height > 0:
            target = self._bottom_row(doc_h)
            current = self._cursor_row
            if current > target:
                buf.write(cursor_up(current - target))
            elif current < target:
                buf.write(cursor_down(target - current))
            self._cursor_row = target
        buf.write("\r\n")
        buf.write(text)
        buf.write("\r\n")
        try:
            self._flush_frame_buf(buf)
            self._stream.flush()
        except (OSError, ValueError):
            _logger.debug("emit_exit_note 写入失败", exc_info=True)
        # 光标已落在提示行下方：记账跟随实际位置（退出路径不再渲染，仅为
        # 状态一致性；height=0 无屏幕约束时直接 +2）。
        if self._height > 0:
            self._cursor_row = self._clamp(self._cursor_row + 2)
        else:
            self._cursor_row += 2

    # ── 生命周期 ─────────────────────────────────────

    def suspend(self) -> None:
        """暂停：刷新输出，**保留**帧/物理缓冲/光标记账。

        ★ 2026-10-20（resume 重复渲染修复）：修复前这里是软重置（
        ``_prev=Frame([])``、``_cursor_row=1``、``_buf_h=0``）——suspend 期间
        交互工具（user_select / 外部编辑器）经 ``request_bottom_redraw``
        触发的**同步渲染**、以及 resume 后的首帧都会被当成「空帧 → 整篇文档
        首次写入」：从物理光标所在位置（终端底部，session.suspend 已
        goto_bottom）开始逐行追加整篇文档。文档矮于屏幕时旧帧只被推出
        ``doc_h-1`` 行 → 屏幕上方残留半份旧 UI，屏幕上同时出现两份 UI；
        ``_buf_h`` 记账与实际物理缓冲永久漂移，scrollback 也被整篇重复写入。

        修复后：suspend 只 flush；``_prev`` 保持最后一帧、``_buf_h`` /
        ``_top_aligned`` / ``_start_row`` 保持真实布局——suspend 期间的同步
        渲染与 resume 后的首帧一律走增量 diff（只重写变化行）。
        终端内容若被交互工具改写，差异行会被下一帧 diff 重写覆盖。
        """
        try:
            self._stream.flush()
        except Exception:
            # ★ P3 修复（review 方向）：裸吞异常补日志（exc_info 保留栈）。
            _logger.debug("suspend 刷出异常", exc_info=True)

    def reset(self, full: bool = False) -> None:
        """重置渲染状态。

        Args:
            full: True = 完全重置（``_prev=None``，下次 render 全量写入），
                  **仅 resize 使用**；下一次全量写入前先清屏（清掉旧帧残留，
                  见 ``_write_full``）；False = 软重置（``_prev=空帧``，下次
                  render 走增量 diff），用于 Ctrl+L 等非 resize 场景。
        """
        if full:
            self._prev = None
            self._cursor_row = 0
            self._buf_h = 0
            self._top_aligned = True
            # ★ 2026-10-22：下一次全量写入先清屏（``_clear_before_full``）——
            #   屏幕将变干净，重置会话写入范围。
            self._painted_top = 0
            # ★ 2026-10-06：起始行锚定仅服务「会话启动首帧」——resize 全量
            #   重写退回底部锚定（屏幕布局已变，旧起始行不再适用）。
            self._start_row = None
            # ★ 2026-10-20：resize 全量重写前清屏（旧帧残留修复）。
            self._clear_before_full = True
        else:
            # 空帧 → 增量 diff（与空帧比较 = 所有行都变化 → 逐行写入，不清屏）
            self._prev = Frame([])
            self._cursor_row = 1
            # ★ 无末尾空行模型（2026-08-15）：软重置后物理缓冲 = 0（无虚拟空行）。
            self._buf_h = 0
            self._top_aligned = True
            # ★ 2026-10-22：软重置**不清屏**，保留会话写入范围（保守：不把
            #   首屏之前用户历史纳入可清理区域）。
            # ★ 2026-10-06：软重置（resume/清屏复用）退回既有锚定语义——
            #   起始行锚定仅在「会话启动首帧 + 未发生复位」期间有效。
            self._start_row = None

    def flush(self) -> None:
        """刷出底层输出。"""
        try:
            self._stream.flush()
        except Exception:
            # ★ P3 修复（review 方向）：裸吞异常补日志（exc_info 保留栈）。
            _logger.debug("flush 刷出异常", exc_info=True)

    # ── 测试辅助 ─────────────────────────────────────

    @property
    def prev_frame(self) -> Frame | None:
        return self._prev

    @property
    def cursor_row(self) -> int:
        return self._cursor_row


__all__ = ["InkRenderer", "RendererStateError"]
