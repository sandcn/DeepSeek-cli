"""InkRenderer — 非全屏（随内容流动）帧差异渲染器。

核心算法（Ink 默认模型，非 DECSTBM）：
  1. 渲染组件树 → 全量 Frame（行列表，每行 = StyledRun 序列）。
  2. 行级 diff：收集 prev 与 new 的**差异区间**（[start, end) 行号，
     连续差异行合并；身份短路——同 Line 对象恒相等）。
  3. 应用：从当前光标定位到各差异区间逐区间重写；仅重写变化的行区间，
     静态/未变行（含 committed 前缀）**永不重写**。高度差（新增/删除行）
     单独处理：增长追加 delta 新行、缩短清除残留行。
  4. 输入光标：渲染后按 ``place_cursor`` 放置。

PERF-4 / 增量细化：
  - 平移快路径：``new_h > prev_h`` 且尾部内容整体下移且相同（仅新增 delta 行
    位于 ``prev_h`` 起始）时，跳过重写相同尾部，仅写新增 delta 行。
  - **差异区间重写**（取代旧的"首差异行→末尾全重写"）：头部动画（如标题栏
    呼吸色时间桶变化）只改动首行时仅重写首行，不再引发整帧重写——大文档下
    每帧输出从 O(文档) 降为 O(变更行)。
  - 单帧重写行数上限 ``_MAX_REWRITE_ROWS``：实际待重写行数（差异区间行数 +
    高度差行数）超限时**不再降级为全量 clear + 全量重建**——增量路径本就只写
    变化行且无 clear_screen（闪烁），超限仅记 warning（阈值保留防静默病态大
    重写）。满足「除终端 resize 外均增量渲染」。

不切换备用屏幕、不用 DECSTBM——内容自然流入 scrollback。

**非 resize 均增量（需求）**：全量写入（``_write_full``）**仅**出现在首帧
（``_prev is None``，无前一帧可 diff）与 ``reset(full=True)``（resize 后
``_render_frame`` 消费 ``_resize_pending`` 置位）；
**其余所有帧一律走行级 diff 增量路径**——包括 Ctrl+L 清屏后、suspend/resume
（交互工具独占终端）后、文档高于屏幕的缩短/增长/等高
（``_rewrite_drifted``/``_grow_drifted`` 物理映射）与缩短/增长/等高**进入屏幕内**
（文档底部对齐可见区底部，负偏移模型 ``_effective_offset`` 供 place_cursor）。
说明：
  1. ``_MAX_REWRITE_ROWS`` 超限降级已消除（原 clear + 全量重建 → 现仍增量，
     超限仅记 warning）；
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

from src.tui._screen import (
    cursor_up, cursor_down, clear_line, cursor_forward, clear_screen,
    cursor_hide, cursor_show,
)
from .output import Frame
from .diff import first_diff_line

_logger = logging.getLogger(__name__)

# 行尾清除（防止旧行尾部残留）
_CLEAR_EOL = "\033[K"

# PERF-4：单帧重写行数上限（防病态大重写冻结 UI）
_MAX_REWRITE_ROWS = 200


class InkRenderer:
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
        # ★ 输出历史已回调行数（BUG-65 修复）：``_write_full``（首帧/reset
        #   (full=True) 全量重写）与增量增长只回调**新增**行——resize 后
        #   ``reset(full=True)`` 全量重写文档若从 0 行重新回调会把整篇文档
        #   重复写入输出历史（scrollback 记录翻倍/多倍）。本字段跟踪已回调
        #   行数：``_write_full`` 经 ``emit_start = min(_history_lines, height)``
        #   跳过已记录行；``_emit_new_lines`` 更新为 max。软重置
        #   （reset(full=False)/suspend/full_clear）不清零——历史已记录的行
        #   不因 TUI 内部重绘重复回调。
        self._history_lines: int = 0
        # ★ 光标可见性状态（2026-08-17 用户需求：轨迹 Trace 不显示光标）：
        #   None=未知（终端默认可见；首次 set 显式对齐实际状态）；True/False
        #   = 上次设置值。``set_cursor_visible`` 仅变化时输出 DECTCEM 序列
        #   （每帧 _position_cursor 调用——状态跟踪避免每帧重复写转义码）。
        self._cursor_visible: bool | None = None

    # ── 屏幕坐标（height>0 时文档高于屏幕的滚动偏移处理） ──────────

    def set_height(self, height: int) -> None:
        """设置终端屏幕高度（resize 时更新）。

        高度变化后钳制光标行到新屏幕范围内（物理光标不会越出屏幕底部）。
        """
        self._height = int(height) if height else 0
        if self._height > 0:
            self._cursor_row = max(1, min(self._cursor_row, self._height))

    def _screen_offset(self, doc_h: int) -> int:
        """文档高于屏幕时被滚出可见区上方的行数（屏幕坐标偏移）。

        ★ 无末尾空行模型（2026-08-15）：按「理想物理缓冲 = doc_h」推导
        ``max(0, doc_h-height)``——文档行 ``row``（1-based）的物理屏幕位置 =
        ``row - (doc_h-height)``（推导：物理行 = 文档行 + drift，drift 恰好
        抵消物理偏移 ``_buf_h-height``）。
        因此**所有文档坐标→屏幕坐标换算用理想偏移**（``_screen_offset``/
        ``_to_screen``/``_bottom_row``），而非 ``_buf_h`` 物理偏移——否则漂移
        状态下 place_cursor 偏上 drift 行（输入光标错位）。物理偏移
        （``_buf_h - height``）仅用于漂移方法内部可见区定位。

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
        供 ``place_cursor`` 使用（每帧渲染后输入光标定位必须与物理位置一致）。

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

        返回可能 <1（位于可见区上方，滚动区）或 >height（下方）——
        调用方据此判断可达性；height=0 时恒等返回（文档坐标即屏幕坐标）。

        Args:
            buffer_row: 文档 1-based 行号。
            doc_h: 文档总行数。

        Returns:
            屏幕 1-based 行号（未钳制）。
        """
        return buffer_row - self._screen_offset(doc_h)

    def _clamp(self, row: int) -> int:
        """将屏幕行号钳制到 [1, height]（height=0 时原样返回）。"""
        if self._height <= 0:
            return row
        return max(1, min(row, self._height))

    def _bottom_row(self, doc_h: int) -> int:
        """文档写入后物理光标所在屏幕行（1-based，已钳制）。

        文档底部（doc_h，1-based）物理屏幕位置 = 理想偏移推导
        （``_to_screen`` 语义）——无漂移时物理缓冲 = doc_h，即缓冲末尾。
        ★ 无末尾空行模型（2026-08-15）：文档最后一行即缓冲末尾（无
        doc_h+1 末尾空行），底部 = doc_h 而非 doc_h+1。
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

    def _write_row(self, buf, frame, idx: int, last_idx: int) -> bool:
        """写一行（\r 归位 + EL 清行 + 内容 + \r 归位）；非段末行写 \n 换行。

        **当前语义（必须遵守）**：
          - 行写入序列恒为 ``\r`` + ``\033[K``（清行）+ 行内容 + ``\r``；
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
        """调试期不变量断言（P3-14 review 方向）：``_prev/_buf_h/_top_aligned``
        三元组一致性校验。

        辅助未来重构校验（``assert`` 在 ``python -O`` 下剥离，常规运行每帧
        O(1) 开销可忽略）。不变量：
          - ``_buf_h >= 0``（物理缓冲行数非负）；
          - ``_top_aligned == False``（底部对齐）仅在**已渲染**状态合法——
            ``_prev`` 非 None 且 ``_buf_h > 0``（底部对齐仅由
            ``_grow_drifted``/``_rewrite_drifted`` 渲染路径置位，二者均以
            ``_prev = frame`` 收尾；首帧/软重置/全量写入恒置 True）；
          - ``_prev is None`` ⇔ 全量写入待触发（首帧 / reset(full=True)），
            此时 ``_buf_h == 0`` 且 ``_top_aligned == True``。
        """
        assert self._buf_h >= 0, f"_buf_h 非负不变量被破坏: {self._buf_h}"
        if not self._top_aligned:
            assert self._prev is not None and self._buf_h > 0, (
                "底部对齐（_top_aligned=False）仅在已渲染状态合法: "
                f"prev={self._prev is not None} buf_h={self._buf_h}"
            )
        if self._prev is None:
            assert self._buf_h == 0 and self._top_aligned, (
                "未渲染状态（_prev is None）应满足 buf_h==0 且 top_aligned: "
                f"buf_h={self._buf_h} top_aligned={self._top_aligned}"
            )

    def render(self, frame: Frame) -> None:
        """渲染新帧（最小差异写入）。"""
        self._assert_renderer_invariants()
        if self._prev is None:
            # ★ BUG-65：首帧（_history_lines==0）全量回调；reset(full=True)
            #   （resize 后全量重写）只回调**新增**行（跳过已记录历史）——
            #   修复前 reset 后从 0 行全量回调，整篇文档重复写入输出历史。
            emit_start = min(self._history_lines, frame.height)
            self._write_full(frame, emit_start=emit_start)
            self._prev = frame
            self._stream.flush()
            return

        prev_h = self._prev.height
        new_h = frame.height
        i = first_diff_line(self._prev, frame)
        if i < 0:
            # 帧完全一致：仅刷新光标位置（调用方自行 place_cursor）
            return

        delta = new_h - prev_h

        # ★ 有漂移增长（缩短后物理缓冲漂移 `_buf_h > prev_h`）：offset 不再随
        #   doc_h 变化，平移快路径/head_runs/位移区均假设「物理行号 = 文档行号」
        #   （无漂移）会写错偏移移动行 → 走 `_grow_drifted`（物理映射重写 +
        #   追加新行）。须放在平移快路径之前（有漂移时纯追加也会偏移错位）。
        #   增长进入屏幕内（new_h <= height）同样走 `_grow_drifted`（可见区
        #   顶部 doc_idx<0 空行区清空）——不重建、不清屏。
        #   ★ 无末尾空行模型（2026-08-15）：无漂移时 ``_buf_h == prev_h``
        #   （物理缓冲 = 文档行数），判定条件从 ``_buf_h > prev_h+1`` 收紧
        #   为 ``_buf_h > prev_h``。
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
                buf.write(_CLEAR_EOL)
                buf.write(frame.render_line(line_idx))
                # ★ 满宽行 wrap 修复（同 _diff_runs 写行循环）：行内容填满宽度时
                #   \r 归位避免 \n 触发 wraparound 额外下移。最后一行不写 \n
                #   （无末尾空行模型：光标停在文档最后一行）。
                buf.write("\r")
            # ★ 无末尾空行模型（2026-08-15）：物理缓冲 = 文档行数（漂移时
            #   保持 max——物理行不删行）；height=0 无约束同语义。
            self._buf_h = max(self._buf_h, new_h)
            self._emit_new_lines(frame, prev_h, new_h)
            self._cursor_row = self._bottom_row(new_h)
            self._prev = frame
            self._stream.write(buf.getvalue())
            self._stream.flush()
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
            # ★ PERF-4 单帧重写行数上限：超限时降级（避免病态大重写冻结 UI）。
            #   行数 = 实际待重写行数（delta==0 时为差异区间行数）。修复前用
            #   ``new_h - i`` 高估——头部动画场景 i=0 但仅首行差异会误触发
            #   降级，引发全屏闪烁。
            rewrite_count = sum(end - start for start, end in runs)
        else:
            runs = []
            # ★ PERF-4 单帧重写行数上限（delta!=0）：头部差异区间 + 位移区 +
            #   高度变化（delta<0 时清除残留行）——病态大重写降级防冻结 UI。
            rewrite_count = (
                sum(end - start for start, end in head_runs)
                + max(0, new_h - min(shift_start, new_h))
                + max(0, prev_h - new_h)
            )
        if rewrite_count > _MAX_REWRITE_ROWS:
            # ★ 非 resize 增量：超限不再降级为全量 clear + 全量重建（闪烁）。
            #   增量路径本就只写变化行（无 clear_screen），输出量 ≤ 全量重建
            #   且无闪烁；超限仅记 warning（阈值保留防静默病态大重写）。
            _logger.warning(
                "单帧重写行数 %d 超上限 %d，仍按增量路径重写（不清屏重建）",
                rewrite_count, _MAX_REWRITE_ROWS,
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
            # 增量缩短（文档仍高于屏幕或进入屏幕内）：重写可见区变化行 +
            # 清残留/清空行区，不清屏重建（物理缓冲 _buf_h 保持）。
            self._rewrite_drifted(frame, prev_h, new_h, first_diff=i)
            self._prev = frame
            return

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
            # 增长：回调新增行（输出历史跟踪；重写循环已写出这些行）。
            # ★ 无末尾空行模型（2026-08-15）：位移区路径无漂移时物理缓冲 =
            #   新文档行数（原公式含末尾空行 +1 语义已去除）；漂移场景由
            #   ``_grow_drifted`` 处理（本路径仅在 ``_buf_h <= prev_h`` 到达）。
            if delta > 0:
                self._buf_h = max(self._buf_h, new_h)
                self._emit_new_lines(frame, prev_h, new_h)

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
        self._stream.write(buf.getvalue())
        self._stream.flush()

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
            grow_rows = max(0, new_h - buf_h0)
            buf_h1 = buf_h0 + grow_rows
            drift1 = buf_h1 - new_h   # 增长后漂移
        prev = self._prev
        rewrites: list[tuple[int, int]] = []
        for q in range(buf_top0, buf_h0):
            old_idx = q - drift0
            doc_idx = q - drift1
            old_line = prev.lines[old_idx] if 0 <= old_idx < prev_h else None
            if doc_idx < 0:
                # 文档上方空行区（增长时文档进入屏幕内）：物理行须为空。
                # ★ BUG-76 同源：漂移时 old_idx 越界（残留自更早帧）→ 保守清除。
                if old_line is not None or old_idx >= prev_h:
                    rewrites.append((q, -1))
                continue
            if doc_idx >= new_h:
                # 残留（越界；无末尾空行模型下 doc_idx == new_h 已越界）：
                # 物理行须为空。★ BUG-76 同源。
                if old_line is not None or old_idx >= prev_h:
                    rewrites.append((q, -1))
                continue
            new_line = frame.lines[doc_idx]
            if old_line is not new_line and (
                old_line is None or old_line.runs != new_line.runs
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
                #   仍只清行（不写内容）。
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
        # ★ BUG-65：统一经 _emit_new_lines 回调新增行（维护 _history_lines）
        self._emit_new_lines(frame, prev_h, new_h)
        self._stream.write(buf.getvalue())
        self._stream.flush()

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
        调用兼容）。
        """
        from ._frame_diff import _diff_runs
        return _diff_runs(prev, frame, n, start)

    def _is_tail_shifted(self, prev: Frame, frame: Frame, i: int, delta: int) -> bool:
        """检测尾部内容是否只是整体下移（仅新增 delta 行）。

        ★ 模块边界（2026-08-05）：实现已迁至 ``_frame_diff.py``（纯函数）；
        本方法为薄包装（测试实例调用兼容）。
        """
        from ._frame_diff import _is_tail_shifted
        return _is_tail_shifted(prev, frame, i, delta)

    def _find_tail_anchor(self, prev: Frame, frame: Frame, delta: int) -> int:
        """从文档末尾向前找尾部位移锚点（方向4 优化）。

        ★ 模块边界（2026-08-05）：实现已迁至 ``_frame_diff.py``（纯函数）；
        本方法为薄包装（测试实例调用兼容）。
        """
        from ._frame_diff import _find_tail_anchor
        return _find_tail_anchor(prev, frame, delta)

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
        prev = self._prev
        # ★ BUG-68：doc 缩短后滚动区有内容变化（首差异行 <= buf_top，滚动区
        #   不可达不重写 → doc 中部行永久陈旧，如 6→5→4 行序列中 'p3' 丢失）
        #   时切换底部对齐，让 doc 内容贴可见区底部显示。仅尾部删除（首差异
        #   行 > buf_top）保持顶部对齐（补全弹窗闪烁修复契约，
        #   test_renderer_screen 锁定）。
        if self._top_aligned:
            old_drift = 0
            if new_h > height:
                if first_diff is not None and first_diff <= buf_top:
                    self._top_aligned = False
                    drift = buf_h - new_h
                else:
                    drift = 0  # 保持顶部对齐（doc 仍高于屏幕）
            else:
                # doc 进入屏幕内 → 切换为底部对齐（完整文档可见契约）
                self._top_aligned = False
                drift = buf_h - new_h
        else:
            # ★ 无末尾空行模型（2026-08-15）：drift 公式去掉末尾空行 +1
            #   项（物理行号 = 文档行号，无 doc_h+1 空行）。
            old_drift = buf_h - prev_h
            drift = buf_h - new_h
        # 待重写项：(物理行, 新文档行)；doc_idx==-1 表示清除残留/空行。
        rewrites: list[tuple[int, int]] = []
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
                if old_line is not None or old_idx >= prev_h:
                    rewrites.append((q, -1))
                continue
            if doc_idx >= new_h:
                # 残留（越界；无末尾空行模型下 doc_idx == new_h 已越界）：
                # 物理行须为空。★ BUG-76 同源：漂移时 old_idx 越界（物理行
                # 内容残留自更早帧）→ 保守清除，防缩短后旧行残留。
                if old_line is not None or old_idx >= prev_h:
                    rewrites.append((q, -1))
                continue
            new_line = frame.lines[doc_idx]
            if old_line is not new_line and (
                old_line is None or old_line.runs != new_line.runs
            ):
                rewrites.append((q, doc_idx))
        if not rewrites:
            # 可见区无变化：仅更新光标（物理缓冲末行）与 prev。
            self._cursor_row = max(1, min(buf_h, height))
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
            #   清行（不写内容）。
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
        self._stream.write(buf.getvalue())
        self._stream.flush()

    def _write_full(self, frame: Frame, emit_start: int = 0) -> None:
        """首帧/重置后：全量写入文档。

        raw 终端模式下 \n 不归位列 1，每行前缀 \r（与 OutputAdapter 的
        CRLF 语义一致）。方向1：整帧缓冲单次 write+flush（免逐行 flush
        闪烁/撕裂）。

        Args:
            frame: 目标帧。
            emit_start: 新增行回调起始行（仅回调 ``[emit_start, height)``；
                首帧默认 0=全量回调；降级重建传上一帧高度，避免重复回调已有行）。
        """
        if not frame.lines:
            # 方向1 步骤3（首帧空帧光标）：空帧也更新 _cursor_row（=1）
            # ——修复前空帧不置位，下一帧 ``n_move`` 产生多余光标移动。
            # ★ 无末尾空行模型（2026-08-15）：空帧 _buf_h 置 0（无虚拟
            #   末尾空行）——空帧后增长首行原地写（终端光标在行 1）。
            self._buf_h = 0
            self._top_aligned = True
            self._cursor_row = self._bottom_row(frame.height)
            return
        buf = io.StringIO()
        n = len(frame.lines)
        for idx, line in enumerate(frame.lines):
            buf.write("\r")
            buf.write(line.render())
            # ★ 满宽行 wrap 修复（同 _diff_runs 写行循环）：行内容填满宽度时
            #   \r 归位避免 \n 触发 wraparound 额外下移。
            buf.write("\r")
            # ★ 无末尾空行模型（2026-08-15）：最后一行不写 \n——光标停在
            #   文档最后一行（模式行），不产生末尾空行（修复「满屏时模式行
            #   下方多一行空行」：doc_h == height 时首行不再被滚动挤出）。
            if idx < n - 1:
                buf.write("\n")
        # 物理缓冲行数 = 文档行数（无末尾空行）
        self._buf_h = frame.height
        self._top_aligned = True
        # ★ P3-13 设计说明（review 方向）：行回调（``_emit_new_lines``）在
        #   终端写入（``stream.write``）**之前**调用——设计取舍：回调先于
        #   终端写入，保证输出历史（scrollback 落盘）在任何终端输出之前记录
        #   （写入/刷新异常时历史不缺失）。回调异常已被 ``_emit_new_lines``
        #   内部吞掉仅记日志，不影响终端写入。保持既有顺序（改动顺序会改变
        #   回调与终端写入的时序语义，无收益）。
        self._emit_new_lines(frame, emit_start, frame.height)
        self._cursor_row = self._bottom_row(frame.height)
        self._stream.write(buf.getvalue())
        self._stream.flush()

    def _emit_new_lines(self, frame: Frame, start: int, end: int) -> None:
        """回调新增行（输出历史跟踪），并更新已回调行数。

        ``_history_lines`` 记录已通过 line_callback 回调的行数（只增不减）——
        ★ BUG-65：回调起点钳制到 ``max(start, _history_lines)``——软重置
        （reset(full=False)/suspend/full_clear 后空帧 diff）与 reset(full=True)
        （resize 后全量重写）重新渲染同一文档时仅回调**新增**行（行号 >=
        ``_history_lines``）；修复前全量回调导致整篇文档重复写入输出历史
        （scrollback 记录翻倍）。
        """
        start = max(start, self._history_lines)
        if end <= start:
            return
        self._history_lines = max(self._history_lines, end)
        if self._line_callback is None:
            return
        try:
            for idx in range(start, end):
                self._line_callback(frame.render_line(idx) + "\n")
        except Exception:
            # ★ P3 修复（review 方向）：裸吞异常补日志（exc_info 保留栈）。
            _logger.debug("_emit_new_lines 行回调异常", exc_info=True)

    # ── 光标 ─────────────────────────────────────────

    def set_cursor_visible(self, visible: bool) -> None:
        """设置终端光标可见性（DECTCEM；状态跟踪，仅变化时输出序列）。

        ★ 2026-08-17（用户需求：轨迹 Trace 不显示光标；2026-08-17 通用化）：
        **模态全屏视图**（``model.fullscreen`` 非空——App 整屏渲染全屏视图
        组件、无输入区）下 ``_position_cursor`` 找不到 input fiber → 隐藏
        光标（避免光标停留在残留位置闪烁）；正常模式（找到 input fiber）显示
        光标并定位。渲染循环每帧调用本方法——内部 ``_cursor_visible`` 状态
        跟踪保证**仅在显隐切换时**写转义序列（不变帧零输出，防每帧重复写）。

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
            pass

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
        #   序列）。上限钳制（``min(col, width)``）渲染器无法实现——本类
        #   无终端宽度状态（仅高度），列上限由调用方负责（``_cursor.
        #   position_cursor`` 已 ``min(..., width)`` 钳到终端宽度）。
        col = max(1, col)
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

    def set_line_callback(self, callback) -> None:
        """设置新增行回调（输出历史跟踪）。"""
        self._line_callback = callback

    def full_clear(self) -> None:
        """全帧清屏（Claude TUI parity 步骤 3.1，Ctrl+L 清屏）。

        写入 ``clear_screen()``（``\\033[2J\\033[H``）并软重置 prev/光标——
        下一帧走增量 diff（与空帧比较 = 所有行变化 → 逐行写入，不清屏）。
        scrollback 历史保留（终端自身行为）。
        """
        try:
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

    # ── 生命周期 ─────────────────────────────────────

    def suspend(self) -> None:
        """暂停：重置渲染状态（live 区已作为普通行提交到 scrollback）。

        非全屏模型下文档行都是真实 scrollback 行，无需额外提交；
        软重置（空帧 prev）使恢复后走增量 diff 路径（非 resize 均增量）。
        """
        self._prev = Frame([])
        self._cursor_row = 1
        # ★ 无末尾空行模型（2026-08-15）：软重置后物理缓冲 = 0（无虚拟空行）。
        self._buf_h = 0
        self._top_aligned = True
        try:
            self._stream.flush()
        except Exception:
            # ★ P3 修复（review 方向）：裸吞异常补日志（exc_info 保留栈）。
            _logger.debug("suspend 刷出异常", exc_info=True)

    def reset(self, full: bool = False) -> None:
        """重置渲染状态。

        Args:
            full: True = 完全重置（``_prev=None``，下次 render 全量写入），
                  **仅 resize 使用**；False = 软重置（``_prev=空帧``，下次
                  render 走增量 diff），用于 resume / Ctrl+L 等非 resize 场景。
        """
        if full:
            self._prev = None
            self._cursor_row = 0
            self._buf_h = 0
            self._top_aligned = True
        else:
            # 空帧 → 增量 diff（与空帧比较 = 所有行都变化 → 逐行写入，不清屏）
            self._prev = Frame([])
            self._cursor_row = 1
            # ★ 无末尾空行模型（2026-08-15）：软重置后物理缓冲 = 0（无虚拟空行）。
            self._buf_h = 0
            self._top_aligned = True

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


__all__ = ["InkRenderer"]
