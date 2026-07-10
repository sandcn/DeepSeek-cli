"""protocols — RenderEngineAPI 协议接口。

定义 Handler 与 Engine 之间的契约接口，消除 Handler 直接访问 Engine 私有属性的紧耦合。
所有 Handler 通过此协议访问 Engine 能力，不再直接读写 engine._xxx 私有属性。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable, Optional, Any

from rich.text import Text
from rich.style import Style


@runtime_checkable
class RenderEngineAPI(Protocol):
    """Handler 可用的 Engine 服务接口协议。

    所有 RenderEngine 和 VNodePatcher 都应实现此协议，
    Handler 只通过此协议访问渲染能力。
    """

    @property
    def typing_speed(self) -> int:
        """获取打字机速度（字符/秒）。"""
        ...

    @property
    def output_width(self) -> int:
        """获取终端输出宽度。"""
        ...

    @property
    def bq_depth(self) -> int:
        """获取 blockquote 深度。"""
        ...

    @bq_depth.setter
    def bq_depth(self, value: int) -> None:
        """设置 blockquote 深度。"""
        ...

    @property
    def todo_emitted(self) -> bool:
        """获取 todo 进度是否已标记。"""
        ...

    @todo_emitted.setter
    def todo_emitted(self, value: bool) -> None:
        """设置 todo 进度是否已标记。"""
        ...

    def render_inline(self, text: str) -> Text:
        """渲染内联 Markdown 格式为 Rich Text。"""
        ...

    def get_lexer(self, lang: str) -> Any:
        """获取/缓存 Pygments 词法分析器。"""
        ...

    def ensure_theme(self) -> None:
        """确保 Pygments 主题已加载。"""
        ...

    def write(self, renderable) -> None:
        """输出 Rich renderable 对象。"""
        ...

    def write_line(self, text: str = "") -> None:
        """输出纯文本行。"""
        ...

    def write_typing(self, text: Text, speed: int, end: str = "\n",
                     fill_style: Optional[Style] = None) -> None:
        """以打字机效果输出。"""
        ...

    def code_typing_speed(self) -> int:
        """计算代码块/图表的打字机速度。"""
        ...

    def get_highlight_lines(self, attrs: str) -> list[int]:
        """从属性字符串中解析行高亮配置。"""
        ...

    def output_assembled(self, assembled: Text) -> None:
        """统一输出 assembled Text（打字机或即时）。"""
        ...

    def emit_todo_progress(self) -> None:
        """如果存在 Todo 统计，输出进度条并重置。"""
        ...

    def write_raw(self, text: str) -> None:
        """快速输出纯文本。"""
        ...

    def print(self, *args, **kwargs) -> None:
        """打印整块 renderable。"""
        ...

