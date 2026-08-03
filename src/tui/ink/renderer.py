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
    高度差行数）超限时降级为"全量 clear + 全量重建"（避免病态大重写冻结 UI）。

不切换备用屏幕、不用 DECSTBM——内容自然流入 scrollback。

Args:
    stream: 输出流（默认 sys.__stdout__；测试传 Mock/StringIO）。
"""

from __future__ import annotations

import io
import logging
import sys

from src.tui._screen import cursor_up, cursor_down, clear_line, cursor_forward, clear_screen
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

        ``_write_full`` 每行以 ``\\n`` 结尾，写满后缓冲区为 ``doc_h + 1`` 行
        （末尾多一行空白），屏幕显示底部 ``height`` 行——偏移按
        ``max(0, (doc_h + 1) - height)`` 计算（含末尾空白行；否则内容行与
        光标的屏幕映射相差一行）。

        Args:
            doc_h: 文档总行数。

        Returns:
            可见区顶部对应的文档行偏移；height=0 时恒 0（无约束）。
        """
        if self._height <= 0:
            return 0
        return max(0, doc_h + 1 - self._height)

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
        """文档写入后物理光标所在屏幕行（1-based，已钳制）。"""
        return self._clamp(self._to_screen(doc_h + 1, doc_h))

    def _advance_row(self, row: int) -> int:
        """写一行并 ``\n`` 后光标行的推进（屏幕底部钳制）。

        光标已在屏幕底部时 ``\n`` 触发上滚、光标停底不再下移（height=0
        时恒 +1，无约束）。
        """
        if self._height > 0 and row >= self._height:
            return self._height
        return row + 1

    # ── 渲染 ─────────────────────────────────────────

    def render(self, frame: Frame) -> None:
        """渲染新帧（最小差异写入）。"""
        if self._prev is None:
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

        delta = new_h - prev_h

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
            # 定位到首个新增行的屏幕行（从 prev 文档底部开始写 delta 新行）
            current_row = self._cursor_row
            target_row = self._clamp(self._to_screen(prev_h + 1, prev_h))
            if current_row > target_row:
                buf.write(cursor_up(current_row - target_row))
            elif current_row < target_row:
                buf.write(cursor_down(target_row - current_row))
            current_row = target_row
            for line_idx in range(prev_h, new_h):
                buf.write("\r")
                buf.write(frame.render_line(line_idx))
                buf.write(_CLEAR_EOL)
                buf.write("\n")
                current_row = self._advance_row(current_row)
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
            _logger.warning(
                "单帧重写行数 %d 超上限 %d，降级为全量 clear + 全量重建",
                rewrite_count, _MAX_REWRITE_ROWS,
            )
            # ★ 1.5 修复：旧实现「仅写末尾 _MAX_REWRITE_ROWS 行 + 清残留」在文档
            #   中间留下旧行残留——跳写语义使首差异行之前的静态内容被跳过，中间
            #   行无法与目标帧对齐，画布出现陈旧行。改为「全量 clear + 全量重建」：
            #   仅超限罕见路径触发，闪烁可接受；重建仅回调新增行（prev_h..new_h），
            #   不重复回调已有行（输出历史不被污染）；_cursor_row 由 _write_full
            #   重置为 new_h + 1（与目标帧一致）。
            try:
                self._stream.write(clear_screen())
            except Exception:
                _logger.debug("降级 clear_screen 写入异常", exc_info=True)
            self._write_full(frame, prev_h)
            # _write_full 不更新 _prev（首帧/重置路径由调用方置 None）；降级重建
            # 须写回目标帧，否则下一帧误判首帧全量重写（输出重复）。
            self._prev = frame
            return

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
        #   - 缩短 + 屏幕约束（delta<0 且 height>0）：**全量重建**——终端缓冲
        #     无法删除行，清行残留使缓冲长度 > doc_h+1，屏幕偏移模型漂移
        #     （后续增长/等高重写按错误偏移写导致错乱）；重建（clear + 重写）
        #     重置缓冲与偏移一致。交互式导航/补全弹窗关闭等缩短场景低频，
        #     重建成本可接受（流式只增长不缩短，不受影响）。
        #   重写目标一律按 **prev 帧偏移** 换算：终端缓冲此刻仍处于 prev 布局
        #   （只能经底部写行触发滚动），按 prev 位置原位重写、由末尾换行滚动
        #   过渡到 new 布局——按 new 偏移写会覆盖未滚动区域。
        #   raw 终端模式下 \n 不归位列 1，每行须前缀 \r。
        # 方向3（缩短闪烁优化）：文档在屏幕内（``_screen_offset==0``，无滚动
        # 偏移）时缩短走常规 diff 路径（重写 + 清残留）——删字/关闭补全弹窗
        # 不再全屏 clear 闪烁。仅文档高于屏幕（offset>0，缩短改变可见区偏移，
        # 终端缓冲无法删除行 → 偏移模型漂移）时全量 clear + 重建（防错乱，
        # 测试锁定：test_shrink_emits_clear_screen / test_grow_shrink_grow_no_drift）。
        if (
            delta < 0
            and self._height > 0
            and self._screen_offset(prev_h) > 0
        ):
            try:
                self._stream.write(clear_screen())
            except Exception:
                _logger.debug("缩短重建 clear_screen 写入异常", exc_info=True)
            # emit_start=prev_h：缩短无新增行（range(prev_h, new_h) 为空），
            # 不回调输出历史；_write_full 重置 _cursor_row 到 new 文档底部。
            self._write_full(frame, prev_h)
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
                    buf.write("\r")
                    buf.write(frame.render_line(idx))
                    buf.write(_CLEAR_EOL)
                    buf.write("\n")
                    current_row = self._advance_row(current_row)
        else:
            # ★ 方向4 优化（delta!=0）：头部差异区间逐区间重写 + 位移区连续
            #   重写——替代旧「从 rewrite_start 连续重写整个可见区」。头部动画
            #   （标题栏呼吸）只改头部区间；committed 历史可见区（锚点之前、
            #   非差异行）零重写。
            #   重写目标一律按 **prev 帧偏移** 换算（终端缓冲此刻仍是 prev
            #   布局，只能经底部写行触发滚动）。
            for start, end in head_runs:
                target_row = self._clamp(self._to_screen(start + 1, prev_h))
                if current_row > target_row:
                    buf.write(cursor_up(current_row - target_row))
                elif current_row < target_row:
                    buf.write(cursor_down(target_row - current_row))
                current_row = target_row
                for idx in range(start, end):
                    buf.write("\r")
                    buf.write(frame.render_line(idx))
                    buf.write(_CLEAR_EOL)
                    buf.write("\n")
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
                    buf.write("\r")
                    buf.write(frame.render_line(idx))
                    buf.write(_CLEAR_EOL)
                    buf.write("\n")
                    current_row = self._advance_row(current_row)
            # 缩短：清除残留行（prev 帧 rows new_h+1 .. prev_h）
            if delta < 0:
                target_row = self._to_screen(new_h + 1, prev_h)
                if self._height <= 0 or target_row >= 1:
                    target_row = self._clamp(target_row)
                    if current_row > target_row:
                        buf.write(cursor_up(current_row - target_row))
                    elif current_row < target_row:
                        buf.write(cursor_down(target_row - current_row))
                    current_row = target_row
                    for _ in range(prev_h - new_h):
                        buf.write(clear_line())
                        buf.write(cursor_down(1))
                        current_row = self._advance_row(current_row)
            # 增长：回调新增行（输出历史跟踪；重写循环已写出这些行）
            if delta > 0:
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

    def _diff_runs(
        self,
        prev: Frame,
        frame: Frame,
        n: int,
        start: int = 0,
    ) -> list[tuple[int, int]]:
        """收集两帧前 n 行的差异区间（[start, end) 行号，升序、不重叠）。

        与 ``first_diff_line`` 相同比较语义：身份短路（Line 对象相同 → 相等）
        + runs 值相等。连续差异行合并为一个区间（区间内逐行 ``\r``+重写，
        免逐行光标移动）；区间间以光标移动衔接。仅覆盖两帧共有行
        （``min(prev.height, frame.height)``）；高度差（新增/删除行）由调用方
        delta 分支单独处理。

        Args:
            prev: 上一帧。
            frame: 新帧。
            n: 参与比较的行数（``min(prev.height, frame.height)``）。
            start: 起始扫描行（0-based，含）；调用方保证 [0, start) 无差异
                （``first_diff_line`` 定义）——免扫描不变的 committed 前缀。

        Returns:
            差异区间列表（每个为 [start, end) 行号，至少含一行）。
        """
        runs: list[tuple[int, int]] = []
        in_run = False
        run_start = start
        for idx in range(start, n):
            p = prev.lines[idx]
            f = frame.lines[idx]
            differs = p is not f and p.runs != f.runs
            if differs and not in_run:
                in_run = True
                run_start = idx
            elif not differs and in_run:
                in_run = False
                runs.append((run_start, idx))
        if in_run:
            runs.append((run_start, n))
        return runs

    def _is_tail_shifted(self, prev: Frame, frame: Frame, i: int, delta: int) -> bool:
        """检测尾部内容是否只是整体下移（仅新增 delta 行）。

        规则：``prev.lines[i:prev_h]`` 与 ``frame.lines[i+delta:new_h]``
        逐行相同（身份短路 + runs 值相等）。

        Args:
            prev: 上一帧。
            frame: 新帧。
            i: 首差异行。
            delta: 高度差（new_h - prev_h，>0 时检测有意义）。

        Returns:
            True — 尾部内容整体下移且相同（可跳过重写）。
        """
        p = prev.lines
        n = frame.lines
        start = i + delta
        if start > len(n):
            return False
        # 索引循环比较（避免每帧创建两段切片——方向3 性能）
        count = prev.height - i
        if count != len(n) - start:
            return False
        for k in range(count):
            x = p[i + k]
            y = n[start + k]
            if x is not y and x.runs != y.runs:
                return False
        return True

    def _find_tail_anchor(self, prev: Frame, frame: Frame, delta: int) -> int:
        """从文档末尾向前找尾部位移锚点（方向4 优化）。

        旧帧 ``[j, prev_h)`` 与新帧 ``[j+delta, new_h)`` 逐行相同（身份短路 +
        runs 值相等）时，j 为位移锚点——高度差发生在 j 之后，尾部内容整体
        位移 delta 行。头部差异（j 之前，如标题栏呼吸色变化）与尾部位移
        （j 之后）分开处理：仅重写头部差异区间 + 从锚点起的位移区，跳过
        锚点之前的未变化行（committed 历史可见区不再被头部动画引发全量
        重写——流式期间每帧重写范围从 O(可见区) 降为 O(头部差异 + 位移区)）。

        Args:
            prev: 上一帧。
            frame: 新帧。
            delta: 高度差（new_h - prev_h，可为负）。

        Returns:
            位移锚点 j（0-based，范围 [0, prev_h]）；无相同尾部时返回 0。
        """
        p = prev.lines
        n = frame.lines
        j = prev.height
        while j > 0:
            old_line = p[j - 1]
            new_idx = j - 1 + delta
            if 0 <= new_idx < len(n):
                new_line = n[new_idx]
                if old_line is new_line or old_line.runs == new_line.runs:
                    j -= 1
                    continue
            break
        return j

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
            # 方向1 步骤3（首帧空帧光标）：空帧也更新 _cursor_row（=height+1=1）
            # ——修复前空帧不置位，下一帧 ``n_move`` 产生多余光标移动。
            self._cursor_row = self._bottom_row(frame.height)
            return
        buf = io.StringIO()
        for line in frame.lines:
            buf.write("\r")
            buf.write(line.render())
            buf.write("\n")
        self._emit_new_lines(frame, emit_start, frame.height)
        self._cursor_row = self._bottom_row(frame.height)
        self._stream.write(buf.getvalue())
        self._stream.flush()

    def _emit_new_lines(self, frame: Frame, start: int, end: int) -> None:
        """回调新增行（输出历史跟踪）。"""
        if self._line_callback is None:
            return
        try:
            for idx in range(start, end):
                self._line_callback(frame.render_line(idx) + "\n")
        except Exception:
            pass

    # ── 光标 ─────────────────────────────────────────

    def place_cursor(self, row: int, col: int) -> None:
        """将光标放置到文档坐标 (row, col)（1-based）。

        从当前光标位置（_cursor_row）相对移动，避免绝对坐标在滚动终端中
        失效。raw 模式下先 \r 归位列 1 再前进到 col。

        height>0 时 row 为文档坐标，先换算为屏幕坐标（钳制到可见区）——
        目标行位于可见区上方（滚动区）时钳制到屏幕顶部（不可达时无法
        放置，钳制安全侧）。
        """
        doc_h = self._prev.height if self._prev is not None else row
        target = self._clamp(self._to_screen(row, doc_h))
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

        写入 ``clear_screen()``（``\\033[2J\\033[H``）并重置 prev/光标——
        下一帧从空文档全量渲染。scrollback 历史保留（终端自身行为）。
        """
        try:
            self._stream.write(clear_screen())
            self._stream.flush()
        except Exception:
            _logger.debug("full_clear 写入异常", exc_info=True)
        self._prev = None
        self._cursor_row = 0

    # ── 生命周期 ─────────────────────────────────────

    def suspend(self) -> None:
        """暂停：重置渲染状态（live 区已作为普通行提交到 scrollback）。

        非全屏模型下文档行都是真实 scrollback 行，无需额外提交；
        仅清空 prev 状态，使恢复后重新全量渲染。
        """
        self._prev = None
        self._cursor_row = 0
        try:
            self._stream.flush()
        except Exception:
            pass

    def reset(self) -> None:
        """重置渲染状态（resume 后重新渲染）。"""
        self._prev = None
        self._cursor_row = 0

    def flush(self) -> None:
        """刷出底层输出。"""
        try:
            self._stream.flush()
        except Exception:
            pass

    # ── 测试辅助 ─────────────────────────────────────

    @property
    def prev_frame(self) -> Frame | None:
        return self._prev

    @property
    def cursor_row(self) -> int:
        return self._cursor_row


__all__ = ["InkRenderer"]
