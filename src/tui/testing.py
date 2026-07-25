"""TUI 测试辅助工具 — 统一测试夹具和缓冲区适配器。

提供：
  - tui_test_env: 上下文管理器，自动复位 Framework/AnimatorContext/ComponentRegistry 单例
  - BufferOutputAdapter: 实现 IOutputTarget 协议的内存缓冲区适配器，捕获输出用于断言

用法：
    from src.tui.testing import tui_test_env, BufferOutputAdapter
    
    with tui_test_env():
        adapter = BufferOutputAdapter()
        adapter.write("Hello")
        assert adapter.getvalue() == "Hello"
"""

from __future__ import annotations

import contextlib
from typing import Generator


__all__: list[str] = [
    "tui_test_env",
    "BufferOutputAdapter",
]


# ═══════════════════════════════════════════════════════════
# 统一测试夹具
# ═══════════════════════════════════════════════════════════


@contextlib.contextmanager
def tui_test_env() -> Generator[None, None, None]:
    """统一测试环境上下文管理器。

    进入时复位所有全局单例，退出时再次复位确保测试隔离。

    复位清单：
      - Framework（from src.tui.framework）
      - AnimatorContext（from src.tui.animation.animator）
      - ComponentRegistry（from src.tui.core.component_registry，若存在）
      - EffectRegistry（from src.tui.core.effects，清空注册表）
      - DisplayEventBus（from src.tui.events.event_bus）

    用法：
        from src.tui.testing import tui_test_env
        from src.tui.framework import Framework
        
        with tui_test_env():
            framework = Framework.get_default()
            # ... 测试逻辑 ...
    """
    from src.tui.framework import Framework
    from src.tui.animation.animator import AnimatorContext
    from src.tui.core.effects import EffectRegistry
    from src.tui.events.event_bus import DisplayEventBus

    # 尝试导入 ComponentRegistry（可能尚不存在，try/except 兜底）
    _has_component_registry = False
    try:
        from src.tui.core.component_registry import ComponentRegistry
        _has_component_registry = True
    except ImportError:
        pass

    # 进入时复位
    Framework.reset_default()
    AnimatorContext.reset_default()
    EffectRegistry.clear()
    DisplayEventBus.reset_default()
    if _has_component_registry:
        ComponentRegistry.reset_default()

    try:
        yield
    finally:
        # 退出时再次复位
        Framework.reset_default()
        AnimatorContext.reset_default()
        EffectRegistry.clear()
        DisplayEventBus.reset_default()
        if _has_component_registry:
            ComponentRegistry.reset_default()


# ═══════════════════════════════════════════════════════════
# 缓冲区输出适配器
# ═══════════════════════════════════════════════════════════


class BufferOutputAdapter:
    """实现 IOutputTarget 协议的内存缓冲区输出适配器。

    捕获所有输出到内部缓冲区，用于测试断言。

    用法：
        adapter = BufferOutputAdapter()
        adapter.write("Hello")
        adapter.write_line(" World")
        assert adapter.getvalue() == "Hello World\\n"
        adapter.clear()
        assert adapter.getvalue() == ""
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []

    # ── IOutputTarget 协议方法 ──

    def write(self, renderable: object) -> None:
        """写入文本（支持 str 和 rich.text.Text 对象）。"""
        if hasattr(renderable, 'plain'):
            self._buffer.append(str(renderable.plain))
        else:
            self._buffer.append(str(renderable))

    def write_line(self, text: str = "") -> None:
        """写入一行文本（追加换行符）。"""
        self._buffer.append(text + "\n")

    def write_raw(self, text: str) -> None:
        """写入原始文本（不处理）。"""
        self._buffer.append(text)

    def flush(self) -> None:
        """刷新缓冲区（无操作，兼容性实现）。"""
        pass

    def render_frame(self, lines: list[str], last_lines: int) -> int:
        """增量渲染帧 — 将 lines 逐条写入缓冲区。"""
        self._buffer.extend(lines)
        return len(lines)

    # ── 访问方法 ──

    def getvalue(self) -> str:
        """获取所有写入的文本拼接结果。"""
        return "".join(self._buffer)

    @property
    def lines(self) -> list[str]:
        """获取所有写入的行（快照）。"""
        return list(self._buffer)

    def clear(self) -> None:
        """清空缓冲区。"""
        self._buffer.clear()

    @property
    def terminal_width(self) -> int:
        """输出目标宽度。"""
        return 120

    def __len__(self) -> int:
        return len(self._buffer)

    def __getitem__(self, index: int | slice):
        return self._buffer[index]



