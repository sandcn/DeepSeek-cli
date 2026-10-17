"""ChatView 端到端流式 Markdown 渲染回归（AppModel → Ink 帧）。

覆盖用户实际看到的最终帧：表格头、代码块、引用块、列表内引用前缀顺序。
"""
from __future__ import annotations

from src.tui._const import ContentCmd
from src.tui.app.apply import _do_content
from src.tui.app.chat_view import ChatView
from src.tui.app.model import AppModel
from src.tui.ink import h
from tests.tui.ink._harness import Harness

_MD = """这是你要的 Markdown 测试：

# Markdown 测试

这是一个**加粗文本**的示例。

| 项目 | 状态 | 说明 |
|------|------|------|
| 标题 | ✅ | 支持 |
| 表格 | ✅ | 支持 |

```python
def hello(name: str) -> str:
    return f"Hello, {name}!"
```

> 引用块用于强调。

- a
  > b
  > ```py
  > x
  > ```
"""


def _stream_model(chunk: int = 5) -> AppModel:
    model = AppModel()
    model.width = 80
    for i in range(0, len(_MD), chunk):
        _do_content(model, ContentCmd(text=_MD[i:i + chunk]))
    return model


def _render(model) -> str:
    harness = Harness(80)
    return harness.render(h(ChatView, {"model": model, "width": 80}))


def test_chat_view_full_markdown_after_close():
    model = _stream_model()
    model.close_content()
    out = _render(model)
    assert "│ 项目 │ 状态 │ 说明 │" in out
    assert "│ 标题 │ ✅   │ 支持 │" in out
    assert "```python [python]" in out
    assert "│ 引用块用于强调。" in out
    assert "  │ ```py [py]" in out
    # 表格头必须在分隔线之前（流式预览/提交不得丢表头）
    assert out.index("│ 项目") < out.index("├──────┼──────┼──────┤")


def test_chat_view_streaming_preview_has_table_header():
    model = _stream_model()
    # 未 close：表格/代码块处于预览或已提交状态，表头不得丢失
    out = _render(model)
    assert "│ 项目 │ 状态 │ 说明 │" in out
    assert "│ 标题 │ ✅   │ 支持 │" in out


def test_chat_view_nested_quote_prefix_order():
    model = _stream_model()
    model.close_content()
    out = _render(model)
    lines = out.split("\n")
    idx = lines.index("• a")
    assert lines[idx + 1] == "  │ b"
    assert lines[idx + 2] == "  │ ```py [py]"
