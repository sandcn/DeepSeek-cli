"""_preview_cache — 未闭合块预览的行级增量渲染缓存。

流式预览每次 ``write`` 都整块重渲染未闭合内容；除代码块外（``_render_code_preview``
已有按行高亮缓存），段落/引用/告示等长块的成本随内容长度增长。本模块提供通用
``LinePreviewCache``：按源行列表做**最长公共前缀复用**，只渲染新增/变化的行，
与代码块预览策略一致。

预览有界化（解析器 ``_preview_tail`` 把超长段落截为最近 ``_PREVIEW_MAX_LINES``
行）会让源行列表**头部滑窗**——每帧丢弃最旧行、追加最新行，前缀复用完全失效
（整块重渲染，长段落流式累计 O(n²)）。故向前缀复用之外补一条「滑窗复用」：
检测 ``cached_src[d:] == src_lines[:len-d]`` 的重叠（``d`` 为丢弃行数），复用
重叠区间的渲染行，只渲染尾部新增行。

行级渲染对跨软换行的行内标记是无状态的（与代码块逐行高亮同理）：预览阶段
未闭合标记可能短暂原样显示，块闭合提交后按整段解析正确配对，提交路径不受影响。
"""

from __future__ import annotations

from .helpers import AnsiLine
from ._line_match import common_prefix_len, sliding_drop


class LinePreviewCache:
    """按源行做最长公共前缀复用的行级渲染缓存。

    使用方式::

        cache = LinePreviewCache()
        rows = cache.render(("para",), src_lines, render_line)

    其中 ``render_line(text) -> list[AnsiLine]`` 把单行源文本渲染为若干行
    （**允许返回多行**——行内多行块如二维公式会让一行源文本展开为多行；
    缓存按源行记录其产出行数，前缀复用/滑窗复用时同步裁剪，不触发整块重渲染）。

    线程/生命周期：单实例由渲染器持有，随渲染器 GC。
    """

    __slots__ = ("_key", "_src", "_spans", "_rows")

    def __init__(self) -> None:
        self._key = None
        self._src: list[str] = []
        #: 每个源行产出的渲染行数（``sum(_spans) == len(_rows)``）
        self._spans: list[int] = []
        self._rows: list[AnsiLine] = []

    def reset(self) -> None:
        """清空缓存（块闭合 / 预览清空 / 类型切换时调用）。"""
        self._key = None
        self._src = []
        self._spans = []
        self._rows = []

    def render(self, key, src_lines: list[str], render_line) -> list[AnsiLine]:
        """返回 ``src_lines`` 的行级渲染结果（复用未变化的前缀行）。

        Args:
            key: 缓存键（区分块类型/样式参数；变化时整体重置）。
            src_lines: 源行列表（流式只追加时前缀稳定 → 复用）。
            render_line: 单行渲染回调，返回 ``list[AnsiLine]``（可多行）。

        Returns:
            与 ``src_lines`` 对应的渲染行列表（行数 = 各源行产出之和）。
        """
        if key != self._key:
            self.reset()
            self._key = key
        cached_src = self._src
        spans = self._spans
        rows = self._rows
        common = common_prefix_len(cached_src, src_lines)
        if common == 0 and len(cached_src) >= 2 and len(spans) == len(cached_src):
            # 首行即分歧 → 尝试「头部滑窗复用」（预览有界化的行丢弃）。
            # 忽略尾行（活动行每帧变化，不参与重叠判定）。
            drop = sliding_drop(cached_src, src_lines, ignore_tail=True)
            if drop:
                del rows[:sum(spans[:drop])]
                del spans[:drop]
                del cached_src[:drop]
                common = common_prefix_len(cached_src, src_lines)
        if common < len(cached_src):
            del rows[sum(spans[:common]):]
            del spans[common:]
            del cached_src[common:]
        for line in src_lines[len(cached_src):]:
            produced = render_line(line)
            rows.extend(produced)
            spans.append(len(produced))
        self._src = list(src_lines)
        return rows


__all__ = ["LinePreviewCache"]
