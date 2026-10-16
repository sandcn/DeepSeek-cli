"""ANSI Mermaid 图表渲染测试（TUI 流式路径）。

覆盖 ``src/renderer/ansi/_mermaid_render.py`` / ``ansi/mermaid.py``：
  - 11 类图（流程图 / 时序图 / 类图 / 状态图 / 甘特 / 饼图 / ER / Git /
    思维导图 / 时间线 / 用户旅程）
  - 单行 ``;`` 分隔语法、空/未知类型降级、异常安全
  - 结果缓存（同源码复用同一结果对象）
"""

from __future__ import annotations

from src.renderer.ansi.mermaid import render_mermaid_block, clear_mermaid_cache


def _text(src: str) -> str:
    return "\n".join(ln.plain for ln in render_mermaid_block(src))


def test_flowchart_td():
    text = _text("flowchart TD\n  A[开始] --> B{判断}\n  B -->|是| C[结束]\n")
    assert "开始" in text and "判断" in text and "结束" in text
    assert "\u25bc" in text  # ▼
    assert "是" in text      # 边标签


def test_flowchart_lr_and_dotted():
    text = _text("flowchart LR\n  A --> B\n  B -.-> C\n")
    assert "\u25b6" in text  # ▶
    assert "\u2505" in text  # ┅ 虚线


def test_flowchart_single_line_semicolon():
    text = _text("graph TD; A-->B")
    assert "A" in text and "B" in text
    assert "\u25bc" in text


def test_flowchart_subgraph():
    text = _text("flowchart TD\n  subgraph 组\n  A --> B\n  end\n")
    assert "\U0001f4c1" in text  # 📁
    assert "组" in text


def test_sequence():
    text = _text("sequenceDiagram\n  participant Alice\n  participant Bob\n"
                 "  Alice->>Bob: Hello\n  Bob-->>Alice: Hi\n")
    assert "Alice" in text and "Bob" in text
    assert "Hello" in text and "Hi" in text


def test_sequence_note():
    text = _text("sequenceDiagram\n  A->>B: x\n  Note over A,B: 说明\n")
    assert "说明" in text


def test_class_diagram():
    text = _text("classDiagram\n  class Animal {\n    +name: str\n    +speak()\n  }\n"
                 "  Animal <|-- Dog\n")
    assert "Animal" in text and "Dog" in text
    assert "+name: str" in text
    assert "\u25c1\u2500" in text  # ◁─ 继承


def test_state_diagram():
    text = _text("stateDiagram\n  [*] --> Idle\n  Idle --> Run: start\n  Run --> [*]\n")
    assert "Idle" in text and "Run" in text
    assert "start" in text
    assert "\u25cf" in text  # ●


def test_gantt():
    text = _text("gantt\n  title 项目\n  section 阶段A\n  任务1: a1, 5d\n  任务2: after a1, 10d\n")
    assert "项目" in text and "任务1" in text and "任务2" in text
    assert "\u2588" in text  # █


def test_pie():
    text = _text('pie title 份额\n  "A": 40\n  "B": 60\n')
    assert "份额" in text
    assert "40.0%" in text and "60.0%" in text


def test_er_diagram():
    text = _text("erDiagram\n  CUSTOMER ||--o{ ORDER : places\n")
    assert "CUSTOMER" in text and "ORDER" in text
    assert "places" in text


def test_gitgraph():
    text = _text("gitGraph\n  commit\n  branch dev\n  checkout dev\n  commit\n"
                 "  checkout main\n  merge dev\n")
    assert "main" in text and "dev" in text
    assert "\u25cf" in text  # ●


def test_mindmap():
    text = _text("mindmap\n  root\n    A\n      A1\n    B\n")
    assert "root" in text and "A1" in text
    assert "\u251c\u2500" in text  # ├─


def test_timeline():
    text = _text("timeline\n  title 历史\n  2000 : 事件A\n  2010 : 事件B : 事件C\n")
    assert "历史" in text and "2000" in text and "事件C" in text


def test_journey():
    text = _text("journey\n  title 旅程\n  section 购物\n  选购: 5: 用户\n  付款: 3: 系统\n")
    assert "旅程" in text and "选购" in text and "付款" in text


def test_empty_and_unknown_fallback():
    assert "\U0001f4ca" in render_mermaid_block("")[0].plain
    text = _text("unknownDiagram\n  x --> y\n")
    assert "unknownDiagram" in text


def test_render_cache_reuses_result():
    clear_mermaid_cache()
    a = render_mermaid_block("graph TD; A-->B")
    b = render_mermaid_block("graph TD; A-->B")
    assert a is b
    clear_mermaid_cache()
    c = render_mermaid_block("graph TD; A-->B")
    assert c is not a
