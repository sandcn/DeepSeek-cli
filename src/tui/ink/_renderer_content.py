"""renderer 内容行发射 Mixin — committed 内容行 → 输出历史回调。

模块边界（2026-10-07 架构优化，巨型文件拆分）：从 ``ink/renderer.py``
拆出「内容行发射」职责（内容行数/内容区起始行的状态维护 + 新增内容行的
回调派发）——``InkRenderer`` 经 Mixin 组合，行为零变化。

依赖方向：本模块 → 标准库 + ``output.Frame``；不反向依赖 ``renderer``
（宿主类提供 ``_stream``/``_line_callback``/内容行状态字段）。
"""

from __future__ import annotations

import logging

from .output import Frame

_logger = logging.getLogger(__name__)


class _RendererContentMixin:
    """内容行发射（输出历史接线）——宿主 ``InkRenderer`` 提供状态字段。"""

    def set_content_line_count(
        self, count: int, *, start: int | None = None, resync: bool = False,
    ) -> None:
        """注入本帧 committed 内容行数与内容区起始行（会话每帧渲染前调用）。

        ``count`` 为 ``AppModel.committed_lines`` 的**卡片行数**（角色头 +
        正文 + 尾空行）——这些行在文档中自 ``start`` 行起连续排布，是输出
        历史应当记录的内容；底部 live 区（状态栏/输入区/解析进度行）不在
        其列。

        Args:
            count: 本帧 committed 内容行数。
            start: committed 内容区在文档中的起始行号（0-based）——由会话
                从 committed host fiber 的 ``layout_box.y`` 读取后注入（不再
                由渲染器猜 App 树结构）。None 时保持上次值。
            resync: True 时把基线**同步**到 ``count``（不回调）——终端
                resize 触发 ``reflow_committed`` 重排已提交行（wrap 变化使
                行数变化），行号空间随之重建：若不同步，wrap 新增行会被
                误判为「新增内容行」重复写入输出历史。

        未注入（默认 0）时不产生任何回调（安全——测试/独立使用不受影响）。
        """
        if start is not None:
            try:
                self._content_start = max(0, int(start))
            except (TypeError, ValueError, OverflowError):
                _logger.debug("set_content_line_count 起始行非法，保持原值", exc_info=True)
        try:
            value = int(count)
        except (TypeError, ValueError, OverflowError):
            value = 0
        value = max(0, value)
        self._frame_content_count = value
        if resync:
            self._content_line_count = value

    def reset_content_lines(self) -> None:
        """内容区重建（清屏/重放）——基线归零。

        ``CLEAR_MSGS``（Ctrl+L 清屏 / ``/editmsg`` 重放）清空
        ``AppModel.committed_lines``：文档行号空间重建，旧基线失效。归零后
        后续提交内容自内容区起始行起重新累计回调（不补记清屏前已记录的行）。
        """
        self._content_line_count = 0
        self._frame_content_count = 0

    def _emit_content_lines(self, frame: Frame) -> None:
        """回调**新增的已提交内容行**（输出历史跟踪）并推进基线。

        只回调 committed 区（文档 ``[_content_start, _content_start + count)``）
        中本次新增的行——修复前各渲染分支按 ``[prev_h, new_h)``（文档末尾行
        区间）回调，把状态栏/输入区/时间线/边框等 live 行当作「新增提交行」
        反复写入输出历史（同一底部行随行号增长被反复记录，占输出历史六成
        以上），而真正新增的内容行（插入在文档中部）几乎全部漏记。

        基线语义：``_content_line_count`` = 上次回调时的内容行数。
        - 增长 → 回调 ``[old, new)`` 对应文档行；
        - 相等 → 无动作（帧内重绘/动画/状态刷新不产生历史）；
        - 缩短（清屏/``/editmsg`` 重放/宽度重排）→ 同步基线（文档行号空间
          已重建，重新累计；不补记旧行、不重复回调）。
        """
        current = self._frame_content_count
        previous = self._content_line_count
        if current == previous:
            return
        if current < previous:
            self._content_line_count = current
            return
        self._content_line_count = current
        if self._line_callback is None:
            return
        base = self._content_start
        start = base + previous
        end = min(base + current, len(frame.lines))
        if end <= start:
            return
        try:
            for idx in range(start, end):
                self._line_callback(frame.render_line(idx) + "\n")
        except Exception:
            # 裸吞异常补日志（exc_info 保留栈）。
            _logger.debug("_emit_content_lines 行回调异常", exc_info=True)


__all__ = ["_RendererContentMixin"]
