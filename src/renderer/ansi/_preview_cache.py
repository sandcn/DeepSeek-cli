"""_preview_cache — 未闭合块预览的行级增量渲染缓存。

流式预览每次 ``write`` 都整块重渲染未闭合内容；除代码块外（``_render_code_preview``
已有按行高亮缓存），段落/引用/告示等长块的成本随内容长度增长。本模块提供通用
``LinePreviewCache``：按源行列表做**最长公共前缀复用**，只渲染新增/变化的行，
与代码块预览策略一致。

行级渲染对跨软换行的行内标记是无状态的（与代码块逐行高亮同理）：预览阶段
未闭合标记可能短暂原样显示，块闭合提交后按整段解析正确配对，提交路径不受影响。
"""

from __future__ import annotations

from .helpers import AnsiLine


class LinePreviewCache:
    """按源行做最长公共前缀复用的行级渲染缓存。

    使用方式::

        cache = LinePreviewCache()
        rows = cache.render(("para",), src_lines, render_line)

    其中 ``render_line(text) -> list[AnsiLine]`` 负责把单行源文本渲染为行
    （约定返回恰好 1 行；返回多行时降级为整块重渲染，保证结果正确）。

    线程/生命周期：单实例由渲染器持有，随渲染器 GC。
    """

    __slots__ = ("_key", "_src", "_rows")

    def __init__(self) -> None:
        self._key = None
        self._src: list[str] = []
        self._rows: list[AnsiLine] = []

    def reset(self) -> None:
        """清空缓存（块闭合 / 预览清空 / 类型切换时调用）。"""
        self._key = None
        self._src = []
        self._rows = []

    def render(self, key, src_lines: list[str], render_line) -> list[AnsiLine]:
        """返回 ``src_lines`` 的行级渲染结果（复用未变化的前缀行）。

        Args:
            key: 缓存键（区分块类型/样式参数；变化时整体重置）。
            src_lines: 源行列表（流式只追加时前缀稳定 → 复用）。
            render_line: 单行渲染回调，返回 ``list[AnsiLine]``（约定 1 行）。

        Returns:
            与 ``src_lines`` 对应的渲染行列表。
        """
        if key != self._key:
            self.reset()
            self._key = key
        cached_src = self._src
        rows = self._rows
        n = min(len(src_lines), len(cached_src))
        common = 0
        while common < n and src_lines[common] == cached_src[common]:
            common += 1
        if common < len(cached_src):
            del rows[common:]
        base = len(rows)
        for line in src_lines[base:]:
            produced = render_line(line)
            if len(produced) != 1:
                # 单行产出非 1 行（异常/特殊内容）→ 放弃增量，整块重渲染
                return self._render_all(src_lines, render_line)
            rows.append(produced[0])
        self._src = list(src_lines)
        return rows

    def _render_all(self, src_lines: list[str], render_line) -> list[AnsiLine]:
        self._src = list(src_lines)
        out: list[AnsiLine] = []
        for line in src_lines:
            out.extend(render_line(line))
        self._rows = out
        return out


__all__ = ["LinePreviewCache"]
