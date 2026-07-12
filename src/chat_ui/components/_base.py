"""组件基类 — TuiComponent + _estimate_content_lines。

从 _components.py 拆分，包含所有组件共用的基类和辅助函数。
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..renderer.output import OutputAdapter

from rich.text import Text

_logger = logging.getLogger(__name__)


class TuiComponent:
    """React Ink-like 渲染组件基类。

    所有子类必须实现 render() 方法，可选重写 render_to_adapter()。

    ## 两种渲染路径

    路径 A（默认——适用于大部分组件）：
        子类仅实现 render() → str | Text。
        基类 render_to_adapter() 自动调用 render() 获取输出，
        再将结果通过 adapter.write() 写入 OutputAdapter。

        适用场景：UserMsgBlock、ErrorBlock、NotificationBlock 等
        输出格式固定、无需直接操作 adapter 的组件。

    路径 B（高级——需要直接操作 OutputAdapter）：
        子类重写 render_to_adapter()，完全绕过 render()，
        直接对 OutputAdapter 进行操作（如分段写入、ANSI 处理等）。

        适用场景：ToolOutputBlock（需要处理 \\r 回车/ANSI 转义）、
        ToolSummaryBlock（需要根据成功/失败组合多次写入）等
        输出逻辑复杂的组件。重写 render_to_adapter() 时仍应实现
        render() 作为降级/调试用途。

    ## 入场动效

    所有组件支持级联入场弹入动效（bounce）。
    通过 set_entry_frame() 设置入场帧号后，render_to_adapter()
    自动在输出前方添加 build_bounce_ansi 前缀，持续 6 帧后关闭。
    重写 render_to_adapter() 的子类可使用 _get_bounce_prefix()
    获取当前帧的动效 ANSI 前缀自行包裹。
    """

    # ── 入场动效基础设施 ──────────────────────────

    _entry_frame: int = -1  # -1 表示无入场动效

    def set_entry_frame(self, frame: int) -> None:
        """设置入场帧号。

        设值后 render_to_adapter() 会在输出中添加弹入动效。
        frame 使用 AnimatorContext.get_default().frame 的当前值。

        Args:
            frame: 入场时的全局帧号。
        """
        self._entry_frame = frame

    @property
    def _entry_phase(self) -> float:
        """归一化入场进度 [0.0, 1.0]。

        基于当前全局帧号与入场帧号的差值计算，
        超过 6 帧后 clamp 到 1.0。
        -1（无入场动效）时恒返回 1.0。
        """
        if self._entry_frame < 0:
            return 1.0
        # 注：使用绝对导入避免循环导入 + 测试环境兼容
        from src.ui.tui._animator import AnimatorContext
        current = AnimatorContext.get_default().frame
        diff = current - self._entry_frame
        return min(1.0, max(0.0, diff / 6.0))

    def _get_bounce_prefix(self) -> str:
        """获取入场弹入动效的 ANSI 前缀。

        入场动效激活时（_entry_frame >= 0 且 _entry_phase < 1.0）
        返回 build_bounce_ansi 生成的前缀，否则返回空字符串。
        子类重写 render_to_adapter() 时可用此前缀包裹最终输出。

        Returns:
            动效 ANSI 前缀，或空字符串。
        """
        if self._entry_frame >= 0 and self._entry_phase < 1.0:
            # 注：使用绝对导入避免循环导入 + 测试环境兼容
            from src.ui.tui._text_utils import build_bounce_ansi
            frame_offset = min(int(self._entry_phase * 6), 5)
            return build_bounce_ansi(frame_offset, 6)
        return ""

    # ── 抽象方法 ──────────────────────────────────

    @abstractmethod
    def render(self) -> str | Text:
        """渲染组件内容。

        子类必须实现此方法，返回 str 或 rich.text.Text 对象。

        Returns:
            str | Text: 渲染后的文本内容，供 adapter.write() 输出。
        """

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """通过 OutputAdapter 渲染组件，返回估计行数。

        默认实现（路径 A）：
            调用 self.render() 获取输出，通过 adapter.write() 写入
            OutputAdapter，最后调用 _estimate_content_lines() 返回行数。

            入场动效：当 _entry_frame >= 0 且 _entry_phase < 1.0 时，
            自动以 build_bounce_ansi 包裹输出（6 帧弹入渐显）。

        重写场景（路径 B）：
            子类可重写此方法以绕过 render() 直接操作 adapter，
            实现分段写入、ANSI 转义处理等高级渲染逻辑。
            重写时仍建议实现 render() 作为降级/调试用途。

        Args:
            adapter: OutputAdapter 实例，用于将内容写入终端。

        Returns:
            int: 渲染内容的估计行数。
        """
        output = self.render()
        if isinstance(output, (str, Text)):
            bounce = self._get_bounce_prefix()
            if bounce:
                output_str = str(output)
                adapter.write_raw(f"{bounce}{output_str}\033[0m")
                return _estimate_content_lines(output_str)
            adapter.write(output)
            return _estimate_content_lines(str(output))
        return 0


# ═══════════════════════════════════════════════════════════
# 行数估算辅助（内部使用）
# ═══════════════════════════════════════════════════════════

def _estimate_content_lines(text: str) -> int:
    """估算文本内容的终端行数。

    按文本中的换行符数量 + 1 计算行数。
    不处理终端换行（word wrapping），仅适用于粗略估计。

    Args:
        text: 要估算的文本。

    Returns:
        int: 估算的行数，至少为 1。
    """
    if not text:
        return 1
    return text.count('\n') + 1
